from __future__ import annotations

from zoneinfo import ZoneInfo

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

try:
    from rest_framework.exceptions import ValidationError
    from rest_framework.parsers import MultiPartParser
    from rest_framework.permissions import IsAuthenticated
    from rest_framework.response import Response
    from rest_framework.views import APIView
except Exception as exc:  # pragma: no cover
    raise RuntimeError(
        "Django REST Framework is required for API views. "
        "Install 'djangorestframework' and ensure 'rest_framework' is in INSTALLED_APPS."
    ) from exc

from accounts.models import Business, Invite, LoginActivity, Membership, Profile


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


def _stripe_connected_account_url(org: Business) -> str | None:
    try:
        acct_id = getattr(org, "stripe_connect_account_id", None)
        if not acct_id:
            return None
        secret = str(getattr(settings, "STRIPE_SECRET_KEY", "") or "")
        is_test = secret.startswith("sk_test")
        base = "https://dashboard.stripe.com/test/connect/accounts/" if is_test else "https://dashboard.stripe.com/connect/accounts/"
        return base + str(acct_id)
    except Exception:
        return None


def _build_accept_invite_url(request, token: str) -> str | None:
    try:
        accept_path = reverse("calendar_app:accept_invite", kwargs={"token": token})
        return request.build_absolute_uri(accept_path)
    except Exception:
        return None


def _profile_response(request, *, include_org_overview: bool = False):
    u = request.user
    profile = _get_or_create_profile(u)

    activities = list(
        LoginActivity.objects.filter(user=u)
        .only("timestamp", "ip_address", "user_agent")
        .order_by("-timestamp")[:5]
    )

    memberships = list(
        Membership.objects.filter(user=u, is_active=True)
        .select_related("organization")
        .order_by("organization__name")
    )

    pending_invites = []
    try:
        if getattr(u, "email", None):
            pending_invites = list(
                Invite.objects.filter(email=u.email, accepted=False)
                .select_related("organization")
                .order_by("-created_at")
            )
    except Exception:
        pending_invites = []

    payload: dict = {
        "user": {
            "id": u.id,
            "username": u.get_username(),
            "email": getattr(u, "email", ""),
            "first_name": getattr(u, "first_name", ""),
            "last_name": getattr(u, "last_name", ""),
        },
        "profile": {
            "display_name": getattr(profile, "display_name", None),
            "timezone": getattr(profile, "timezone", "UTC"),
            "email_alerts": bool(getattr(profile, "email_alerts", True)),
            "booking_reminders": bool(getattr(profile, "booking_reminders", True)),
            "avatar_url": _avatar_url(request, profile),
            "avatar_updated_at": (
                profile.avatar_updated_at.isoformat() if getattr(profile, "avatar_updated_at", None) else None
            ),
            "scheduled_account_deletion_at": (
                profile.scheduled_account_deletion_at.isoformat()
                if getattr(profile, "scheduled_account_deletion_at", None)
                else None
            ),
            "scheduled_account_deletion_reason": getattr(profile, "scheduled_account_deletion_reason", None),
        },
        "recent_logins": [
            {
                "timestamp": a.timestamp.isoformat() if getattr(a, "timestamp", None) else None,
                "ip_address": getattr(a, "ip_address", None),
                "user_agent": (getattr(a, "user_agent", "") or "")[:500],
            }
            for a in activities
        ],
        "memberships": [
            {
                "org": {
                    "id": m.organization_id,
                    "slug": m.organization.slug,
                    "name": m.organization.name,
                },
                "role": m.role,
                "is_active": bool(m.is_active),
            }
            for m in memberships
        ],
        "pending_invites": [
            {
                "org": {
                    "id": inv.organization_id,
                    "slug": inv.organization.slug,
                    "name": inv.organization.name,
                },
                "role": inv.role,
                "created_at": inv.created_at.isoformat() if getattr(inv, "created_at", None) else None,
                "accept_url": _build_accept_invite_url(request, inv.token),
            }
            for inv in pending_invites
        ],
    }

    if include_org_overview and request.query_params.get("org"):
        org_param = request.query_params.get("org")
        org, membership = _get_org_and_membership(user=u, org_param=org_param)

        can_use_offline_payment_methods = False
        try:
            from billing.utils import can_use_offline_payment_methods as _can_use_offline_payment_methods

            can_use_offline_payment_methods = bool(_can_use_offline_payment_methods(org))
        except Exception:
            can_use_offline_payment_methods = False

        org_offline_venmo = ""
        org_offline_zelle = ""
        if membership.role == "owner" and can_use_offline_payment_methods:
            try:
                from bookings.models import OrgSettings

                settings_obj, _ = OrgSettings.objects.get_or_create(organization=org)
                org_offline_venmo = (getattr(settings_obj, "offline_venmo", "") or "").strip()
                org_offline_zelle = (getattr(settings_obj, "offline_zelle", "") or "").strip()
            except Exception:
                org_offline_venmo = ""
                org_offline_zelle = ""

        payload["org_overview"] = {
            "org": {"id": org.id, "slug": org.slug, "name": org.name},
            "membership": {"role": membership.role},
            "features": {
                "can_use_offline_payment_methods": can_use_offline_payment_methods,
            },
            "offline_payment": {
                "can_edit": bool(membership.role == "owner" and can_use_offline_payment_methods),
                "offline_venmo": org_offline_venmo,
                "offline_zelle": org_offline_zelle,
            },
            "stripe": {
                "connect_account_id": bool(getattr(org, "stripe_connect_account_id", None)),
                "connect_details_submitted": bool(getattr(org, "stripe_connect_details_submitted", False)),
                "connect_charges_enabled": bool(getattr(org, "stripe_connect_charges_enabled", False)),
                "connect_payouts_enabled": bool(getattr(org, "stripe_connect_payouts_enabled", False)),
                "connected_account_url": _stripe_connected_account_url(org),
            },
        }

    return payload


