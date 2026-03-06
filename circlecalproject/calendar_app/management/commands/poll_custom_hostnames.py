from django.core.management.base import BaseCommand

class Command(BaseCommand):
    help = "Deprecated no-op. Cloudflare custom hostname polling has been disabled."

    def add_arguments(self, parser):
        parser.add_argument("--org-id", type=int, default=None, help="Only poll one org/business id")
        parser.add_argument(
            "--limit",
            type=int,
            default=200,
            help="Max number of pending domains to process per run",
        )

    def handle(self, *args, **options):
        self.stdout.write(
            self.style.WARNING(
                "poll_custom_hostnames is disabled: Cloudflare custom-hostname polling is no longer used."
            )
        )
