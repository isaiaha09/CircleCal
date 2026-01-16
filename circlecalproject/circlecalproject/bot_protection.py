import json
import urllib.parse
import urllib.request

from django.conf import settings
from django.core.cache import cache


TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def turnstile_is_enabled() -> bool:
    try:
        if not bool(getattr(settings, 'TURNSTILE_ENABLED', True)):
            return False
        return bool((getattr(settings, 'TURNSTILE_SITE_KEY', '') or '').strip()) and bool(
            (getattr(settings, 'TURNSTILE_SECRET_KEY', '') or '').strip()
        )
    except Exception:
        return False


def get_turnstile_site_key() -> str:
    try:
        return (getattr(settings, 'TURNSTILE_SITE_KEY', '') or '').strip()
    except Exception:
        return ''


def _get_client_ip(request) -> str:
    try:
        ip = (request.META.get('REMOTE_ADDR') or '').strip()
    except Exception:
        ip = ''
    return ip


def verify_turnstile(request) -> tuple[bool, str | None]:
    """Verify Cloudflare Turnstile response.

    Returns (ok, error_message).
    """
    if not turnstile_is_enabled():
        return True, None

    token = (request.POST.get('cf-turnstile-response') or request.POST.get('turnstile_token') or '').strip()
    if not token:
        return False, 'Please complete the security check.'

    secret = (getattr(settings, 'TURNSTILE_SECRET_KEY', '') or '').strip()
    if not secret:
        return False, 'Security check is not configured.'

    payload = {
        'secret': secret,
        'response': token,
    }
    ip = _get_client_ip(request)
    if ip:
        payload['remoteip'] = ip

    try:
        data = urllib.parse.urlencode(payload).encode('utf-8')
        req = urllib.request.Request(
            TURNSTILE_VERIFY_URL,
            data=data,
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read().decode('utf-8', errors='ignore')
        parsed = json.loads(raw or '{}')
        ok = bool(parsed.get('success'))
        if ok:
            return True, None
        return False, 'Security check failed. Please try again.'
    except Exception:
        return False, 'Security check failed. Please try again.'


def rate_limit(request, action: str, limit: int, window_seconds: int) -> tuple[bool, int]:
    """Simple IP-based rate limiter backed by Django cache.

    Returns (allowed, remaining).
    """
    ip = _get_client_ip(request) or 'unknown'
    key = f"rl:{action}:{ip}"

    try:
        # Initialize counter if missing
        added = cache.add(key, 0, timeout=window_seconds)
        try:
            current = cache.incr(key)
        except Exception:
            # Fallback for cache backends that don't support incr
            current = int(cache.get(key, 0) or 0) + 1
            cache.set(key, current, timeout=window_seconds)
    except Exception:
        # If cache misbehaves, fail open (don't break signup/contact)
        return True, limit

    remaining = max(0, limit - int(current))
    return (int(current) <= int(limit)), remaining
