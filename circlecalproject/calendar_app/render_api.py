import os
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


def get_render_config() -> RenderApiConfig | None:
    api_key = (os.getenv("RENDER_API_KEY") or "").strip()
    service_id = (os.getenv("RENDER_SERVICE_ID") or "").strip()
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
        # If Render can't find it by name yet, don't fail the whole flow.
        if exc.status_code == 404:
            return
        raise
