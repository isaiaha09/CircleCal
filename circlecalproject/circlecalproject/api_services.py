from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.utils import timezone
from django.utils.crypto import get_random_string
from django.utils.text import slugify

from accounts.models import Business, Membership
from bookings.models import Service

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


def _require_service_manager(membership: Membership):
    if membership.role not in {"owner", "admin", "manager"}:
        raise ValidationError({"detail": "You do not have permission to manage services."})


def _serialize_service(svc: Service):
    return {
        "id": svc.id,
        "name": svc.name,
        "slug": svc.slug,
        "description": svc.description,
        "duration": int(svc.duration),
        "price": svc.price,
        "is_active": bool(svc.is_active),
        "show_on_public_calendar": bool(getattr(svc, "show_on_public_calendar", True)),
    }


def _coerce_decimal(val, *, field: str) -> Decimal:
    try:
        if val is None or val == "":
            return Decimal("0")
        return Decimal(str(val))
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError({field: "Invalid number."})


class ServicesListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        org, _membership = _get_org_and_membership(user=request.user, org_param=request.query_params.get("org"))

        qs = Service.objects.filter(organization=org).order_by("name")
        items = [_serialize_service(s) for s in qs]
        return Response(
            {
                "org": {"id": org.id, "slug": org.slug, "name": org.name},
                "count": len(items),
                "services": items,
            }
        )

    def post(self, request):
        org, membership = _get_org_and_membership(user=request.user, org_param=request.query_params.get("org"))
        _require_service_manager(membership)

        data = request.data or {}

        name_raw = data.get("name")
        name = (str(name_raw).strip() if name_raw is not None else "")
        if not name:
            raise ValidationError({"name": "Name is required."})
        if len(name) > 120:
            raise ValidationError({"name": "Name is too long."})

        duration_raw = data.get("duration")
        try:
            duration = int(duration_raw)
        except Exception:
            raise ValidationError({"duration": "Duration is required (minutes)."})
        if duration <= 0 or duration > 24 * 60:
            raise ValidationError({"duration": "Duration must be between 1 and 1440 minutes."})

        price = _coerce_decimal(data.get("price"), field="price")
        if price < 0:
            raise ValidationError({"price": "Price cannot be negative."})

        description = str(data.get("description") or "").strip()

        base_slug = slugify(data.get("slug") or name) or get_random_string(8)
        slug = base_slug
        i = 1
        while Service.objects.filter(slug=slug).exists():
            i += 1
            slug = f"{base_slug}-{i}"

        svc = Service.objects.create(
            organization=org,
            name=name,
            slug=slug,
            description=description,
            duration=duration,
            price=price,
            is_active=bool(data.get("is_active", True)),
            show_on_public_calendar=bool(data.get("show_on_public_calendar", True)),
            signature_updated_at=timezone.now(),
        )

        return Response(
            {
                "org": {"id": org.id, "slug": org.slug, "name": org.name},
                "service": _serialize_service(svc),
            }
        )


class ServiceDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, service_id: int):
        org, _membership = _get_org_and_membership(user=request.user, org_param=request.query_params.get("org"))

        svc = Service.objects.filter(organization=org, id=int(service_id)).first()
        if not svc:
            raise ValidationError({"detail": "Service not found."})

        return Response({"org": {"id": org.id, "slug": org.slug, "name": org.name}, "service": _serialize_service(svc)})

    def patch(self, request, service_id: int):
        org, membership = _get_org_and_membership(user=request.user, org_param=request.query_params.get("org"))
        _require_service_manager(membership)

        svc = Service.objects.filter(organization=org, id=int(service_id)).first()
        if not svc:
            raise ValidationError({"detail": "Service not found."})

        data = request.data or {}
        signature_changed = False

        if "name" in data:
            name = str(data.get("name") or "").strip()
            if not name:
                raise ValidationError({"name": "Name cannot be empty."})
            if len(name) > 120:
                raise ValidationError({"name": "Name is too long."})
            svc.name = name

        if "description" in data:
            svc.description = str(data.get("description") or "")

        if "duration" in data:
            try:
                duration = int(data.get("duration"))
            except Exception:
                raise ValidationError({"duration": "Invalid duration."})
            if duration <= 0 or duration > 24 * 60:
                raise ValidationError({"duration": "Duration must be between 1 and 1440 minutes."})
            if duration != svc.duration:
                signature_changed = True
            svc.duration = duration

        if "price" in data:
            price = _coerce_decimal(data.get("price"), field="price")
            if price < 0:
                raise ValidationError({"price": "Price cannot be negative."})
            svc.price = price

        if "is_active" in data:
            svc.is_active = bool(data.get("is_active"))

        if "show_on_public_calendar" in data:
            svc.show_on_public_calendar = bool(data.get("show_on_public_calendar"))

        if signature_changed:
            svc.signature_updated_at = timezone.now()

        svc.save()
        return Response({"org": {"id": org.id, "slug": org.slug, "name": org.name}, "service": _serialize_service(svc)})
