import os, django, sys
from datetime import datetime, timedelta

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'circlecalproject.settings')
django.setup()

from accounts.models import Business as Organization
from bookings.models import Service, WeeklyAvailability, Booking
from billing.utils import get_subscription
from zoneinfo import ZoneInfo
from django.utils import timezone

# Accept org/service as args
if len(sys.argv) < 3:
    print('Usage: python inspect_avail.py <org_slug> <service_slug>')
    sys.exit(2)
org_slug = sys.argv[1]
svc_slug = sys.argv[2]

org = Organization.objects.get(slug=org_slug)
svc = Service.objects.get(slug=svc_slug, organization=org)

print('Org:', org.slug)
print('Service:', svc.slug, 'duration', svc.duration, 'buffer_before', svc.buffer_before, 'buffer_after', svc.buffer_after)

# Build start/end for today in org tz
now_org = timezone.now().astimezone(ZoneInfo(getattr(org, 'timezone', 'UTC')))
start = now_org.replace(hour=0, minute=0, second=0, microsecond=0)
end = now_org.replace(hour=23, minute=59, second=0, microsecond=0)
print('Org now:', now_org.isoformat())
print('range start:', start.isoformat(), 'end:', end.isoformat())

# Compute earliest and latest
earliest_allowed = now_org + timedelta(hours=svc.min_notice_hours)
latest_allowed = now_org + timedelta(days=svc.max_booking_days)
print('earliest_allowed:', earliest_allowed.isoformat())
print('latest_allowed:', latest_allowed.isoformat())
# trial cap
sub = get_subscription(org)
print('subscription:', getattr(sub, 'status', None))

# Get per-date overrides overlapping today
day_start_candidate = start
day_end_candidate = end
overrides = Booking.objects.filter(organization=org, service__isnull=True, start__lt=day_end_candidate, end__gt=day_start_candidate)
print('overrides count:', overrides.count())
for bk in overrides:
    print('  override:', bk.start, bk.end, 'is_blocking', bk.is_blocking)

# Build base windows
weekday = start.weekday()
print('weekday', weekday)
svc_rows = svc.weekly_availability.filter(is_active=True, weekday=weekday)
if svc_rows.exists():
    base_windows = []
    for w in svc_rows.order_by('start_time'):
        w_start = start.replace(hour=w.start_time.hour, minute=w.start_time.minute, second=0, microsecond=0)
        w_end = start.replace(hour=w.end_time.hour, minute=w.end_time.minute, second=0, microsecond=0)
        base_windows.append((w_start, w_end))
else:
    weekly_rows = WeeklyAvailability.objects.filter(organization=org, is_active=True, weekday=weekday)
    base_windows = []
    for w in weekly_rows:
        w_start = start.replace(hour=w.start_time.hour, minute=w.start_time.minute, second=0, microsecond=0)
        w_end = start.replace(hour=w.end_time.hour, minute=w.end_time.minute, second=0, microsecond=0)
        base_windows.append((w_start, w_end))

print('base_windows:')
for a,b in base_windows:
    print(' ', a.isoformat(), '->', b.isoformat())

# Determine slot_increment
slot_inc_minutes = None
try:
    slot_inc_minutes = getattr(org.settings, 'block_size', None)
except Exception:
    slot_inc_minutes = None
if not slot_inc_minutes or slot_inc_minutes <= 0:
    slot_inc_minutes = svc.duration if svc.duration > 0 else 15
print('slot_inc_minutes:', slot_inc_minutes)

# Build busy bookings for day
day_start = start
day_end = end
existing = Booking.objects.filter(organization=org, start__lt=day_end, end__gt=day_start).exclude(service__isnull=True)
busy = [(bk.start.astimezone(ZoneInfo(getattr(org, 'timezone', 'UTC'))), bk.end.astimezone(ZoneInfo(getattr(org, 'timezone', 'UTC')))) for bk in existing]
print('busy count:', len(busy))
for bs,be in busy:
    print(' busy', bs.isoformat(), '->', be.isoformat())

# Now simulate slot generation
from datetime import timedelta

duration = timedelta(minutes=svc.duration)
buffer_before = timedelta(minutes=svc.buffer_before)
buffer_after = timedelta(minutes=svc.buffer_after)
total_length = duration
slot_increment = timedelta(minutes=slot_inc_minutes)

available_slots = []
for win_start, win_end in base_windows:
    if win_end <= start or win_start >= end:
        continue
    window_end = min(win_end, end, latest_allowed)
    if window_end - win_start < total_length:
        continue
    slot_start = win_start.replace(second=0, microsecond=0)
    while slot_start + total_length <= window_end:
        slot_end = slot_start + total_length
        # availability enforcement
        # here we'll just call the is_within_availability helper via importing
        from bookings.views import is_within_availability
        if not is_within_availability(org, slot_start, slot_end, svc):
            slot_start += slot_increment
            continue
        is_same_day = slot_start.date() == now_org.date()
        if is_same_day and slot_start < earliest_allowed:
            slot_start += slot_increment
            continue
        # conflicts
        conflict = False
        proposed_start = (slot_start - buffer_before).astimezone(ZoneInfo(getattr(org, 'timezone', 'UTC')))
        proposed_end = (slot_end + buffer_after).astimezone(ZoneInfo(getattr(org, 'timezone', 'UTC')))
        for booked_start, booked_end in busy:
            if proposed_start < booked_end and proposed_end > booked_start:
                conflict = True
                break
        if not conflict:
            available_slots.append({'start': slot_start.isoformat(), 'end': slot_end.isoformat()})
        slot_start += slot_increment

print('available_slots_count:', len(available_slots))
for s in available_slots[:50]:
    print(' ', s)

print('done')
