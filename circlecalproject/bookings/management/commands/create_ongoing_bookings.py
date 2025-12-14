from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from datetime import timedelta

from bookings.models import Booking, Service
from accounts.models import Business


class Command(BaseCommand):
    help = "Create ongoing bookings (start shortly before now and end shortly after) for testing."

    def add_arguments(self, parser):
        parser.add_argument("--org", dest="org", help="Organization slug to create bookings for", required=False)
        parser.add_argument("--count", dest="count", type=int, default=1, help="Number of ongoing bookings to create")

    def handle(self, *args, **options):
        slug = options.get("org")
        count = options.get("count") or 1

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

        svc = Service.objects.filter(organization=org).first()

        for i in range(count):
            # ongoing: started 15 minutes ago, ends in 45 minutes (1 hour total)
            start = now - timedelta(minutes=15 + i)  # small offset per booking
            end = now + timedelta(minutes=45 + i)

            b = Booking.objects.create(
                organization=org,
                title=f"Ongoing test booking {i+1}",
                start=start,
                end=end,
                client_name=f"Live Client {i+1}",
                client_email=f"live{i+1}@example.com",
                service=svc,
            )
            created.append(b)

        self.stdout.write(self.style.SUCCESS(f"Created {len(created)} ongoing bookings for organization '{org.slug}'"))
        for b in created:
            self.stdout.write(f"- id={b.id} start={b.start} end={b.end} client={b.client_name}")
