from __future__ import annotations

import logging
from typing import Any

import requests
from django.conf import settings

from .models import PushDevice

logger = logging.getLogger(__name__)


def _env_bool(value: Any) -> bool:
    try:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on")
    except Exception:
        return False


def push_enabled() -> bool:
    return _env_bool(getattr(settings, "EXPO_PUSH_ENABLED", False))


def expo_push_url() -> str:
    return str(getattr(settings, "EXPO_PUSH_URL", "https://exp.host/--/api/v2/push/send"))


def send_expo_push(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Send one or more Expo push messages.

    Best-effort: returns parsed JSON on success; returns None on network/parse failures.
    """

    if not messages:
        return None

    try:
        resp = requests.post(expo_push_url(), json=messages, timeout=6)
    except Exception as exc:
        logger.info("Expo push send failed (network): %s", exc)
        return None

    try:
        data = resp.json()
    except Exception:
        logger.info("Expo push send failed (non-JSON response) status=%s", getattr(resp, "status_code", "?"))
        return None

    return data


def send_push_to_user(*, user, title: str, body: str, data: dict[str, Any] | None = None) -> int:
    """Send a push notification to a user's active devices.

    Returns the number of target devices attempted.

    Design constraints (per product direction):
    - Only internal users (staff/manager/GM/owner) can receive pushes.
    - Clients are never pushed (they are not User records in this app).
    - Caller must decide who is "involved"; this helper just targets tokens for a user.
    """

    if not push_enabled():
        return 0

    # Pull current active tokens.
    tokens = list(
        PushDevice.objects.filter(user=user, is_active=True)
        .values_list("token", flat=True)
    )

    if not tokens:
        return 0

    payload_data: dict[str, Any] = data or {}

    messages = [
        {
            "to": t,
            "title": title,
            "body": body,
            "data": payload_data,
        }
        for t in tokens
    ]

    resp = send_expo_push(messages)
    if not resp:
        return len(tokens)

    # If Expo tells us a device token is no longer valid, deactivate it.
    try:
        results = resp.get("data")
        if isinstance(results, list) and len(results) == len(tokens):
            dead_tokens: list[str] = []
            for idx, r in enumerate(results):
                try:
                    if not isinstance(r, dict):
                        continue
                    if r.get("status") != "error":
                        continue
                    details = r.get("details") or {}
                    err = details.get("error") if isinstance(details, dict) else None
                    if err in {"DeviceNotRegistered", "InvalidCredentials"}:
                        dead_tokens.append(tokens[idx])
                except Exception:
                    continue

            if dead_tokens:
                try:
                    PushDevice.objects.filter(token__in=dead_tokens).update(is_active=False)
                except Exception:
                    pass
    except Exception:
        pass

    return len(tokens)
