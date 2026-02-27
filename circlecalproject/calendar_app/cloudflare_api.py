import os
import logging
from dataclasses import dataclass
from typing import Any
import re

import requests


CLOUDFLARE_API_BASE_URL = "https://api.cloudflare.com/client/v4"


@dataclass(frozen=True)
class CloudflareApiConfig:
    api_token: str
    zone_id: str
    fallback_origin: str | None


class CloudflareApiError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, payload: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


def _env_first(*names: str) -> str:
    for name in names:
        try:
            val = (os.getenv(name) or "").strip()
        except Exception:
            val = ""
        if val:
            return val
    return ""


def _strip_wrapping_quotes(val: str) -> str:
    val = (val or "").strip()
    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
        return val[1:-1].strip()
    return val


def _normalize_api_token(token: str) -> str:
    token = (token or "").replace("\r", "").replace("\n", "").strip()
    token = _strip_wrapping_quotes(token)

    # Tolerate pasting a full header line.
    if token.lower().startswith("authorization:"):
        token = token.split(":", 1)[1].strip()

    bearer_match = re.search(r"(?i)\bbearer\s+(.+)$", token)
    if bearer_match:
        token = bearer_match.group(1).strip()

    token = _strip_wrapping_quotes(token)
    token = "".join((token or "").split())
    return token


def get_cloudflare_config() -> CloudflareApiConfig | None:
    api_token = _normalize_api_token(_env_first("CLOUDFLARE_API_TOKEN", "CF_API_TOKEN"))
    zone_id = _env_first("CLOUDFLARE_ZONE_ID", "CF_ZONE_ID")
    fallback_origin = _env_first(
        "CLOUDFLARE_FALLBACK_ORIGIN",
        "CF_FALLBACK_ORIGIN",
        "CUSTOM_DOMAIN_FALLBACK_ORIGIN",
    )
    if not api_token or not zone_id:
        return None
    return CloudflareApiConfig(
        api_token=api_token,
        zone_id=zone_id.strip(),
        fallback_origin=(fallback_origin.strip() if fallback_origin else None),
    )


