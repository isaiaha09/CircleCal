import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'circlecalproject.settings')
django.setup()

from bookings.models import Booking
from bookings.emails import send_booking_confirmation, send_booking_rescheduled

b = Booking.objects.filter(public_ref__iexact='NLLKNL0L').first()
print('booking:', getattr(b,'id',None), getattr(b,'public_ref',None), getattr(b,'client_email',None))
if b:
    setattr(b, '_suppress_confirmation', True)
    print('send_booking_confirmation returned:', send_booking_confirmation(b))
    print('send_booking_rescheduled returned:', send_booking_rescheduled(b, old_booking_id=117))
else:
    print('no booking found')
