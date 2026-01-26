from __future__ import annotations

from accounts.models import Business, Membership

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


class OrgOfflinePaymentsView(APIView):
    """Read/update org-level offline payment identifiers (Venmo/Zelle).

    Web profile exposes this owner-only and Pro/Team-only.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        org, membership = _get_org_and_membership(user=request.user, org_param=request.query_params.get("org"))

        can_use_offline_payment_methods = False
        try:
            from billing.utils import can_use_offline_payment_methods as _can_use

            can_use_offline_payment_methods = bool(_can_use(org))
        except Exception:
            can_use_offline_payment_methods = False

        offline_venmo = ""
        offline_zelle = ""
        if membership.role == "owner" and can_use_offline_payment_methods:
            try:
                from bookings.models import OrgSettings

                settings_obj, _ = OrgSettings.objects.get_or_create(organization=org)
                offline_venmo = (getattr(settings_obj, "offline_venmo", "") or "").strip()
                offline_zelle = (getattr(settings_obj, "offline_zelle", "") or "").strip()
            except Exception:
                offline_venmo = ""
                offline_zelle = ""

        return Response(
            {
                "org": {"id": org.id, "slug": org.slug, "name": org.name},
                "can_use_offline_payment_methods": can_use_offline_payment_methods,
                "can_edit": bool(membership.role == "owner" and can_use_offline_payment_methods),
                "offline_venmo": offline_venmo,
                "offline_zelle": offline_zelle,
            }
        )

    def patch(self, request):
        org, membership = _get_org_and_membership(user=request.user, org_param=request.query_params.get("org"))

        if membership.role != "owner":
            raise ValidationError({"detail": "Only the owner can edit offline payment settings."})

        try:
            from billing.utils import can_use_offline_payment_methods as _can_use

            if not bool(_can_use(org)):
                raise ValidationError({"detail": "Offline payment methods require Pro/Team."})
        except ValidationError:
            raise
        except Exception:
            raise ValidationError({"detail": "Offline payment methods are not available."})

        from bookings.models import OrgSettings

        settings_obj, _ = OrgSettings.objects.get_or_create(organization=org)
        data = request.data or {}

        if "offline_venmo" in data:
            settings_obj.offline_venmo = ("" if data.get("offline_venmo") is None else str(data.get("offline_venmo"))).strip()
        if "offline_zelle" in data:
            settings_obj.offline_zelle = ("" if data.get("offline_zelle") is None else str(data.get("offline_zelle"))).strip()

        settings_obj.save(update_fields=["offline_venmo", "offline_zelle"])
        return self.get(request)
