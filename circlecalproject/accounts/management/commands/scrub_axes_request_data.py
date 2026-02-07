from __future__ import annotations

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Scrub django-axes AccessAttempt GET/POST payloads (set get_data/post_data to empty strings)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Only report how many rows would be updated.",
        )

    def handle(self, *args, **options):
        try:
            from axes.models import AccessAttempt
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"django-axes not installed or not importable: {e}"))
            return

        qs = AccessAttempt.objects.all()
        total = qs.count()
        to_scrub = qs.exclude(get_data="").count() + qs.exclude(post_data="").count()

        self.stdout.write(f"AccessAttempt rows: {total}")

        if options.get("dry_run"):
            self.stdout.write(self.style.WARNING(f"Dry-run: would scrub payloads on up to {to_scrub} rows"))
            return

        updated_get = qs.exclude(get_data="").update(get_data="")
        updated_post = qs.exclude(post_data="").update(post_data="")
        self.stdout.write(self.style.SUCCESS(f"Scrubbed get_data on {updated_get} rows"))
        self.stdout.write(self.style.SUCCESS(f"Scrubbed post_data on {updated_post} rows"))
