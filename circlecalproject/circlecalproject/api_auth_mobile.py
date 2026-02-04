from __future__ import annotations

try:
    from rest_framework.permissions import AllowAny
    from rest_framework.response import Response
    from rest_framework.views import APIView
except Exception as exc:  # pragma: no cover
    raise RuntimeError(
        "Django REST Framework is required for API views. "
        "Install 'djangorestframework' and ensure 'rest_framework' is in INSTALLED_APPS."
    ) from exc

from django.contrib.auth import authenticate

try:
    from django_otp import devices_for_user
except Exception:  # pragma: no cover
    devices_for_user = None  # type: ignore[assignment]

try:
    from rest_framework_simplejwt.tokens import RefreshToken
except Exception:  # pragma: no cover
    RefreshToken = None  # type: ignore[assignment]


class MobileTokenView(APIView):
    """JWT token obtain endpoint with optional 2FA enforcement.

    Flow:
    - Mobile submits username/password.
    - If the user has confirmed OTP devices, require `otp`.
    - Only then issue access/refresh tokens.

    This keeps 2FA entirely in the native login UX (not inside the WebView).
    """

    permission_classes = [AllowAny]
    authentication_classes: list[type] = []

    def post(self, request):
        username = str((request.data or {}).get("username") or "").strip()
        password = str((request.data or {}).get("password") or "")
        otp = str((request.data or {}).get("otp") or "").strip().replace(" ", "")

        if not username or not password:
            return Response({"detail": "Missing username or password."}, status=400)

        user = authenticate(request, username=username, password=password)
        if user is None or not getattr(user, "is_active", True):
            return Response({"detail": "Invalid credentials."}, status=401)

        has_otp = False
        if devices_for_user is not None:
            try:
                has_otp = any(True for _d in devices_for_user(user, confirmed=True))
            except Exception:
                has_otp = False

        if has_otp:
            if not otp:
                return Response({"detail": "otp_required", "otp_required": True}, status=400)

            verified = False
            try:
                for d in devices_for_user(user, confirmed=True):
                    try:
                        if d.verify_token(otp):
                            verified = True
                            break
                    except Exception:
                        continue
            except Exception:
                verified = False

            if not verified:
                return Response({"detail": "Invalid code.", "otp_required": True}, status=401)

        if RefreshToken is None:
            return Response({"detail": "JWT auth is not available on this server."}, status=500)

        refresh = RefreshToken.for_user(user)
        return Response(
            {
                "refresh": str(refresh),
                "access": str(refresh.access_token),
                "otp_required": False,
            }
        )
