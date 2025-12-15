import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE','circlecalproject.settings_prod')
import django
django.setup()
from bookings.models import Booking
from django.core.signing import TimestampSigner
from django.urls import reverse
from django.conf import settings

b = Booking.objects.filter(client_email__isnull=False).exclude(client_email='').order_by('-created_at').first()
if not b:
    print('No booking found')
else:
    signer = TimestampSigner()
    token = signer.sign(str(b.id))
    cancel_path = reverse('bookings:cancel_booking', args=[b.id])
    base = getattr(settings, 'SITE_URL', 'http://localhost:8000')
    print('Cancel URL:')
    print(f"{base}{cancel_path}?token={token}")
