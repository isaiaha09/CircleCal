from __future__ import annotations

from django.urls import path

from .api_views import HealthView, HelloView, MeView

urlpatterns = [
    path("health/", HealthView.as_view(), name="api_health"),
    path("hello/", HelloView.as_view(), name="api_hello"),
    path("me/", MeView.as_view(), name="api_me"),
]

# JWT endpoints (optional): only register if SimpleJWT is installed.
try:
    from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

    urlpatterns += [
        path("auth/token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
        path("auth/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    ]
except Exception:
    # SimpleJWT not installed; keep API functional without auth endpoints.
    pass