def _headers(cfg: CloudflareApiConfig) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {cfg.api_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _request(cfg: CloudflareApiConfig, method: str, path: str, *, json: Any = None, params: Any = None) -> Any:
    url = f"{CLOUDFLARE_API_BASE_URL}{path}"
    resp = requests.request(method, url, headers=_headers(cfg), json=json, params=params, timeout=20)

    payload: Any
    try:
        payload = resp.json()
    except Exception:
        payload = resp.text

    if resp.status_code >= 400:
        msg = None
        if isinstance(payload, dict):
            # Cloudflare wraps errors in an array.
            errs = payload.get("errors")
            if isinstance(errs, list) and errs:
                try:
                    msg = errs[0].get("message")
                except Exception:
                    msg = None
            msg = msg or payload.get("message")
        raise CloudflareApiError(msg or f"Cloudflare API error {resp.status_code}", status_code=resp.status_code, payload=payload)

    # Cloudflare standard envelope: { success, errors, messages, result }
    if isinstance(payload, dict) and "success" in payload and payload.get("success") is False:
        msg = None
        errs = payload.get("errors")
        if isinstance(errs, list) and errs:
            try:
                msg = errs[0].get("message")
            except Exception:
                msg = None
        raise CloudflareApiError(msg or "Cloudflare API returned success=false", status_code=resp.status_code, payload=payload)

    return payload


def ensure_fallback_origin(cfg: CloudflareApiConfig, origin: str) -> None:
    origin = (origin or "").strip()
    if not origin:
        return
    _request(cfg, "PUT", f"/zones/{cfg.zone_id}/custom_hostnames/fallback_origin", json={"origin": origin})


def find_custom_hostname_id_by_hostname(cfg: CloudflareApiConfig, hostname: str) -> str | None:
    hostname = (hostname or "").strip().lower().rstrip(".")
    if not hostname:
        return None

    # Cloudflare supports filtering by hostname.
    payload = _request(
        cfg,
        "GET",
        f"/zones/{cfg.zone_id}/custom_hostnames",
        params={"hostname": hostname, "per_page": 50, "page": 1},
    )

    try:
        results = payload.get("result") if isinstance(payload, dict) else None
    except Exception:
        results = None

    if isinstance(results, list):
        for row in results:
            if not isinstance(row, dict):
                continue
            if (row.get("hostname") or "").strip().lower().rstrip(".") == hostname:
                cid = (row.get("id") or "").strip()
                if cid:
                    return cid
    return None


def create_custom_hostname(cfg: CloudflareApiConfig, hostname: str, *, custom_metadata: dict[str, str] | None = None) -> dict[str, Any]:
    hostname = (hostname or "").strip().lower().rstrip(".")
    body: dict[str, Any] = {
        "hostname": hostname,
        "ssl": {
            "method": "http",
            "type": "dv",
        },
    }
    if custom_metadata:
        body["custom_metadata"] = custom_metadata

    payload = _request(cfg, "POST", f"/zones/{cfg.zone_id}/custom_hostnames", json=body)
    if isinstance(payload, dict) and isinstance(payload.get("result"), dict):
        return payload["result"]
    return {}


def ensure_custom_hostname(cfg: CloudflareApiConfig, hostname: str, *, custom_metadata: dict[str, str] | None = None) -> str | None:
    """Create a custom hostname if it doesn't exist; returns its id."""
    existing_id = find_custom_hostname_id_by_hostname(cfg, hostname)
    if existing_id:
        return existing_id

    result = create_custom_hostname(cfg, hostname, custom_metadata=custom_metadata)
    cid = (result.get("id") or "").strip() if isinstance(result, dict) else ""
    return cid or None


def delete_custom_hostname(cfg: CloudflareApiConfig, custom_hostname_id: str) -> None:
    custom_hostname_id = (custom_hostname_id or "").strip()
    if not custom_hostname_id:
        return
    _request(cfg, "DELETE", f"/zones/{cfg.zone_id}/custom_hostnames/{custom_hostname_id}")


def log_config_presence() -> None:
    """Logs-only: whether Cloudflare env vars look present (never logs secrets)."""
    try:
        raw_token = _env_first("CLOUDFLARE_API_TOKEN", "CF_API_TOKEN")
        zone_id = _env_first("CLOUDFLARE_ZONE_ID", "CF_ZONE_ID")
        token_norm = _normalize_api_token(raw_token)
        print(
            "CC_CLOUDFLARE_CONFIG_PRESENT "
            f"api_token_present={bool(raw_token)} api_token_len={len(token_norm)} "
            f"zone_id_present={bool(zone_id)} zone_id_len={len(zone_id)}"
        )
    except Exception:
        pass


def log_api_check(cfg: CloudflareApiConfig) -> None:
    """Logs-only: basic connectivity/auth check."""
    logger = logging.getLogger(__name__)
    try:
        _request(cfg, "GET", "/user")
        logger.info("Cloudflare API auth check OK (zone_id=%s)", cfg.zone_id)
        try:
            print(f"CC_CLOUDFLARE_AUTH_CHECK ok zone_id={cfg.zone_id}")
        except Exception:
            pass
    except CloudflareApiError as exc:
        try:
            logger.warning(
                "Cloudflare API auth check failed (status=%s, zone_id=%s, payload=%s)",
                getattr(exc, "status_code", None),
                cfg.zone_id,
                getattr(exc, "payload", None),
            )
        except Exception:
            pass
        try:
            status = getattr(exc, "status_code", None)
            payload = getattr(exc, "payload", None)
            print(f"CC_CLOUDFLARE_AUTH_CHECK fail status={status} zone_id={cfg.zone_id} payload={payload!r}")
        except Exception:
            pass
    except Exception as exc:
        try:
            logger.warning("Cloudflare API auth check errored (zone_id=%s, err=%s)", cfg.zone_id, exc)
        except Exception:
            pass
        try:
            print(f"CC_CLOUDFLARE_AUTH_CHECK error zone_id={cfg.zone_id} err={exc!r}")
        except Exception:
            pass
