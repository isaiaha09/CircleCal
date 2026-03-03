from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from django.utils import timezone

from accounts.models import Business
from calendar_app.cloudflare_api import (
    CloudflareApiError,
    ensure_fallback_origin,
    create_custom_hostname,
    extract_dcv_records,
    extract_ssl_status,
    find_custom_hostname_id_by_hostname,
    get_cloudflare_config,
    get_custom_hostname,
    get_custom_hostname_by_hostname,
)


logger = logging.getLogger(__name__)


@dataclass
class CloudflareSyncResult:
    configured: bool
    created: bool
    active: bool
    ssl_status: str
    domain: str
    custom_hostname_id: str | None
    error: str | None = None


def _save_org_cloudflare_state(org: Business, payload: dict | None, error: str | None = None) -> CloudflareSyncResult:
    now = timezone.now()
    ssl_status = extract_ssl_status(payload)
    dcv_records = extract_dcv_records(payload)
    cid = ""
    hostname = (getattr(org, "custom_domain", None) or "").strip().lower()

    if isinstance(payload, dict):
        cid = (payload.get("id") or "").strip()
        hostname = ((payload.get("hostname") or hostname) or "").strip().lower()

    update_fields = [
        "custom_domain_cloudflare_ssl_status",
        "custom_domain_cloudflare_dcv_records",
        "custom_domain_cloudflare_last_checked_at",
        "custom_domain_cloudflare_last_error",
    ]

    org.custom_domain_cloudflare_ssl_status = ssl_status or None
    org.custom_domain_cloudflare_dcv_records = dcv_records
    org.custom_domain_cloudflare_last_checked_at = now
    org.custom_domain_cloudflare_last_error = (error or "").strip() or None

    if cid and cid != (getattr(org, "custom_domain_cloudflare_id", None) or ""):
        org.custom_domain_cloudflare_id = cid
        update_fields.append("custom_domain_cloudflare_id")

    if isinstance(payload, dict):
        if ssl_status.lower() == "active":
            if not org.custom_domain_verified:
                org.custom_domain_verified = True
                org.custom_domain_verified_at = now
                update_fields.extend(["custom_domain_verified", "custom_domain_verified_at"])
        else:
            if org.custom_domain_verified:
                org.custom_domain_verified = False
                org.custom_domain_verified_at = None
                update_fields.extend(["custom_domain_verified", "custom_domain_verified_at"])

    org.save(update_fields=list(dict.fromkeys(update_fields)))

    return CloudflareSyncResult(
        configured=True,
        created=False,
        active=(ssl_status.lower() == "active"),
        ssl_status=ssl_status,
        domain=hostname,
        custom_hostname_id=(getattr(org, "custom_domain_cloudflare_id", None) or None),
        error=(error or "").strip() or None,
    )


def sync_custom_hostname(
    org: Business,
    *,
    create_if_missing: bool = False,
) -> CloudflareSyncResult:
    domain = (getattr(org, "custom_domain", None) or "").strip().lower().rstrip(".")
    if not domain:
        return CloudflareSyncResult(
            configured=bool(get_cloudflare_config()),
            created=False,
            active=False,
            ssl_status="",
            domain="",
            custom_hostname_id=None,
            error="No custom domain set",
        )

    cfg = get_cloudflare_config()
    if not cfg:
        return CloudflareSyncResult(
            configured=False,
            created=False,
            active=False,
            ssl_status="",
            domain=domain,
            custom_hostname_id=(getattr(org, "custom_domain_cloudflare_id", None) or None),
            error="Cloudflare config missing",
        )

    created = False
    try:
        # Cloudflare SaaS requires a fallback origin to be configured for custom
        # hostnames to become active. Best-effort: keep it synced from env.
        try:
            if getattr(cfg, "fallback_origin", None):
                ensure_fallback_origin(cfg, cfg.fallback_origin or "")
        except Exception:
            # Do not block hostname provisioning on fallback-origin sync errors.
            pass

        cid = (getattr(org, "custom_domain_cloudflare_id", None) or "").strip()
        payload: dict = {}

        if cid:
            payload = get_custom_hostname(cfg, cid)

        if not payload:
            payload = get_custom_hostname_by_hostname(cfg, domain)

        if not payload and create_if_missing:
            payload = create_custom_hostname(
                cfg,
                domain,
                custom_metadata={
                    "org_id": str(org.id),
                    "org_slug": str(org.slug),
                },
            )
            created = bool(payload)

        if not payload:
            # Last chance: find id by hostname and fetch directly.
            found_id = find_custom_hostname_id_by_hostname(cfg, domain)
            if found_id:
                payload = get_custom_hostname(cfg, found_id)

        result = _save_org_cloudflare_state(org, payload, error=None)
        result.created = created
        return result
    except CloudflareApiError as exc:
        logger.warning(
            "Cloudflare sync failed for org=%s domain=%s status=%s payload=%s",
            org.id,
            domain,
            getattr(exc, "status_code", None),
            getattr(exc, "payload", None),
        )
        msg = f"Cloudflare API error: {exc}"
        save_result = _save_org_cloudflare_state(org, None, error=msg)
        save_result.created = created
        save_result.error = msg
        return save_result
    except Exception as exc:
        logger.exception("Unexpected Cloudflare sync error for org=%s domain=%s", org.id, domain)
        msg = f"Unexpected sync error: {exc}"
        save_result = _save_org_cloudflare_state(org, None, error=msg)
        save_result.created = created
        save_result.error = msg
        return save_result


def poll_custom_hostname_until_active(
    org: Business,
    *,
    interval_seconds: int = 30,
    max_attempts: int = 6,
    create_if_missing: bool = False,
) -> CloudflareSyncResult:
    attempts = max(1, int(max_attempts or 1))
    sleep_s = max(5, int(interval_seconds or 30))

    latest = sync_custom_hostname(org, create_if_missing=create_if_missing)
    for idx in range(1, attempts):
        if latest.active:
            return latest
        time.sleep(sleep_s)
        latest = sync_custom_hostname(org, create_if_missing=False)

    return latest
