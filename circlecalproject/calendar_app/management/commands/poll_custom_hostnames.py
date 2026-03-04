from django.core.management.base import BaseCommand

from accounts.models import Business
from calendar_app.custom_domain_cloudflare import sync_custom_hostname


class Command(BaseCommand):
    help = "Poll Cloudflare custom hostname SSL status and mark domains live when active."

    def add_arguments(self, parser):
        parser.add_argument("--org-id", type=int, default=None, help="Only poll one org/business id")
        parser.add_argument(
            "--limit",
            type=int,
            default=200,
            help="Max number of pending domains to process per run",
        )

    def handle(self, *args, **options):
        org_id = options.get("org_id")
        limit = max(1, int(options.get("limit") or 200))

        queryset = Business.objects.filter(
            custom_domain__isnull=False,
            custom_domain_cloudflare_id__isnull=False,
            custom_domain_verified=False,
        )
        if org_id:
            queryset = queryset.filter(id=org_id)

        orgs = list(queryset.order_by("id")[:limit])
        if not orgs:
            self.stdout.write(self.style.SUCCESS("No pending Cloudflare subdomains to poll."))
            return

        active_count = 0
        pending_count = 0
        error_count = 0

        for org in orgs:
            result = sync_custom_hostname(org, create_if_missing=False)
            if result.error:
                error_count += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"org={org.id} domain={result.domain} status={result.ssl_status or 'unknown'} error={result.error}"
                    )
                )
                continue

            if result.active:
                active_count += 1
                self.stdout.write(self.style.SUCCESS(f"org={org.id} domain={result.domain} ssl=active"))
            else:
                pending_count += 1
                self.stdout.write(f"org={org.id} domain={result.domain} ssl={result.ssl_status or 'pending'}")

        self.stdout.write(
            self.style.SUCCESS(
                f"Processed={len(orgs)} active={active_count} pending={pending_count} errors={error_count}"
            )
        )
