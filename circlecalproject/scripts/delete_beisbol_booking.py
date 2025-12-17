#!/usr/bin/env python
import os, sys, pathlib
# Ensure project root is on sys.path so settings package can be imported
BASE_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'circlecalproject.settings_prod')
import django
django.setup()
from django.utils import timezone
from bookings.models import Booking
from django.db import transaction

now = timezone.localtime(timezone.now())
today = now.date()
print('Today (local):', today)
qs = Booking.objects.filter(service__slug='beisbol', start__date=today, start__hour=10, end__hour=11)
print('Matching bookings count:', qs.count())
for b in qs:
    print('Found booking:', b.id, 'client:', b.client_name, b.client_email, 'start:', b.start, 'end:', b.end)
    try:
        with transaction.atomic():
            b.delete()
        print('Deleted booking id', b.id)
    except Exception as e:
        print('Failed to delete booking', b.id, 'error:', e)
print('Done')
