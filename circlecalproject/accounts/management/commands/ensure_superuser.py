from __future__ import annotations

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        "Create or update a Django superuser from environment variables. "
        "Designed for hosts that don't allow interactive shell access (e.g., Render free plan)."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--force",
            action="store_true",
            help="Run even if DJANGO_BOOTSTRAP_SUPERUSER is not set to '1'.",
        )

    def handle(self, *args, **options) -> None:
        enabled = os.getenv("DJANGO_BOOTSTRAP_SUPERUSER", "").strip() == "1"
        if not enabled and not options.get("force"):
            self.stdout.write("ensure_superuser: disabled (set DJANGO_BOOTSTRAP_SUPERUSER=1)")
            return

        username = (os.getenv("DJANGO_SUPERUSER_USERNAME") or "").strip()
        email = (os.getenv("DJANGO_SUPERUSER_EMAIL") or "").strip()
        password = os.getenv("DJANGO_SUPERUSER_PASSWORD") or ""

        if not username:
            raise SystemExit("ensure_superuser: missing DJANGO_SUPERUSER_USERNAME")
        if not email:
            raise SystemExit("ensure_superuser: missing DJANGO_SUPERUSER_EMAIL")
        if not password:
            raise SystemExit("ensure_superuser: missing DJANGO_SUPERUSER_PASSWORD")

        User = get_user_model()

        field_names = {f.name for f in User._meta.fields}
        has_username = "username" in field_names
        has_email = "email" in field_names

        user = None
        created = False

        # 1) Prefer username lookup (usually unique)
        if has_username:
            user = User.objects.filter(username=username).first()

        # 2) Fall back to email lookup
        if user is None and has_email:
            matches = list(User.objects.filter(email=email)[:2])
            if len(matches) > 1:
                self.stderr.write(
                    "ensure_superuser: WARNING multiple users share DJANGO_SUPERUSER_EMAIL; using the first match"
                )
            user = matches[0] if matches else None

        # 3) Create if missing (prefer create_superuser for custom user models)
        if user is None:
            if hasattr(User.objects, "create_superuser"):
                user = User.objects.create_superuser(
                    username=username if has_username else email,
                    email=email if has_email else "",
                    password=password,
                )
            else:
                user = User.objects.create(
                    **({"username": username} if has_username else {}),
                    **({"email": email} if has_email else {}),
                )
                user.is_staff = True
                user.is_superuser = True
                user.is_active = True
                user.set_password(password)
                user.save()
            created = True

        # Keep fields consistent.
        changed = False
        if getattr(user, "username", None) != username and hasattr(user, "username"):
            user.username = username
            changed = True
        if getattr(user, "email", None) != email and hasattr(user, "email"):
            user.email = email
            changed = True

        if not user.is_staff:
            user.is_staff = True
            changed = True
        if not user.is_superuser:
            user.is_superuser = True
            changed = True
        if not user.is_active:
            user.is_active = True
            changed = True

        # Always set password when bootstrapping to ensure access.
        user.set_password(password)
        changed = True

        if changed:
            user.save()

        if created:
            self.stdout.write("ensure_superuser: created superuser")
        else:
            self.stdout.write("ensure_superuser: updated superuser")

        # Safety: remind to disable after first successful deploy.
        self.stdout.write(
            "ensure_superuser: done (recommended: set DJANGO_BOOTSTRAP_SUPERUSER=0 after verification)"
        )
