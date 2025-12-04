from django.core.management.base import BaseCommand
import datetime
import json


class Command(BaseCommand):
    help = 'Seed a lightweight org/service for E2E tests or delete by slug'

    def add_arguments(self, parser):
        parser.add_argument('--delete', dest='delete', help='Delete organization by slug', default=None)

    def handle(self, *args, **options):
        from accounts.models import Business as Organization
        from bookings.models import Service, WeeklyAvailability

        if options.get('delete'):
            slug = options.get('delete')
            qs = Organization.objects.filter(slug=slug)
            count = qs.count()
            qs.delete()
            self.stdout.write(json.dumps({'deleted': slug, 'count': count}))
            return

        sfx = str(int(datetime.datetime.utcnow().timestamp()))
        org_slug = f'pw-e2e-{sfx}'
        svc_slug = org_slug + '-svc'

        org, _ = Organization.objects.get_or_create(slug=org_slug, defaults={'name': 'PW E2E Org ' + sfx})
        svc, _ = Service.objects.get_or_create(organization=org, slug=svc_slug, defaults={'name': 'PW E2E Service', 'duration': 30, 'price': 0})

        # Ensure availability exists for today and tomorrow so tests running at any time of day
        today = datetime.date.today()
        wd_today = today.weekday()
        wd_tomorrow = (today + datetime.timedelta(days=1)).weekday()
        WeeklyAvailability.objects.get_or_create(organization=org, weekday=wd_today, defaults={'start_time': '08:00', 'end_time': '18:00'})
        WeeklyAvailability.objects.get_or_create(organization=org, weekday=wd_tomorrow, defaults={'start_time': '08:00', 'end_time': '18:00'})

        # Also create a short per-date availability override for today in the near future
        # so E2E tests running late can still find at least one slot on the current day.
        from django.utils import timezone
        from bookings.models import Booking
        now = timezone.now()
        # Respect service.min_notice_hours when choosing the override start
        start_offset = int(getattr(svc, 'min_notice_hours', 1)) + 1
        start_dt = now + datetime.timedelta(hours=start_offset)
        # Round to next 10 minutes for nicer slot alignment
        minute = (start_dt.minute // 10 + 1) * 10
        if minute >= 60:
            start_dt = start_dt.replace(hour=(start_dt.hour + 1) % 24, minute=0, second=0, microsecond=0)
        else:
            start_dt = start_dt.replace(minute=minute, second=0, microsecond=0)
        end_dt = start_dt + datetime.timedelta(minutes=getattr(svc, 'duration', 30))
        # Only create if not already present for this org on same start
        Booking.objects.get_or_create(
            organization=org,
            start=start_dt,
            end=end_dt,
            service=None,
            defaults={'title': 'E2E available override', 'client_name': '', 'client_email': '', 'is_blocking': False}
        )

        self.stdout.write(json.dumps({'org_slug': org.slug, 'service_slug': svc.slug}))
