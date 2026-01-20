import os
import django
import smtplib
from email.message import EmailMessage as StdEmailMessage
from django.core.signing import TimestampSigner
from zoneinfo import ZoneInfo
from django.utils import timezone

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'circlecalproject.settings')
django.setup()

from bookings.models import Booking
from django.template.loader import render_to_string
from django.conf import settings

PUBLIC_REF = 'NLLKNL0L'
OLD_BOOKING_ID = None

b = Booking.objects.filter(public_ref__iexact=PUBLIC_REF).first()
if not b:
    print('Booking not found:', PUBLIC_REF)
    raise SystemExit(1)

# Build context similar to send_booking_rescheduled
old_booking_display = None

try:
    signer = TimestampSigner()
    token = signer.sign(str(b.id))
    cancel_path = f"/cancel/{b.id}/"
    reschedule_path = f"/reschedule/{b.id}/"
    base_url = getattr(settings, 'SITE_URL', 'https://circlecal.app')
    cancel_url = f"{base_url}{cancel_path}?token={token}"
    reschedule_url = cancel_url
except Exception:
    cancel_url = ''
    reschedule_url = ''

try:
    org_tz_name = getattr(b.organization, 'timezone', getattr(settings, 'TIME_ZONE', 'UTC'))
    org_tz = ZoneInfo(org_tz_name)
except Exception:
    org_tz = timezone.get_current_timezone()

try:
    start_local = b.start.astimezone(org_tz)
    end_local = b.end.astimezone(org_tz)
    start_display = start_local.strftime('%A, %B %d, %Y at %I:%M %p')
    end_display = end_local.strftime('%A, %B %d, %Y at %I:%M %p')
except Exception:
    start_display = ''
    end_display = ''

try:
    if b.service and getattr(b.service, 'duration', None) is not None:
        duration_minutes = int(b.service.duration)
    else:
        duration_minutes = int((b.end - b.start).total_seconds() / 60)
except Exception:
    duration_minutes = None

context = {
    'booking': b,
    'public_ref': getattr(b, 'public_ref', None),
    'site_url': getattr(settings, 'SITE_URL', 'https://circlecal.app'),
    'old_booking_id': OLD_BOOKING_ID,
    'old_booking_display': old_booking_display,
    'start_display': start_display,
    'end_display': end_display,
    'duration_minutes': duration_minutes,
    'cancel_url': cancel_url,
    'reschedule_url': reschedule_url,
}

html_content = render_to_string('bookings/emails/booking_rescheduled.html', context)
subject = f"Booking Rescheduled - {b.organization.name}"
from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@circlecal.app')
recipient = b.client_email

msg = StdEmailMessage()
msg['Subject'] = subject
msg['From'] = from_email
msg['To'] = recipient
msg.set_content('This is an HTML email. Please view in an HTML capable client.')
msg.add_alternative(html_content, subtype='html')
msg['X-CircleCal-Booking-ID'] = str(b.id)

# SMTP creds from environment (settings.load_dotenv has been run by Django settings)
smtp_host = os.getenv('EMAIL_HOST', 'smtp-relay.brevo.com')
smtp_port = int(os.getenv('EMAIL_PORT', 587))
smtp_user = os.getenv('BREVO_SMTP_USER') or os.getenv('EMAIL_HOST_USER')
smtp_pass = os.getenv('BREVO_SMTP_PASSWORD') or os.getenv('BREVO_SMTP_PASSWORD') or os.getenv('BREVO_SMTP_PASSWORD')

if not smtp_user or not smtp_pass:
    print('SMTP credentials not found in environment.')
    raise SystemExit(1)

print('Sending via', smtp_host, smtp_port, 'from', from_email, 'to', recipient)
try:
    with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(smtp_user, smtp_pass)
        server.send_message(msg)
    print('Send OK')
except Exception as e:
    print('Send failed:', type(e), e)
    raise
