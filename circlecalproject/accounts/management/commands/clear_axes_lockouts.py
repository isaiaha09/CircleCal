from __future__ import annotations

import os

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        "Clear Django Axes lockouts/attempts. Useful on hosts without shell access "
        "when you get stuck in a lockout loop."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--force",
            action="store_true",
            help="Run even if DJANGO_CLEAR_AXES is not set to '1'.",
        )

    def handle(self, *args, **options) -> None:
        enabled = os.getenv("DJANGO_CLEAR_AXES", "").strip() == "1"
        if not enabled and not options.get("force"):
            self.stdout.write("clear_axes_lockouts: disabled (set DJANGO_CLEAR_AXES=1)")
            return

        username = (os.getenv("DJANGO_CLEAR_AXES_USERNAME") or "").strip() or None

        # Best effort: use axes' public reset helper when available.
        try:
            from axes.utils import reset as axes_reset  # type: ignore

            if username:
                axes_reset(username=username)
                self.stdout.write(f"clear_axes_lockouts: reset(username={username!r})")
            else:
                axes_reset()
                self.stdout.write("clear_axes_lockouts: reset(all)")
            return
        except Exception:
            pass

        # Fallback: clear AccessAttempt rows when Axes uses the database handler.
        try:
            from axes.models import AccessAttempt  # type: ignore

            qs = AccessAttempt.objects.all()
            if username:
                qs = qs.filter(username__iexact=username)
            deleted = qs.delete()[0]
            self.stdout.write(f"clear_axes_lockouts: deleted {deleted} AccessAttempt rows")
            return
        except Exception as e:
            self.stderr.write(f"clear_axes_lockouts: failed to clear axes data ({e})")
