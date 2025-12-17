#!/usr/bin/env python
import os, sys, pathlib
BASE_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'circlecalproject.settings_prod')
import django
django.setup()
from bookings.models import Booking
from bookings.emails import send_booking_confirmation

public_ref = 'U2XW6ULS'
print('Looking up booking with public_ref:', public_ref)
# Try exact case-insensitive match first
b = Booking.objects.filter(public_ref__iexact=public_ref).first()
if not b:
    # try contains
    b = Booking.objects.filter(public_ref__icontains=public_ref).first()

if not b:
    print('Booking not found with public_ref in active bookings.')
    # Optionally, search audit entries
    try:
        from bookings.models import AuditBooking
        ab = AuditBooking.objects.filter(booking_snapshot__public_ref__iexact=public_ref).order_by('-created_at').first()
        if ab:
            print('Found audit entry for deleted booking (cannot resend confirmation for deleted booking).')
            print('Audit id:', ab.id)
        else:
            print('No audit entry found for that public_ref.')
    except Exception:
        print('Audit lookup failed or not available.')
else:
    print('Found booking id:', b.id, 'client:', b.client_name, b.client_email, 'start:', b.start)
    try:
        sent = send_booking_confirmation(b)
        print('send_booking_confirmation returned', sent)
    except Exception as e:
        print('Error sending confirmation:', e)
