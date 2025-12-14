from django.core.mail import EmailMessage
from django.template.loader import render_to_string
from django.conf import settings
from django.utils import timezone
from zoneinfo import ZoneInfo
from django.core.signing import TimestampSigner
from django.urls import reverse


def send_booking_confirmation(booking):
    """Send booking confirmation email to client."""
    if not booking.client_email:
        return False
    
    signer = TimestampSigner()
    token = signer.sign(str(booking.id))
    cancel_path = reverse('bookings:cancel_booking', args=[booking.id])
    base_url = getattr(settings, 'SITE_URL', 'http://localhost:8000')
    cancel_url = f"{base_url}{cancel_path}?token={token}"
    context = {
        'booking': booking,
        'public_ref': getattr(booking, 'public_ref', None),
        'site_url': getattr(settings, 'SITE_URL', 'http://localhost:8000'),
        'cancel_url': cancel_url,
    }
    
    subject = f"Booking Confirmed - {booking.organization.name}"
    html_content = render_to_string('bookings/emails/booking_confirmation.html', context)
    from_email = settings.DEFAULT_FROM_EMAIL
    recipient_list = [booking.client_email]
    msg = EmailMessage(subject, html_content, from_email, recipient_list)
    msg.content_subtype = "html"  # Send HTML-only (no plain text fallback)
    
    try:
        msg.send()
        return True
    except Exception as e:
        # Log error but don't raise - booking should still succeed
        print(f"Failed to send booking confirmation: {e}")
        return False


def send_booking_cancellation(booking, refund_info=None):
    """Send booking cancellation email to client. Accept optional refund_info string."""
    if not booking.client_email:
        return False
    
    context = {
        'booking': booking,
        'public_ref': getattr(booking, 'public_ref', None),
        'site_url': getattr(settings, 'SITE_URL', 'http://localhost:8000'),
        'refund_info': refund_info,
    }
    
    subject = f"Booking Cancelled - {booking.organization.name}"
    html_content = render_to_string('bookings/emails/booking_cancellation.html', context)
    from_email = settings.DEFAULT_FROM_EMAIL
    recipient_list = [booking.client_email]
    msg = EmailMessage(subject, html_content, from_email, recipient_list)
    msg.content_subtype = "html"
    
    try:
        msg.send()
        return True
    except Exception as e:
        print(f"Failed to send booking cancellation: {e}")
        return False


def send_booking_reminder(booking):
    """Send booking reminder email to client (typically 24h before)."""
    if not booking.client_email:
        return False
    
    context = {
        'booking': booking,
        'site_url': getattr(settings, 'SITE_URL', 'http://localhost:8000'),
    }
    
    subject = f"Reminder: Upcoming Booking - {booking.organization.name}"
    html_content = render_to_string('bookings/emails/booking_reminder.html', context)
    from_email = settings.DEFAULT_FROM_EMAIL
    recipient_list = [booking.client_email]
    msg = EmailMessage(subject, html_content, from_email, recipient_list)
    msg.content_subtype = "html"
    
    try:
        msg.send()
        return True
    except Exception as e:
        print(f"Failed to send booking reminder: {e}")
        return False

def send_owner_booking_notification(booking):
    """Send a styled HTML notification to the business owner for a new booking."""
    org = booking.organization
    if not getattr(org, "owner", None) or not org.owner.email:
        return

    try:
        org_tz_name = getattr(org, 'timezone', getattr(settings, 'TIME_ZONE', 'UTC'))
        org_tz = ZoneInfo(org_tz_name)
    except Exception:
        org_tz = timezone.get_current_timezone()
        org_tz_name = str(org_tz)

    start_local = booking.start.astimezone(org_tz)
    end_local = booking.end.astimezone(org_tz)

    subject = f"New booking: {booking.title}"
    context = {
        'booking': booking,
        'org': org,
        'service': booking.service,
        'client_name': booking.client_name,
        'client_email': booking.client_email,
        'start_local': start_local,
        'end_local': end_local,
        'org_tz_name': org_tz_name,
        'site_url': getattr(settings, 'SITE_URL', ''),
    }

    html_content = render_to_string('bookings/emails/booking_owner_notification.html', context)
    from_email = settings.DEFAULT_FROM_EMAIL
    recipient_list = [org.owner.email]
    msg = EmailMessage(subject, html_content, from_email, recipient_list)
    msg.content_subtype = "html"
    msg.send(fail_silently=True)
