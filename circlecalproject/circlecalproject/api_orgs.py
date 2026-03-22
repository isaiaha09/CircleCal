from __future__ import annotations

from .api_org_access import list_accessible_orgs

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
        return Response({"orgs": list_accessible_orgs(user=request.user)})
