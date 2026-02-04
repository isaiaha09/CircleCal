from __future__ import annotations

from django.utils import timezone

from accounts.push import push_enabled

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

from accounts.models import PushDevice


class PushStatusView(APIView):
    """Return push registration + server gating status for the authenticated user.

    This is intentionally lightweight and safe to call from the mobile app.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = PushDevice.objects.filter(user=request.user)
        total = qs.count()
        active = qs.filter(is_active=True).count()
        last_seen = None
        try:
            last_seen = qs.order_by('-last_seen_at').values_list('last_seen_at', flat=True).first()
        except Exception:
            last_seen = None

        return Response(
            {
                'ok': True,
                'push_enabled': bool(push_enabled()),
                'devices_total': int(total),
                'devices_active': int(active),
                'last_seen_at': last_seen,
                'server_time': timezone.now(),
            }
        )


class PushTokensView(APIView):
    """Register/unregister Expo push tokens for the authenticated user."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        token = None
        platform = ''
        try:
            token = (request.data or {}).get('token')
            platform = (request.data or {}).get('platform') or ''
        except Exception:
            token = None
            platform = ''

        try:
            token = (str(token).strip() if token is not None else '')
        except Exception:
            token = ''

        if not token:
            raise ValidationError({'token': 'This field is required.'})

        try:
            platform = str(platform).strip().lower()
        except Exception:
            platform = ''

        dev, _created = PushDevice.objects.update_or_create(
            token=token,
            defaults={
                'user': request.user,
                'platform': platform,
                'is_active': True,
                'last_seen_at': timezone.now(),
            },
        )

        return Response({'ok': True, 'device_id': dev.id})

    def delete(self, request):
        token = None
        try:
            token = (request.data or {}).get('token')
        except Exception:
            token = None

        try:
            token = (str(token).strip() if token is not None else '')
        except Exception:
            token = ''

        if not token:
            raise ValidationError({'token': 'This field is required.'})

        # Only allow deleting your own device token.
        deleted, _ = PushDevice.objects.filter(user=request.user, token=token).delete()
        return Response({'ok': True, 'deleted': deleted})
