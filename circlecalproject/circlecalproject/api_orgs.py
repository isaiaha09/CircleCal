from __future__ import annotations

from accounts.models import Business, Membership

try:
    from rest_framework.permissions import IsAuthenticated
    from rest_framework.response import Response
    from rest_framework.views import APIView
except Exception as exc:  # pragma: no cover
    raise RuntimeError(
        "Django REST Framework is required for API views. "
        "Install 'djangorestframework' and ensure 'rest_framework' is in INSTALLED_APPS."
    ) from exc


class OrgsListView(APIView):
    """List organizations the authenticated user belongs to."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        memberships = (
            Membership.objects.filter(user=request.user, is_active=True)
            .select_related("organization")
            .order_by("organization__name")
        )

        orgs = []
        for m in memberships:
            org: Business = m.organization
            orgs.append(
                {
                    "id": org.id,
                    "slug": org.slug,
                    "name": org.name,
                    "role": m.role,
                }
            )

        return Response({"orgs": orgs})
