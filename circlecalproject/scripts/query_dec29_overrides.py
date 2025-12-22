import os
import sys
from datetime import datetime, time

# Try settings_prod first then fallback
for settings_mod in ("circlecalproject.settings_prod", "circlecalproject.settings"):
    try:
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', settings_mod)
        import django
        django.setup()
        print(f"Using settings: {settings_mod}")
        break
    except Exception as e:
        print(f"Failed to use settings {settings_mod}: {e}")
else:
    print("Could not set up Django with known settings modules.")
    sys.exit(1)

from bookings.models import Booking
from accounts.models import Business, Membership
from zoneinfo import ZoneInfo
from django.conf import settings

# Target date/time window to search
TARGET_DATE = datetime(2025, 12, 29).date()
START_T = time(9, 0)
END_T = time(17, 0)

print('Searching for per-date overrides overlapping', TARGET_DATE, START_T, '->', END_T)

# We'll search across all orgs; show bookings where service is NULL (per-date overrides)
qs = Booking.objects.filter(service__isnull=True)

found = []
for bk in qs:
    try:
        # Convert start/end to date and time in their timezone-aware fields (they are stored with tzinfo)
        s = bk.start
        e = bk.end
        # Compare date equality and overlap with window in UTC naive terms
        # Simpler: check if bk.start.date() == TARGET_DATE or overlap
        s_date = s.date()
        e_date = e.date()
        overlaps = False
        # If the booking spans days, check if TARGET_DATE falls within [s.date(), e.date()]
        if s_date <= TARGET_DATE <= e_date:
            # Further check times if it's the same day; otherwise treat as overlapping
            if s_date == TARGET_DATE and e_date == TARGET_DATE:
                # Both on same day: check time overlap
                if not (e.time() <= START_T or s.time() >= END_T):
                    overlaps = True
            else:
                overlaps = True
        if overlaps:
            found.append(bk)
    except Exception:
        continue

print(f'Found {len(found)} matching per-date override bookings overlapping {TARGET_DATE}')
for bk in found:
    assigned_user = getattr(bk.assigned_user, 'id', None) if bk.assigned_user else None
    print('---')
    print('id:', bk.id)
    print('org:', getattr(bk.organization, 'slug', None))
    print('start:', bk.start)
    print('end:', bk.end)
    print('created_at:', bk.created_at)
    print('is_blocking:', bk.is_blocking)
    print('client_name:', repr(bk.client_name))
    print('assigned_user:', assigned_user)
    print('service:', getattr(bk.service, 'id', None))

# Also print last 20 per-date override creations to inspect unexpected rows
print('\nLast 20 per-date overrides by created_at:')
for bk in Booking.objects.filter(service__isnull=True).order_by('-created_at')[:20]:
    print(f"id={bk.id} created_at={bk.created_at} client_name={repr(bk.client_name)} org={getattr(bk.organization,'slug',None)} start={bk.start} assigned_user={getattr(bk.assigned_user,'id',None)}")

print('\nDone')

# Also check any booking that starts on TARGET_DATE (any service)
print('\nChecking any bookings that start on target date (any service):')
any_bks = Booking.objects.filter(start__date=TARGET_DATE).order_by('-created_at')
print(f'Found {any_bks.count()} bookings starting on {TARGET_DATE}:')
for bk in any_bks:
    print(f'id={bk.id} org={getattr(bk.organization,"slug",None)} start={bk.start} service={getattr(bk.service,"id",None)} client_name={repr(bk.client_name)} assigned_user={getattr(bk.assigned_user,"id",None)} created_at={bk.created_at}')

print('\nFinished checks')

# Show details for service id 123 (referenced by scope:svc:123) if present
from bookings.models import Service
svc = Service.objects.filter(id=123).first()
if svc:
    print(f"\nService 123 -> id={svc.id} name={svc.name} slug={svc.slug} org={getattr(svc.organization,'slug',None)}")
else:
    print('\nService with id=123 not found')

# List memberships for the inferred org (if available)
try:
    org = Business.objects.filter().first()
    if org:
        from accounts.models import Membership
        mems = Membership.objects.filter(organization=org)
        print(f"\nMemberships for org {org.slug} (count={mems.count()}):")
        for m in mems[:50]:
            print(f"id={m.id} user_id={getattr(m.user,'id',None)} email={getattr(m.user,'email',None)} name={getattr(m.user,'first_name',None)} {getattr(m.user,'last_name',None)}")
except Exception:
    pass
