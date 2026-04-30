from __future__ import annotations

from typing import Any

try:
    from rest_framework.settings import api_settings
    from rest_framework.throttling import SimpleRateThrottle
except Exception as exc:  # pragma: no cover
    raise RuntimeError(
        "Django REST Framework is required for API throttles. "
        "Install 'djangorestframework' and ensure 'rest_framework' is in INSTALLED_APPS."
    ) from exc


class DynamicScopeRateThrottle(SimpleRateThrottle):
    scope = ""

    def get_cache_key(self, request, view):
        if not self.scope:
            return None

        identity = self.get_identity_fragment(request)
        if not identity:
            return None
        return self.cache_format % {"scope": self.scope, "ident": identity}

    def get_rate(self):
        if not self.scope:
            return None
        return (api_settings.DEFAULT_THROTTLE_RATES or {}).get(self.scope)

    def get_identity_fragment(self, request) -> str | None:
        user = getattr(request, "user", None)
        try:
            if user is not None and getattr(user, "is_authenticated", False):
                return f"user:{int(getattr(user, 'id', 0) or 0)}"
        except Exception:
            pass

        ident = self.get_ident(request)
        if ident:
            return f"ip:{ident}"
        return None


class MobileAuthBurstThrottle(DynamicScopeRateThrottle):
    scope = "mobile_auth_burst"

    def get_identity_fragment(self, request) -> str | None:
        base = super().get_identity_fragment(request) or "unknown"
        try:
            username = str((getattr(request, "data", {}) or {}).get("username") or "").strip().lower()
        except Exception:
            username = ""
        if username:
            return f"{base}:username:{username}"
        return base


class MobileAuthSustainedThrottle(MobileAuthBurstThrottle):
    scope = "mobile_auth_sustained"


class MobileSessionThrottle(DynamicScopeRateThrottle):
    scope = "mobile_session_rotate"


class PushStatusThrottle(DynamicScopeRateThrottle):
    scope = "push_status"


class PushWriteThrottle(DynamicScopeRateThrottle):
    scope = "push_write"


class BillingReadThrottle(DynamicScopeRateThrottle):
    scope = "billing_read"


class BillingWriteBurstThrottle(DynamicScopeRateThrottle):
    scope = "billing_write_burst"


class BillingWriteSustainedThrottle(DynamicScopeRateThrottle):
    scope = "billing_write_sustained"