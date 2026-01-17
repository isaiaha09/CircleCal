"""Smoke test for avatar uploads.

Runs against whichever Django storage backend is configured.
- Local dev (default): FileSystemStorage into MEDIA_ROOT
- Production (optional): GoogleCloudStorage (Firebase/GCS) when GS_BUCKET_NAME is set

Usage:
  python scripts/smoke_avatar_upload.py --email you@example.com
  python scripts/smoke_avatar_upload.py --settings circlecalproject.settings_prod --cleanup

Environment notes (for GCS/Firebase):
  - GS_BUCKET_NAME or FIREBASE_STORAGE_BUCKET must be set
  - Provide credentials via GOOGLE_APPLICATION_CREDENTIALS or GOOGLE_CREDENTIALS_JSON
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
from pathlib import Path
from urllib.parse import urlparse

import django


_ONE_BY_ONE_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\x0bIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe"
    b"\xa7\x85\x81\x9a\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke test avatar upload storage")
    parser.add_argument(
        "--settings",
        default=os.getenv("DJANGO_SETTINGS_MODULE", "circlecalproject.settings"),
        help="Django settings module (default: circlecalproject.settings)",
    )
    parser.add_argument(
        "--email",
        default=None,
        help="Use an existing user by email; otherwise a temp user is created.",
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Delete the uploaded avatar object after verifying.",
    )
    parser.add_argument(
        "--info-only",
        action="store_true",
        help="Only print backend/config info; do not upload.",
    )
    return parser.parse_args()


def _safe_cloudinary_cloud_name() -> str | None:
    url = (os.getenv("CLOUDINARY_URL") or "").strip()
    if not url:
        return (os.getenv("CLOUDINARY_CLOUD_NAME") or "").strip() or None
    try:
        parsed = urlparse(url)
        # cloudinary://<key>:<secret>@<cloud_name>
        host = (parsed.hostname or "").strip()
        return host or None
    except Exception:
        return None


def _warn_if_cloudinary_url_looks_wrong() -> None:
    url = os.getenv("CLOUDINARY_URL")
    if not url:
        return
    # Common copy/paste mistake: including the <...> brackets from docs/UI.
    if "<" in url or ">" in url:
        print("WARN: CLOUDINARY_URL contains '<' or '>' characters. Remove those brackets.")
    if any(ch.isspace() for ch in url):
        print("WARN: CLOUDINARY_URL contains whitespace. Remove spaces/newlines.")


def main() -> int:
    args = _parse_args()

    # Ensure the Django project root (the folder containing manage.py) is on sys.path.
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", args.settings)
    django.setup()

    from django.contrib.auth import get_user_model
    from django.core.files.base import ContentFile

    from accounts.models import Profile

    User = get_user_model()

    if args.email:
        try:
            user = User.objects.get(email=args.email)
        except User.DoesNotExist:
            print(f"No user found with email={args.email!r}")
            return 2
    else:
        token = secrets.token_hex(6)
        email = f"smoke-avatar-{token}@example.com"
        username = f"smokeavatar{token}"
        user = User.objects.create_user(username=username, email=email, password=secrets.token_urlsafe(16))

    profile, _ = Profile.objects.get_or_create(user=user)

    storage = profile.avatar.storage

    # Print config/backends (no secrets)
    _warn_if_cloudinary_url_looks_wrong()
    cloud_name = _safe_cloudinary_cloud_name()
    gs_bucket = (os.getenv("GS_BUCKET_NAME") or os.getenv("FIREBASE_STORAGE_BUCKET") or "").strip() or None
    using_cloudinary = bool(cloud_name)
    using_gcs = bool(gs_bucket) and not using_cloudinary
    print("Backend info")
    print(" settings:", args.settings)
    print(" cloudinary:", "enabled" if using_cloudinary else "disabled", (f"(cloud={cloud_name})" if cloud_name else ""))
    print(" gcs:", "enabled" if using_gcs else "disabled", (f"(bucket={gs_bucket})" if gs_bucket else ""))
    print(" storage:", f"{storage.__class__.__module__}.{storage.__class__.__name__}")

    if args.info_only:
        return 0
    content = ContentFile(_ONE_BY_ONE_PNG)

    # Use a stable filename; your upload_to will place it under the per-user directory.
    profile.avatar.save("avatar.png", content, save=True)

    print("OK uploaded")
    print(" user_id:", user.id)
    print(" name:", profile.avatar.name)
    try:
        print(" url:", profile.avatar.url)
    except Exception as exc:  # noqa: BLE001
        print(" url: <unavailable>", exc)

    if args.cleanup:
        try:
            profile.avatar.delete(save=True)
            print("OK cleaned up")
        except Exception as exc:  # noqa: BLE001
            print("WARN cleanup failed:", exc)
            return 3

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