def _get_or_create_profile(user):
    profile, _ = Profile.objects.get_or_create(user=user)
    return profile


def _avatar_url(request, profile: Profile) -> str | None:
    try:
        f = getattr(profile, "avatar", None)
        if not f:
            return None
        url = f.url
        try:
            return request.build_absolute_uri(url)
        except Exception:
            return url
    except Exception:
        return None


class ProfileView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(_profile_response(request, include_org_overview=False))

    def patch(self, request):
        profile = _get_or_create_profile(request.user)
        u = request.user
        data = request.data or {}

        if "username" in data:
            try:
                v = data.get("username")
                if v is not None:
                    u.username = str(v).strip()[:150]
            except Exception:
                raise ValidationError({"username": "Invalid value."})

        if "email" in data:
            try:
                v = data.get("email")
                if v is None:
                    u.email = ""
                else:
                    u.email = str(v).strip()[:254]
            except Exception:
                raise ValidationError({"email": "Invalid value."})

        if "first_name" in data:
            try:
                v = data.get("first_name")
                u.first_name = ("" if v is None else str(v)).strip()[:150]
            except Exception:
                raise ValidationError({"first_name": "Invalid value."})

        if "last_name" in data:
            try:
                v = data.get("last_name")
                u.last_name = ("" if v is None else str(v)).strip()[:150]
            except Exception:
                raise ValidationError({"last_name": "Invalid value."})

        if "display_name" in data:
            try:
                display_name = data.get("display_name")
                if display_name is None:
                    profile.display_name = None
                else:
                    profile.display_name = str(display_name).strip()[:255] or None
            except Exception:
                raise ValidationError({"display_name": "Invalid value."})

        if "timezone" in data:
            tz = data.get("timezone")
            try:
                tz_str = str(tz).strip()
                ZoneInfo(tz_str)  # validate
                profile.timezone = tz_str
            except Exception:
                raise ValidationError({"timezone": "Invalid timezone (e.g., America/Los_Angeles)."})

        if "email_alerts" in data:
            profile.email_alerts = bool(data.get("email_alerts"))

        if "booking_reminders" in data:
            profile.booking_reminders = bool(data.get("booking_reminders"))

        try:
            u.save()
        except Exception:
            # Preserve useful validation messages where possible (e.g., username uniqueness)
            raise ValidationError({"detail": "Could not update user fields (possibly not unique)."})

        profile.save()
        return self.get(request)


class ProfileOverviewView(APIView):
    """Single-call profile payload for mobile (optionally org-scoped via ?org=)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(_profile_response(request, include_org_overview=True))


class ProfileAvatarUploadView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser]

    def post(self, request):
        profile = _get_or_create_profile(request.user)
        f = request.FILES.get("avatar")
        if not f:
            raise ValidationError({"avatar": "File required (form field name: avatar)."})

        profile.avatar = f
        profile.avatar_updated_at = timezone.now()
        profile.save()
        return Response(
            {
                "avatar_url": _avatar_url(request, profile),
                "avatar_updated_at": profile.avatar_updated_at.isoformat() if profile.avatar_updated_at else None,
            }
        )
