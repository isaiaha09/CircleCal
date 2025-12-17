import sys,os
sys.path.insert(0, os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE','circlecalproject.settings')
import django
django.setup()
from bookings.models import Booking
from django.template.loader import render_to_string
from django.conf import settings
from django.core.signing import TimestampSigner
from zoneinfo import ZoneInfo
from django.utils import timezone
import smtplib
from email.message import EmailMessage as StdEmailMessage

b = Booking.objects.filter(public_ref__iexact='NLLKNL0L').first()
print('booking', b.id if b else None)

signer = TimestampSigner()
token = signer.sign(str(b.id))
base_url = getattr(settings, 'SITE_URL', 'http://127.0.0.1:8000')
cancel_url = f"{base_url}/cancel/{b.id}/?token={token}"

org_tz = ZoneInfo(getattr(b.organization, 'timezone', getattr(settings, 'TIME_ZONE', 'UTC')))
start_local = b.start.astimezone(org_tz)
start_display = start_local.strftime('%A, %B %d, %Y at %I:%M %p')

context = {
    'booking': b,
    'public_ref': getattr(b, 'public_ref', None),
    'site_url': getattr(settings, 'SITE_URL', 'http://127.0.0.1:8000'),
    'old_booking_id': None,
    'old_booking_display': None,
    'start_display': start_display,
    'end_display': '',
    'duration_minutes': int((b.end - b.start).total_seconds() / 60),
    'cancel_url': cancel_url,
    'reschedule_url': cancel_url,
}
html = render_to_string('bookings/emails/booking_rescheduled.html', context)
subject = f"Booking Rescheduled - {b.organization.name}"
from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@circlecal.app')
recipient = b.client_email

msg = StdEmailMessage()
msg['Subject'] = subject
msg['From'] = from_email
msg['To'] = recipient
msg.set_content('HTML email')
msg.add_alternative(html, subtype='html')
msg['X-CircleCal-Booking-ID'] = str(b.id)

smtp_host = os.getenv('EMAIL_HOST', 'smtp-relay.brevo.com')
smtp_port = int(os.getenv('EMAIL_PORT', 587))
smtp_user = os.getenv('BREVO_SMTP_USER')
smtp_pass = os.getenv('BREVO_SMTP_PASSWORD')
print('SMTP', smtp_host, smtp_port, smtp_user is not None)

try:
    with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as s:
        s.ehlo()
        s.starttls()
        s.ehlo()
        s.login(smtp_user, smtp_pass)
        s.send_message(msg)
        print('sent')
except Exception as e:
    print('send failed', type(e), e)
    raise
