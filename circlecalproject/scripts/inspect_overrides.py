import os
import sys
from datetime import datetime

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
from accounts.models import Business

org_slug = None
# Try to infer org slug from working DB (pick first business)
try:
    org = Business.objects.first()
    org_slug = org.slug if org else None
except Exception as e:
    print('Failed to fetch organization:', e)
    org_slug = None

print('Org slug inferred:', org_slug)

qs = Booking.objects.filter(service__isnull=True).order_by('-created_at')[:200]
print(f'Found {qs.count()} per-date override bookings (latest 200 shown):')

for bk in qs:
    assigned_user = getattr(bk.assigned_user, 'id', None) if bk.assigned_user else None
    assigned_user_repr = f"User(id={assigned_user})" if assigned_user else 'None'
    svc_marker = bk.client_name or ''
    print('---')
    print('id:', bk.id, 'org:', getattr(bk.organization, 'slug', None))
    print('start:', bk.start, 'end:', bk.end, 'created_at:', bk.created_at, 'is_blocking:', bk.is_blocking)
    print('client_name:', repr(svc_marker))
    print('assigned_user:', assigned_user_repr)
    print('service:', getattr(bk.service, 'id', None))

print('\nDone')
