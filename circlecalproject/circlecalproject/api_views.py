from __future__ import annotations

from django.conf import settings

try:
    from rest_framework.permissions import AllowAny, IsAuthenticated
    from rest_framework.response import Response
    from rest_framework.views import APIView
except Exception as exc:  # pragma: no cover
    # If DRF isn't installed, importing this module shouldn't break the whole site.
    raise RuntimeError(
        "Django REST Framework is required for API views. "
        "Install 'djangorestframework' and ensure 'rest_framework' is in INSTALLED_APPS."
    ) from exc


class HealthView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"status": "ok"})


class HelloView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"message": "Hello from CircleCal API"})


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        u = request.user
        return Response(
            {
                "id": u.id,
                "username": u.get_username(),
                "email": getattr(u, "email", ""),
                "is_staff": bool(getattr(u, "is_staff", False)),
                "is_superuser": bool(getattr(u, "is_superuser", False)),
                "debug": bool(getattr(settings, "DEBUG", False)),
            }
        )
