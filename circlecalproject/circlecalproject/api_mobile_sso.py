from __future__ import annotations

from urllib.parse import urlencode

import secrets
from datetime import timedelta

from django.core import signing
from django.urls import reverse
from django.utils import timezone

from accounts.models import MobileSSOToken

try:
    from rest_framework.permissions import IsAuthenticated
    from rest_framework.response import Response
    from rest_framework.views import APIView
except Exception as exc:  # pragma: no cover
    raise RuntimeError(
        "Django REST Framework is required for API views. "
        "Install 'djangorestframework' and ensure 'rest_framework' is in INSTALLED_APPS."
    ) from exc


class MobileSSOLinkView(APIView):
    """Issues a one-time SSO link for the mobile WebView.

    Auth: JWT (preferred) or session.

    The returned URL points at a normal Django view that establishes a session
    cookie, then redirects to `next`.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        next_path = (request.data or {}).get("next") or "/"
        return self._issue(request, next_path)

    def get(self, request):
        next_path = request.query_params.get("next") or "/"
        return self._issue(request, next_path)

    def _issue(self, request, next_path: str):
        # Stripe onboarding can easily take longer than 5 minutes on mobile.
        # If the one-time token expires too quickly, users see a 400 "invalid or expired".
        # Use a longer TTL for onboarding routes, keep a shorter TTL otherwise.
        ttl_seconds = 300
        try:
            np = (next_path or '').lower()
            if ('post-login' in np) or ('stripe' in np) or ('/accounts/profile' in np) or ('/profile' in np):
                ttl_seconds = 60 * 60  # 1 hour
        except Exception:
            ttl_seconds = 300

        def _build_signed_token_url() -> tuple[str, int]:
            # Fallback when DB-backed token creation fails (e.g., missing migration or DB outage).
            now = timezone.now()
            exp = int((now + timedelta(seconds=int(ttl_seconds))).timestamp())
            payload = {
                "uid": int(getattr(request.user, "id", 0) or 0),
                "exp": exp,
                "n": secrets.token_urlsafe(16),
            }
            signed = signing.dumps(payload, salt="cc_mobile_sso", compress=True)
            consume_path = reverse(
                "accounts:mobile_sso_consume",
                kwargs={"token": f"sig_{signed}"},
            )
            url = request.build_absolute_uri(consume_path)
            if next_path:
                url = f"{url}?{urlencode({'next': next_path})}"
            expires_in = max(0, exp - int(now.timestamp()))
            return url, expires_in

        try:
            token_obj = MobileSSOToken.create_for_user(request.user, ttl_seconds=ttl_seconds)
            consume_path = reverse(
                "accounts:mobile_sso_consume",
                kwargs={"token": token_obj.token},
            )
            url = request.build_absolute_uri(consume_path)
            if next_path:
                url = f"{url}?{urlencode({'next': next_path})}"
            expires_in = max(0, int((token_obj.expires_at - timezone.now()).total_seconds()))
            return Response({"url": url, "expires_in": expires_in})
        except Exception:
            url, expires_in = _build_signed_token_url()
            return Response({"url": url, "expires_in": expires_in})
