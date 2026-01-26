from __future__ import annotations

from django.conf import settings
from django.urls import reverse
from django.utils.crypto import get_random_string

from accounts.models import Business, Invite, Membership

try:
    from rest_framework.exceptions import ValidationError
    from rest_framework.permissions import IsAuthenticated
    from rest_framework.response import Response
    from rest_framework.views import APIView
except Exception as exc:  # pragma: no cover
    raise RuntimeError(
        "Django REST Framework is required for API views. "
        "Install 'djangorestframework' and ensure 'rest_framework' is in INSTALLED_APPS."
    ) from exc


def _get_org_and_membership(*, user, org_param: str | None):
    if not org_param:
        raise ValidationError({"org": "This query param is required (org slug or id)."})

    org: Business | None
    if str(org_param).isdigit():
        org = Business.objects.filter(id=int(org_param)).first()
    else:
        org = Business.objects.filter(slug=str(org_param)).first()

    if not org:
        raise ValidationError({"org": "Unknown organization."})

    membership = Membership.objects.filter(user=user, organization=org, is_active=True).first()
    if not membership:
        raise ValidationError({"detail": "You do not have access to this organization."})

    return org, membership


def _require_team_admin(membership: Membership):
    if membership.role not in {"owner", "admin"}:
        raise ValidationError({"detail": "Only owners/admins can manage staff."})


def _require_team_plan(org: Business):
    try:
        from billing.utils import can_add_staff

        if not bool(can_add_staff(org)):
            raise ValidationError({"detail": "Staff management is available on the Team plan only."})
    except ValidationError:
        raise
    except Exception:
        raise ValidationError({"detail": "Staff management is available on the Team plan only."})


def _serialize_member(m: Membership):
    u = getattr(m, "user", None)
    return {
        "id": m.id,
        "role": m.role,
        "is_active": bool(m.is_active),
        "created_at": (m.created_at.isoformat() if getattr(m, "created_at", None) else None),
        "user": {
            "id": getattr(u, "id", None),
            "username": getattr(u, "username", "") or "",
            "email": getattr(u, "email", "") or "",
            "first_name": getattr(u, "first_name", "") or "",
            "last_name": getattr(u, "last_name", "") or "",
        },
    }


def _serialize_invite(inv: Invite, *, request=None):
    accept_url = None
    if request is not None:
        try:
            accept_path = reverse("calendar_app:accept_invite", kwargs={"token": inv.token})
            accept_url = request.build_absolute_uri(accept_path)
        except Exception:
            accept_url = None

    return {
        "id": inv.id,
        "email": inv.email,
        "role": inv.role,
        "accepted": bool(inv.accepted),
        "created_at": (inv.created_at.isoformat() if getattr(inv, "created_at", None) else None),
        "accept_url": accept_url,
    }


class TeamMembersView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        org, membership = _get_org_and_membership(user=request.user, org_param=request.query_params.get("org"))
        _require_team_admin(membership)
        _require_team_plan(org)

        members = (
            Membership.objects.filter(organization=org, is_active=True)
            .select_related("user")
            .order_by("user__email", "id")
        )

        items = [_serialize_member(m) for m in members]
        return Response(
            {
                "org": {"id": org.id, "slug": org.slug, "name": org.name},
                "count": len(items),
                "members": items,
            }
        )


class TeamMemberDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, member_id: int):
        org, membership = _get_org_and_membership(user=request.user, org_param=request.query_params.get("org"))
        _require_team_admin(membership)
        _require_team_plan(org)

        target = Membership.objects.filter(organization=org, id=int(member_id)).select_related("user").first()
        if not target:
            raise ValidationError({"detail": "Member not found."})

        data = request.data or {}

        # role updates (optional)
        if "role" in data:
            role = str(data.get("role") or "").strip()
            allowed_roles = {"owner", "admin", "manager", "staff"}
            if role not in allowed_roles:
                raise ValidationError({"role": "Invalid role."})
            # Do not allow changing ownership via API.
            if target.role == "owner" and role != "owner":
                raise ValidationError({"detail": "Cannot change owner role."})
            if target.role != "owner" and role == "owner":
                raise ValidationError({"detail": "Cannot promote to owner."})
            target.role = role

        # deactivate/reactivate
        if "is_active" in data:
            is_active = bool(data.get("is_active"))
            if target.role == "owner" and not is_active:
                raise ValidationError({"detail": "Cannot deactivate owner."})
            if target.user_id == membership.user_id and not is_active:
                raise ValidationError({"detail": "You cannot deactivate yourself."})
            target.is_active = is_active

        target.save()
        return Response({"member": _serialize_member(target)})


class TeamInvitesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        org, membership = _get_org_and_membership(user=request.user, org_param=request.query_params.get("org"))
        _require_team_admin(membership)
        _require_team_plan(org)

        invites = Invite.objects.filter(organization=org, accepted=False).order_by("-created_at")
        items = [_serialize_invite(inv, request=request) for inv in invites]
        return Response(
            {
                "org": {"id": org.id, "slug": org.slug, "name": org.name},
                "count": len(items),
                "invites": items,
            }
        )

    def post(self, request):
        org, membership = _get_org_and_membership(user=request.user, org_param=request.query_params.get("org"))
        _require_team_admin(membership)
        _require_team_plan(org)

        data = request.data or {}
        email = (str(data.get("email") or "").strip() or "").lower()
        if not email or "@" not in email:
            raise ValidationError({"email": "Valid email is required."})

        role = str(data.get("role") or "staff").strip()
        if role not in {"admin", "manager", "staff"}:
            raise ValidationError({"role": "Role must be admin, manager, or staff."})

        # Avoid spamming duplicates; if one exists, return it.
        existing = Invite.objects.filter(organization=org, email__iexact=email, accepted=False).first()
        if existing:
            return Response({"invite": _serialize_invite(existing, request=request), "sent": False})

        token = get_random_string(48)
        inv = Invite.objects.create(organization=org, email=email, role=role, token=token)

        sent = False
        error = None

        # Attempt to send email (best effort).
        try:
            from django.core.mail import EmailMultiAlternatives
            from django.template.loader import render_to_string

            accept_path = reverse("calendar_app:accept_invite", kwargs={"token": token})
            accept_url = request.build_absolute_uri(accept_path)

            context = {
                "org": org,
                "email": email,
                "role": role,
                "accept_url": accept_url,
                "site_url": getattr(settings, "SITE_URL", request.build_absolute_uri("/")),
                "recipient_name": "",
            }

            subject = f"{org.name} invited you to join"
            text_content = render_to_string("calendar_app/emails/invite_email.txt", context)
            html_content = render_to_string("calendar_app/emails/invite_email.html", context)

            msg = EmailMultiAlternatives(subject, text_content, settings.DEFAULT_FROM_EMAIL, [email])
            msg.attach_alternative(html_content, "text/html")
            msg.send()
            sent = True
        except Exception as e:
            sent = False
            error = str(e)

        payload = {"invite": _serialize_invite(inv, request=request), "sent": sent}
        if error:
            payload["send_error"] = error
        return Response(payload)
