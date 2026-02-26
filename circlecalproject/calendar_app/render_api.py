import os
import logging
from dataclasses import dataclass
from typing import Any

import requests


RENDER_API_BASE_URL = "https://api.render.com/v1"


@dataclass(frozen=True)
class RenderApiConfig:
    api_key: str
    service_id: str


class RenderApiError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, payload: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


def _env_first(*names: str) -> str:
    for name in names:
        try:
            val = (os.getenv(name) or '').strip()
        except Exception:
            val = ''
        if val:
            return val
    return ''


def _normalize_api_key(api_key: str) -> str:
    api_key = (api_key or '').strip()
    # Some env var UIs accidentally add newlines.
    api_key = api_key.replace('\r', '').replace('\n', '').strip()

    def _strip_wrapping_quotes(val: str) -> str:
        val = (val or '').strip()
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            return val[1:-1].strip()
        return val

    # Common misconfig: wrapping the token in quotes in env vars.
    api_key = _strip_wrapping_quotes(api_key)

    # Common misconfig: pasting the key with a 'Bearer ' prefix.
    # Our requests add 'Authorization: Bearer <key>', so a prefixed value becomes
    # 'Bearer Bearer ...' and yields 401.
    if api_key.lower().startswith('bearer '):
        api_key = api_key.split(None, 1)[1].strip()

    # Handle keys that were quoted *around* the Bearer prefix (e.g. "'Bearer abc'").
    api_key = _strip_wrapping_quotes(api_key)
    return api_key


def get_render_config() -> RenderApiConfig | None:
    # Support a few common env var names to reduce deployment foot-guns.
    api_key = _normalize_api_key(_env_first('RENDER_API_KEY', 'RENDER_API_TOKEN', 'RENDER_TOKEN'))
    service_id = _env_first('RENDER_SERVICE_ID', 'RENDER_WEB_SERVICE_ID')
    if not api_key or not service_id:
        return None
    return RenderApiConfig(api_key=api_key, service_id=service_id)


def _headers(cfg: RenderApiConfig) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {cfg.api_key}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _request(cfg: RenderApiConfig, method: str, path: str, *, json: Any = None, params: Any = None) -> Any:
    url = f"{RENDER_API_BASE_URL}{path}"
    resp = requests.request(method, url, headers=_headers(cfg), json=json, params=params, timeout=15)

    # Render often returns JSON errors, but keep this defensive.
    payload: Any
    try:
        payload = resp.json()
    except Exception:
        payload = resp.text

    if resp.status_code >= 400:
        msg = None
        if isinstance(payload, dict):
            msg = payload.get("message") or payload.get("error")
        raise RenderApiError(msg or f"Render API error {resp.status_code}", status_code=resp.status_code, payload=payload)

    return payload


def list_custom_domains(cfg: RenderApiConfig) -> list[dict[str, Any]]:
    # Response is a list of { cursor, customDomain } items
    payload = _request(cfg, "GET", f"/services/{cfg.service_id}/custom-domains")
    if isinstance(payload, list):
        return payload
    return []


def retrieve_service(cfg: RenderApiConfig) -> dict[str, Any]:
    payload = _request(cfg, "GET", f"/services/{cfg.service_id}")
    return payload if isinstance(payload, dict) else {}


def log_auth_diagnostics(cfg: RenderApiConfig) -> None:
    """Logs-only check to distinguish missing/invalid auth from other failures.

    Never raises; never logs the API key.
    """
    logger = logging.getLogger(__name__)
    try:
        _request(cfg, 'GET', '/users')
        logger.info('Render API auth check OK (service_id=%s)', cfg.service_id)
        try:
            print(f'CC_RENDER_AUTH_CHECK ok service_id={cfg.service_id}')
        except Exception:
            pass
    except RenderApiError as exc:
        try:
            logger.warning(
                'Render API auth check failed (status=%s, service_id=%s, payload=%s)',
                getattr(exc, 'status_code', None),
                cfg.service_id,
                getattr(exc, 'payload', None),
            )
        except Exception:
            pass
        try:
            status = getattr(exc, 'status_code', None)
            payload = getattr(exc, 'payload', None)
            print(f'CC_RENDER_AUTH_CHECK fail status={status} service_id={cfg.service_id} payload={payload!r}')
        except Exception:
            pass
    except Exception as exc:
        try:
            logger.warning('Render API auth check failed unexpectedly (service_id=%s, err=%s)', cfg.service_id, exc)
        except Exception:
            pass
        try:
            print(f'CC_RENDER_AUTH_CHECK error service_id={cfg.service_id} err={exc!r}')
        except Exception:
            pass


def log_config_presence() -> None:
    """Logs-only: indicate whether Render env vars look present (never logs values)."""
    try:
        api_key_raw = _env_first('RENDER_API_KEY', 'RENDER_API_TOKEN', 'RENDER_TOKEN')
        service_id_raw = _env_first('RENDER_SERVICE_ID', 'RENDER_WEB_SERVICE_ID')
        api_key_norm = _normalize_api_key(api_key_raw)
        service_id = (service_id_raw or '').strip()
        print(
            'CC_RENDER_CONFIG_PRESENT '
            f'api_key_present={bool(api_key_raw)} api_key_len={len(api_key_norm)} '
            f'service_id_present={bool(service_id_raw)} service_id_len={len(service_id)}'
        )
    except Exception:
        # Never break the request if logging fails.
        pass


def create_custom_domain(cfg: RenderApiConfig, domain_name: str) -> Any:
    try:
        return _request(
            cfg,
            "POST",
            f"/services/{cfg.service_id}/custom-domains",
            json={"name": domain_name},
        )
    except RenderApiError as exc:
        # Domain already exists on the service
        if exc.status_code == 409:
            return {"already_exists": True}
        raise


def delete_custom_domain(cfg: RenderApiConfig, domain_name_or_id: str) -> None:
    try:
        _request(cfg, "DELETE", f"/services/{cfg.service_id}/custom-domains/{domain_name_or_id}")
    except RenderApiError as exc:
        # If it's already gone, treat as success.
        if exc.status_code == 404:
            return
        raise


def trigger_verify_custom_domain(cfg: RenderApiConfig, domain_name_or_id: str) -> None:
    _request(cfg, "POST", f"/services/{cfg.service_id}/custom-domains/{domain_name_or_id}/verify")


def ensure_custom_domain_attached(cfg: RenderApiConfig, domain_name: str) -> None:
    # Ensure it exists and then trigger Render-side DNS verification.
    create_custom_domain(cfg, domain_name)
    try:
        trigger_verify_custom_domain(cfg, domain_name)
    except RenderApiError as exc:
        # Render may not be able to verify by *name* immediately after creation.
        # If that happens, fall back to resolving the domain's ID via list and
        # verify using the ID.
        if exc.status_code == 404:
            try:
                rows = list_custom_domains(cfg)
                target = domain_name.strip().lower()
                for row in rows:
                    cd = (row or {}).get("customDomain") if isinstance(row, dict) else None
                    if not isinstance(cd, dict):
                        continue
                    if (cd.get("name") or "").strip().lower() != target:
                        continue
                    cd_id = (cd.get("id") or "").strip()
                    if cd_id:
                        trigger_verify_custom_domain(cfg, cd_id)
                    return
            except Exception:
                return
        raise
