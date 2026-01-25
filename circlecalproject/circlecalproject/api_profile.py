from __future__ import annotations

from zoneinfo import ZoneInfo

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

from accounts.models import Profile


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
        u = request.user
        profile = _get_or_create_profile(u)

        return Response(
            {
                "user": {
                    "id": u.id,
                    "username": u.get_username(),
                    "email": getattr(u, "email", ""),
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
                },
            }
        )

    def patch(self, request):
        profile = _get_or_create_profile(request.user)
        data = request.data or {}

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

        profile.save()
        return self.get(request)


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
