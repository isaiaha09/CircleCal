from django.core.management.base import BaseCommand
from accounts.deletion import delete_due_trial_accounts


class Command(BaseCommand):
    help = (
        "Deactivates user accounts that were scheduled for deactivation at trial end "
        "(typically after cancel-at-period-end during trial)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be deleted without deleting anything.',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=200,
            help='Max number of accounts to process in one run.',
        )

    def handle(self, *args, **options):
        dry_run = bool(options.get('dry_run'))
        limit = int(options.get('limit') or 200)
        result = delete_due_trial_accounts(limit=limit, dry_run=dry_run)
        self.stdout.write(self.style.SUCCESS(
            f"Deactivated={result.get('deactivated', 0)}, skipped={result.get('skipped', 0)}."
        ))
