#!/usr/bin/env python
import os, sys, pathlib
BASE_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'circlecalproject.settings_prod')
import django
django.setup()
from django.utils import timezone
from bookings.models import Booking
from bookings.emails import send_booking_confirmation

now = timezone.localtime(timezone.now())
today = now.date()
print('Today (local):', today)
qs = Booking.objects.filter(service__slug='beisbol', start__date=today, start__hour=10)
print('Matching bookings count:', qs.count())
if qs.exists():
    b = qs.first()
    print('Found booking:', b.id, 'client:', b.client_name, b.client_email, 'start:', b.start, 'end:', b.end)
    try:
        sent = send_booking_confirmation(b)
        print('send_booking_confirmation returned', sent)
    except Exception as e:
        print('Error sending confirmation:', e)
else:
    print('No booking found for today 10:00 with service slug beisbol')
