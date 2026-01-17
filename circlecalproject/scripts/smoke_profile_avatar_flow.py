"""Smoke test: profile page avatar upload/render flow.

This verifies the *real* view/template path:
- logs a user in via Django test client
- POSTs a tiny image to /accounts/profile/
- confirms Profile.avatar_updated_at is set
- confirms the profile page HTML includes a Cloudinary (or storage) URL and a cache-busting ?v=<unix>

Usage:
  D:/CircleCalBackup/.venv/Scripts/python.exe scripts/smoke_profile_avatar_flow.py --cleanup
  D:/CircleCalBackup/.venv/Scripts/python.exe scripts/smoke_profile_avatar_flow.py --email you@example.com

Notes:
- This script may upload to Cloudinary if Cloudinary is enabled in your env.
- Keep it as a local/dev smoke test (not intended for CI).
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
from pathlib import Path

import django


_ONE_BY_ONE_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\x0bIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe"
    b"\xa7\x85\x81\x9a\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _make_png_bytes() -> bytes:
    try:
        from PIL import Image  # type: ignore
        from io import BytesIO

        img = Image.new("RGBA", (16, 16), (255, 0, 0, 255))
        buf = BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return _ONE_BY_ONE_PNG


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke test profile avatar upload + page render")
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
    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", args.settings)
    django.setup()

    # Standalone scripts don't get Django's test runner host relaxations.
    # The Django test Client uses HTTP_HOST='testserver' by default.
    from django.conf import settings as dj_settings

    try:
        allowed = list(getattr(dj_settings, "ALLOWED_HOSTS", []) or [])
        if "testserver" not in allowed and "*" not in allowed:
            allowed.append("testserver")
            dj_settings.ALLOWED_HOSTS = allowed
    except Exception:
        pass

    # So response.context / template info is captured.
    try:
        from django.test.utils import setup_test_environment

        setup_test_environment()
    except Exception:
        pass

    from django.contrib.auth import get_user_model
    from django.core.files.uploadedfile import SimpleUploadedFile
    from django.test import Client
    from django.urls import reverse

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
        email = f"smoke-profile-avatar-{token}@example.com"
        username = f"smokeprofile{token}"
        user = User.objects.create_user(username=username, email=email, password=secrets.token_urlsafe(16))

    profile, _ = Profile.objects.get_or_create(user=user)

    client = Client()
    client.force_login(user)

    url = reverse("accounts:profile")

    uploaded = SimpleUploadedFile("avatar.png", _make_png_bytes(), content_type="image/png")
    data = {
        "username": getattr(user, "username", "") or "",
        "email": getattr(user, "email", "") or "",
        "first_name": getattr(user, "first_name", "") or "Smoke",
        "last_name": getattr(user, "last_name", "") or "Test",
        "timezone": getattr(profile, "timezone", "UTC") or "UTC",
        "display_name": getattr(profile, "display_name", "") or "",
        "email_alerts": "on" if getattr(profile, "email_alerts", True) else "",
        "booking_reminders": "on" if getattr(profile, "booking_reminders", True) else "",
    }

    resp = client.post(url, data={**data, "avatar": uploaded}, follow=False)
    status = int(getattr(resp, "status_code", 0) or 0)
    ok_redirect = status in (302, 303)
    print("POST", url, "->", status)
    if not ok_redirect:
        # Try to print server-side validation errors (more useful than raw HTML)
        try:
            ctx = getattr(resp, "context", None)
            if ctx and isinstance(ctx, (list, tuple)):
                # When multiple templates are rendered, Django uses a list.
                ctx = ctx[-1]
            if ctx and "form" in ctx:
                form = ctx["form"]
                try:
                    print("Form errors:")
                    for field, errs in form.errors.items():
                        for e in errs:
                            print(f" - {field}: {e}")
                except Exception:
                    pass
            msgs = None
            try:
                msgs = list(ctx.get("messages", [])) if ctx else None
            except Exception:
                msgs = None
            if msgs:
                print("Messages:")
                for m in msgs:
                    try:
                        print(f" - {m}")
                    except Exception:
                        pass
        except Exception:
            pass

        # Also validate the form directly (independent of template context capture)
        try:
            from accounts.forms import ProfileForm
            from django.contrib import messages as _messages
            from django.contrib.messages.storage.fallback import FallbackStorage
            from django.http import HttpRequest

            uploaded2 = SimpleUploadedFile("avatar.png", _make_png_bytes(), content_type="image/png")
            form = ProfileForm(data, {"avatar": uploaded2}, instance=profile)
            if not form.is_valid():
                print("Direct ProfileForm.is_valid() -> False")
                for field, errs in form.errors.items():
                    for e in errs:
                        print(f" - {field}: {e}")
            else:
                print("Direct ProfileForm.is_valid() -> True (so failure is likely before/after form validation)")

            # If the view bailed out due to missing name fields, it sets a message.
            # We can't easily access request messages here, but we can still hint.
            if not (str(data.get("first_name") or "").strip() and str(data.get("last_name") or "").strip()):
                print("Hint: profile_view requires first_name and last_name on POST")
        except Exception as exc:
            print("WARN: could not run direct ProfileForm validation:", exc)

        try:
            body = (getattr(resp, "content", b"") or b"")[:2000]
            print("Response body (first 2KB):")
            print(body.decode("utf-8", errors="replace"))
        except Exception:
            pass
        return 1

    # Refresh and verify stored values
    profile.refresh_from_db()
    print("Profile updated_at:", profile.avatar_updated_at)
    if not profile.avatar_updated_at:
        print("FAIL: avatar_updated_at was not set")
        return 1

    if not profile.avatar:
        print("FAIL: profile.avatar is empty")
        return 1

    try:
        avatar_url = profile.avatar.url
    except Exception as exc:  # noqa: BLE001
        print("FAIL: profile.avatar.url not available:", exc)
        return 1

    print("Avatar name:", profile.avatar.name)
    print("Avatar url:", avatar_url)

    # Fetch profile page and verify HTML includes cache-busting ?v=
    resp2 = client.get(url)
    if int(getattr(resp2, "status_code", 0) or 0) != 200:
        print("FAIL: GET profile did not return 200:", resp2.status_code)
        return 1

    html = (getattr(resp2, "content", b"") or b"").decode("utf-8", errors="replace")
    expected_v = str(int(profile.avatar_updated_at.timestamp()))
    if "?v=" not in html:
        print("FAIL: profile page did not include ?v= cache-buster")
        return 1
    if expected_v not in html:
        print("FAIL: profile page did not include expected cache-buster value:", expected_v)
        return 1

    # Optional extra hint
    if "res.cloudinary.com" in avatar_url:
        print("OK: Cloudinary URL detected")
    else:
        print("OK: Non-Cloudinary URL detected (this is fine if Cloudinary is disabled)")

    print("OK: profile page includes cache-busting query param")

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
