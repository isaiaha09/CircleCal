import os
import sys
from datetime import datetime, time

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'circlecalproject.settings_prod')
# ensure project path
sys.path.insert(0, r'D:\CircleCalBackup\circlecalproject')
import django
django.setup()
from bookings.models import Service, Booking
from accounts.models import Business
from zoneinfo import ZoneInfo
from django.utils import timezone

org = Business.objects.filter(slug='beisbol').first()
if not org:
    print('Org beisbol not found')
    sys.exit(1)

svc = Service.objects.filter(organization=org, name__icontains='2 hour diddy').first()
if not svc:
    print('Service with name containing "2 hour diddy" not found. Listing services:')
    for s in Service.objects.filter(organization=org):
        print(s.id, s.name, s.slug)
    sys.exit(1)

print('Found service:', svc.id, svc.name)
# compute aware datetimes in org timezone
org_tz = ZoneInfo(getattr(org, 'timezone', getattr(os.environ, 'TZ', 'UTC')))
start_dt = datetime(2025, 12, 29, 9, 0, tzinfo=org_tz)
end_dt = datetime(2025, 12, 29, 17, 0, tzinfo=org_tz)
# ensure no duplicate
exists = Booking.objects.filter(organization=org, start=start_dt, end=end_dt, service__isnull=True).exists()
if exists:
    print('Duplicate exists, aborting')
    sys.exit(0)

bk = Booking.objects.create(
    organization=org,
    title='Per-date available override',
    start=start_dt,
    end=end_dt,
    client_name=f'scope:svc:{svc.id}',
    client_email='',
    is_blocking=False,
    service=None
)
print('Created booking id', bk.id)
