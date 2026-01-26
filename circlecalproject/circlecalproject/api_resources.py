from __future__ import annotations

from django.utils.crypto import get_random_string
from django.utils.text import slugify

from accounts.models import Business, Membership
from bookings.models import FacilityResource, ServiceResource

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


def _require_resource_manager(membership: Membership):
    if membership.role not in {"owner", "admin", "manager"}:
        raise ValidationError({"detail": "You do not have permission to manage resources."})


def _require_team_resources(org: Business):
    try:
        from billing.utils import can_use_resources

        if not bool(can_use_resources(org)):
            raise ValidationError({"detail": "Resources are available on the Team plan only."})
    except ValidationError:
        raise
    except Exception:
        raise ValidationError({"detail": "Resources are available on the Team plan only."})


def _unique_resource_slug_for_org(org: Business, base_slug: str, exclude_id: int | None = None) -> str:
    base_slug = (base_slug or '').strip() or get_random_string(8)
    slug_candidate = base_slug
    counter = 1
    qs = FacilityResource.objects.filter(organization=org)
    if exclude_id is not None:
        qs = qs.exclude(id=exclude_id)
    while qs.filter(slug=slug_candidate).exists():
        slug_candidate = f"{base_slug}-{counter}"
        counter += 1
    return slug_candidate


def _serialize_resource(r: FacilityResource):
    try:
        in_use = ServiceResource.objects.filter(resource=r).exists()
    except Exception:
        in_use = False

    return {
        "id": r.id,
        "name": r.name,
        "slug": r.slug,
        "is_active": bool(getattr(r, "is_active", True)),
        "max_services": int(getattr(r, "max_services", 1) or 1),
        "in_use": bool(in_use),
    }


class ResourcesListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        org, membership = _get_org_and_membership(user=request.user, org_param=request.query_params.get("org"))
        _require_resource_manager(membership)
        _require_team_resources(org)

        qs = FacilityResource.objects.filter(organization=org).order_by("name", "id")
        items = [_serialize_resource(r) for r in qs]
        return Response(
            {
                "org": {"id": org.id, "slug": org.slug, "name": org.name},
                "count": len(items),
                "resources": items,
            }
        )

    def post(self, request):
        org, membership = _get_org_and_membership(user=request.user, org_param=request.query_params.get("org"))
        _require_resource_manager(membership)
        _require_team_resources(org)

        data = request.data or {}
        name = str(data.get("name") or "").strip()
        if not name:
            raise ValidationError({"name": "Name is required."})
        if len(name) > 120:
            raise ValidationError({"name": "Name is too long."})

        slug_input = str(data.get("slug") or "").strip()
        base_slug = slugify(slug_input or name) or get_random_string(8)
        slug_val = _unique_resource_slug_for_org(org, base_slug)

        is_active = bool(data.get("is_active", True))

        max_services_raw = data.get("max_services", 1)
        try:
            max_services = int(max_services_raw)
        except Exception:
            max_services = 1
        if max_services < 0:
            max_services = 1

        r = FacilityResource.objects.create(
            organization=org,
            name=name,
            slug=slug_val,
            is_active=is_active,
            max_services=max_services,
        )

        return Response({"resource": _serialize_resource(r)})


class ResourceDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, resource_id: int):
        org, membership = _get_org_and_membership(user=request.user, org_param=request.query_params.get("org"))
        _require_resource_manager(membership)
        _require_team_resources(org)

        r = FacilityResource.objects.filter(organization=org, id=int(resource_id)).first()
        if not r:
            raise ValidationError({"detail": "Resource not found."})

        data = request.data or {}

        if "name" in data:
            name = str(data.get("name") or "").strip()
            if not name:
                raise ValidationError({"name": "Name cannot be empty."})
            if len(name) > 120:
                raise ValidationError({"name": "Name is too long."})
            r.name = name

        if "slug" in data:
            slug_input = str(data.get("slug") or "").strip()
            if slug_input:
                base_slug = slugify(slug_input) or get_random_string(8)
                r.slug = _unique_resource_slug_for_org(org, base_slug, exclude_id=r.id)

        if "max_services" in data:
            try:
                ms = int(data.get("max_services"))
            except Exception:
                raise ValidationError({"max_services": "Invalid number."})
            if ms < 0:
                raise ValidationError({"max_services": "Must be >= 0."})
            r.max_services = ms

        if "is_active" in data:
            next_active = bool(data.get("is_active"))
            # Block deactivation when linked to services.
            if (not next_active) and bool(getattr(r, "is_active", True)):
                try:
                    in_use = ServiceResource.objects.filter(resource=r).exists()
                except Exception:
                    in_use = False
                if in_use:
                    raise ValidationError({"detail": "Resource is linked to a service. Unlink it before deactivating."})
            r.is_active = next_active

        r.save()
        return Response({"resource": _serialize_resource(r)})
