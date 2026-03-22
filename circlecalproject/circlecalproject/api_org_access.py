from __future__ import annotations

from types import SimpleNamespace

from accounts.models import Business, Membership

try:
    from rest_framework.exceptions import ValidationError
except Exception as exc:  # pragma: no cover
    raise RuntimeError(
        "Django REST Framework is required for API views. "
        "Install 'djangorestframework' and ensure 'rest_framework' is in INSTALLED_APPS."
    ) from exc


def resolve_org_and_membership(*, user, org_param: str | None):
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
    if membership:
        return org, membership

    if getattr(org, "owner_id", None) == getattr(user, "id", None):
        return org, SimpleNamespace(
            id=None,
            user=user,
            user_id=getattr(user, "id", None),
            organization=org,
            organization_id=getattr(org, "id", None),
            role="owner",
            is_active=True,
        )

    raise ValidationError({"detail": "You do not have access to this organization."})


def list_accessible_orgs(*, user):
    memberships = list(
        Membership.objects.filter(user=user, is_active=True)
        .select_related("organization")
        .order_by("organization__name")
    )

    orgs = []
    seen_ids = set()

    for membership in memberships:
        org = membership.organization
        seen_ids.add(org.id)
        orgs.append(
            {
                "id": org.id,
                "slug": org.slug,
                "name": org.name,
                "role": membership.role,
            }
        )

    owned_qs = Business.objects.filter(owner=user).order_by("name")
    if seen_ids:
        owned_qs = owned_qs.exclude(id__in=seen_ids)

    for org in owned_qs:
        orgs.append(
            {
                "id": org.id,
                "slug": org.slug,
                "name": org.name,
                "role": "owner",
            }
        )

    return orgs