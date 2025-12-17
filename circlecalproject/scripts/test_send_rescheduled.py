#!/usr/bin/env python
import os, sys, pathlib
BASE_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'circlecalproject.settings_prod')
import django
django.setup()
from bookings.models import Booking
from bookings.emails import send_booking_rescheduled

import sys
public_ref = sys.argv[1] if len(sys.argv) > 1 else 'TK2ONFSM'
print('Looking up booking with public_ref:', public_ref)
# Try exact case-insensitive match first
b = Booking.objects.filter(public_ref__iexact=public_ref).first()
if not b:
    b = Booking.objects.filter(public_ref__icontains=public_ref).first()

if not b:
    print('Booking not found with public_ref:', public_ref)
else:
    print('Found booking id:', b.id, 'client:', b.client_name, b.client_email, 'start:', b.start)
    try:
        sent = send_booking_rescheduled(b, old_booking_id=106)
        print('send_booking_rescheduled returned', sent)
    except Exception as e:
        print('Error sending rescheduled email:', e)
