import os, traceback
os.environ.setdefault('DJANGO_SETTINGS_MODULE','circlecalproject.settings_prod')
import django
django.setup()
from bookings.models import Booking
from bookings.emails import send_booking_confirmation

b = Booking.objects.filter(client_email__isnull=False).exclude(client_email='').order_by('-created_at').first()
if not b:
    print('No booking with client_email found.')
else:
    print('Found booking id=', b.id, 'email=', b.client_email)
    try:
        ok = send_booking_confirmation(b)
        print('send_booking_confirmation returned:', ok)
    except Exception:
        traceback.print_exc()
