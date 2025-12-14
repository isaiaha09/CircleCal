from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from datetime import timedelta

from bookings.models import Booking, Service
from accounts.models import Business


class Command(BaseCommand):
    help = "Create a couple of past bookings for testing."

    def add_arguments(self, parser):
        parser.add_argument("--org", dest="org", help="Organization slug to create bookings for", required=False)
        parser.add_argument("--count", dest="count", type=int, default=2, help="Number of past bookings to create")

    def handle(self, *args, **options):
        slug = options.get("org")
        count = options.get("count") or 2

        if slug:
            try:
                org = Business.objects.get(slug=slug)
            except Business.DoesNotExist:
                raise CommandError(f"Organization with slug '{slug}' not found")
        else:
            org = Business.objects.first()
            if not org:
                raise CommandError("No organizations found in the database. Create one first or pass --org slug")

        now = timezone.now()
        created = []

        # Try to pick a service for the organization if available
        svc = Service.objects.filter(organization=org).first()

        for i in range(count):
            # space them out: 30 and 7 days ago (or 7*i)
            days_ago = 30 if i == 0 else 7 * i
            start = now - timedelta(days=days_ago, hours=2)
            end = start + timedelta(hours=1)

            b = Booking.objects.create(
                organization=org,
                title=f"Past test booking {i+1}",
                start=start,
                end=end,
                client_name=f"Test Client {i+1}",
                client_email=f"test{i+1}@example.com",
                service=svc,
            )
            created.append(b)

        self.stdout.write(self.style.SUCCESS(f"Created {len(created)} past bookings for organization '{org.slug}'"))
        for b in created:
            self.stdout.write(f"- id={b.id} start={b.start} end={b.end} client={b.client_name}")
