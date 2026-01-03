from django.core.management.base import BaseCommand
import datetime
import json


class Command(BaseCommand):
    help = 'Seed a lightweight org/service for E2E tests or delete by slug'

    def add_arguments(self, parser):
        parser.add_argument('--delete', dest='delete', help='Delete organization by slug', default=None)

    def handle(self, *args, **options):
        from accounts.models import Business as Organization
        from bookings.models import Service, WeeklyAvailability, ServiceWeeklyAvailability

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

        # Ensure availability exists for today and tomorrow so tests running at any time of day.
        # NOTE: Unassigned services (no assignees) are treated as explicitly-scoped and therefore
        # require per-service weekly availability rows; org weekly availability alone is not enough.
        today = datetime.date.today()
        wd_today = today.weekday()
        wd_tomorrow = (today + datetime.timedelta(days=1)).weekday()
        WeeklyAvailability.objects.get_or_create(organization=org, weekday=wd_today, defaults={'start_time': '08:00', 'end_time': '18:00'})
        WeeklyAvailability.objects.get_or_create(organization=org, weekday=wd_tomorrow, defaults={'start_time': '08:00', 'end_time': '18:00'})

        ServiceWeeklyAvailability.objects.get_or_create(service=svc, weekday=wd_today, defaults={'start_time': '08:00', 'end_time': '18:00'})
        ServiceWeeklyAvailability.objects.get_or_create(service=svc, weekday=wd_tomorrow, defaults={'start_time': '08:00', 'end_time': '18:00'})

        self.stdout.write(json.dumps({'org_slug': org.slug, 'service_slug': svc.slug}))
