import logging
from urllib.parse import quote
import re
from django.core.mail import EmailMessage
from django.template.loader import render_to_string
from django.conf import settings
from django.utils import timezone
from zoneinfo import ZoneInfo
from django.core.signing import TimestampSigner
from django.urls import reverse
from bookings.models import build_offline_payment_instructions, filter_offline_payment_instructions_for_method


def _extract_offline_line_for_method(instructions: str, method: str) -> str:
    """Extract a method-specific line from org offline instructions.

    Looks for lines like:
      "Venmo: @myhandle" or "Zelle - myemail@example.com"
    """
    try:
        full = (instructions or '').strip()
        m = (method or '').strip().lower()
        if not full or not m:
            return ''
        for raw in full.splitlines():
            line = (raw or '').strip()
            if not line:
                continue
            low = line.lower()
            if not low.startswith(m):
                continue
            rest = line[len(m):].lstrip()
            if rest.startswith(':') or rest.startswith('-'):
                rest = rest[1:].lstrip()
            return rest.strip()
    except Exception:
        return ''
    return ''


def _build_offline_qr_url(instructions: str, method: str) -> str:
    """Build a QR image URL for Venmo/Zelle based on instructions text."""
    try:
        m = (method or '').strip().lower()
        if m not in {'venmo', 'zelle'}:
            return ''
        method_line = _extract_offline_line_for_method(instructions, m)
        # Never fall back to full instructions for QR; it may include other methods.
        if not method_line:
            return ''

        def _zelle_payload(value: str) -> str:
            raw = (value or '').strip()
            if not raw:
                return ''
            # Prefer email -> opens mail client reliably.
            m_email = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", raw, re.I)
            if m_email:
                return f"mailto:{m_email.group(0)}"

            # Otherwise treat as phone -> opens dialer.
            digits = re.sub(r"\D", "", raw)
            if not digits:
                return raw
            if len(digits) == 10:
                digits = "1" + digits
            if len(digits) == 11 and digits.startswith("1"):
                return f"tel:+{digits}"
            return f"tel:+{digits}"

        payload = _zelle_payload(method_line) if m == 'zelle' else f"{m.upper()}: {method_line}"
        if not payload:
            return ''
        return 'https://api.qrserver.com/v1/create-qr-code/?size=180x180&data=' + quote(payload)
    except Exception:
        return ''


def send_booking_confirmation(booking):
    """Send booking confirmation email to client."""
    if not booking.client_email:
        return False
    # Defensive: if booking was marked to suppress confirmation (e.g. part
    # of a reschedule flow), avoid sending the normal confirmation.
    try:
        if getattr(booking, '_suppress_confirmation', False):
            logger = logging.getLogger(__name__)
            logger.info('Skipping booking confirmation due to suppress flag for booking=%s', getattr(booking, 'id', None))
            return False
    except Exception:
        pass
    
    signer = TimestampSigner()
    token = signer.sign(str(booking.id))
    cancel_path = reverse('bookings:cancel_booking', args=[booking.id])
    reschedule_path = reverse('bookings:reschedule_booking', args=[booking.id])
    base_url = getattr(settings, 'SITE_URL', 'http://localhost:8000')
    cancel_url = f"{base_url}{cancel_path}?token={token}"
    # Make the reschedule button in the confirmation email behave like the cancel button
    # so clients must cancel first before rescheduling. Point reschedule_url to cancel_url.
    reschedule_url = cancel_url
    payment_method = (getattr(booking, 'payment_method', '') or '').strip().lower()
    context = {
        'booking': booking,
        'public_ref': getattr(booking, 'public_ref', None),
        'site_url': getattr(settings, 'SITE_URL', 'http://localhost:8000'),
        'cancel_url': cancel_url,
        'reschedule_url': reschedule_url,
        'payment_method': payment_method,
        'stripe_card_brand': '',
        'stripe_card_last4': '',
    }

    # Stripe payment details (safe to show last4; do not include full card data)
    try:
        if payment_method == 'stripe':
            session_id = (getattr(booking, 'stripe_checkout_session_id', '') or '').strip()
            secret = getattr(settings, 'STRIPE_SECRET_KEY', None)
            if session_id and secret:
                import stripe  # local import to avoid dependency issues in non-Stripe environments
                stripe.api_key = secret

                org = getattr(booking, 'organization', None)
                acct = None
                try:
                    if org and getattr(org, 'stripe_connect_charges_enabled', False):
                        acct = getattr(org, 'stripe_connect_account_id', None) or None
                except Exception:
                    acct = None

                def _retrieve(fn, *args, **kwargs):
                    if acct:
                        kwargs.setdefault('stripe_account', acct)
                    return fn(*args, **kwargs)

                # Minimal API calls: session -> payment_intent -> latest_charge -> card details
                sess = _retrieve(stripe.checkout.Session.retrieve, session_id, expand=['payment_intent'])
                pi = getattr(sess, 'payment_intent', None) or (sess.get('payment_intent') if isinstance(sess, dict) else None)

                pi_id = None
                if isinstance(pi, str):
                    pi_id = pi
                else:
                    pi_id = getattr(pi, 'id', None) or (pi.get('id') if isinstance(pi, dict) else None)

                latest_charge = None
                charges_list = None
                # Prefer expanded PI's latest_charge when available
                if not isinstance(pi, str) and pi is not None:
                    latest_charge = getattr(pi, 'latest_charge', None) or (pi.get('latest_charge') if isinstance(pi, dict) else None)

                # Fallback: retrieve PI expanded with charges if latest_charge missing
                if not latest_charge and pi_id:
                    try:
                        pi_obj = _retrieve(
                            stripe.PaymentIntent.retrieve,
                            pi_id,
                            expand=['charges.data.payment_method_details']
                        )
                        latest_charge = getattr(pi_obj, 'latest_charge', None) or (pi_obj.get('latest_charge') if isinstance(pi_obj, dict) else None)
                        charges_list = (pi_obj.get('charges') if isinstance(pi_obj, dict) else getattr(pi_obj, 'charges', None))
                    except Exception:
                        latest_charge = None
                        charges_list = None

                charge_obj = None
                if latest_charge:
                    try:
                        charge_obj = _retrieve(stripe.Charge.retrieve, latest_charge)
                    except Exception:
                        charge_obj = None
                elif charges_list:
                    try:
                        data = charges_list.get('data') if isinstance(charges_list, dict) else getattr(charges_list, 'data', None)
                        if data:
                            # pick the last charge as the most recent
                            charge_obj = data[-1]
                    except Exception:
                        charge_obj = None

                if charge_obj:
                    pmd = charge_obj.get('payment_method_details', {}) if isinstance(charge_obj, dict) else (getattr(charge_obj, 'payment_method_details', None) or {})
                    card = (pmd.get('card') if isinstance(pmd, dict) else None) or {}
                    brand = (card.get('brand') or '').strip()
                    last4 = (card.get('last4') or '').strip()
                    if brand:
                        context['stripe_card_brand'] = brand
                    if last4:
                        context['stripe_card_last4'] = last4
    except Exception:
        # Never block sending confirmation email due to Stripe lookup issues
        pass

    # Offline payment details (manual payments outside CircleCal)
    try:
        if payment_method == 'offline':
            org = getattr(booking, 'organization', None)
            org_settings = getattr(org, 'settings', None) if org else None
            offline_method = (getattr(booking, 'offline_payment_method', '') or '').strip().lower()
            full = build_offline_payment_instructions(org_settings) if org_settings else ''
            offline_instructions = filter_offline_payment_instructions_for_method(full, offline_method) if offline_method else full
            context.update({
                'offline_instructions': offline_instructions,
                'offline_method': offline_method,
                'offline_qr_url': _build_offline_qr_url(offline_instructions, offline_method),
            })
    except Exception:
        pass
    
    logger = logging.getLogger(__name__)
    subject = f"Booking Confirmed - {booking.organization.name}"
    html_content = render_to_string('bookings/emails/booking_confirmation.html', context)
    from_email = settings.DEFAULT_FROM_EMAIL
    recipient_list = [booking.client_email]
    msg = EmailMessage(subject, html_content, from_email, recipient_list)
    msg.content_subtype = "html"  # Send HTML-only (no plain text fallback)
    # Add a helpful X-header so provider logs can be correlated with our booking id
    try:
        msg.extra_headers = {**getattr(msg, 'extra_headers', {}), 'X-CircleCal-Booking-ID': str(booking.id)}
    except Exception:
        pass

    try:
        logger.info('Sending booking confirmation for booking=%s to=%s', booking.id, booking.client_email)
        sent = msg.send()
        logger.info('booking confirmation send result for booking=%s sent=%s', booking.id, sent)
        return True
    except Exception as e:
        logger.exception('Failed to send booking confirmation for booking=%s to=%s', booking.id, booking.client_email)
        return False


def send_booking_cancellation(booking, refund_info=None):
    """Send booking cancellation email to client. Accept optional refund_info string."""
    if not booking.client_email:
        return False
    
    signer = TimestampSigner()
    token = signer.sign(str(booking.id))
    reschedule_path = reverse('bookings:reschedule_booking', args=[booking.id])
    base_url = getattr(settings, 'SITE_URL', 'http://localhost:8000')
    reschedule_url = f"{base_url}{reschedule_path}?token={token}"

    context = {
        'booking': booking,
        'public_ref': getattr(booking, 'public_ref', None),
        'site_url': getattr(settings, 'SITE_URL', 'http://localhost:8000'),
        'refund_info': refund_info,
        'reschedule_url': reschedule_url,
    }
    
    logger = logging.getLogger(__name__)
    subject = f"Booking Cancelled - {booking.organization.name}"
    html_content = render_to_string('bookings/emails/booking_cancellation.html', context)
    from_email = settings.DEFAULT_FROM_EMAIL
    recipient_list = [booking.client_email]
    msg = EmailMessage(subject, html_content, from_email, recipient_list)
    msg.content_subtype = "html"
    try:
        msg.extra_headers = {**getattr(msg, 'extra_headers', {}), 'X-CircleCal-Booking-ID': str(booking.id)}
    except Exception:
        pass

    try:
        logger.info('Sending booking cancellation for booking=%s to=%s', booking.id, booking.client_email)
        sent = msg.send()
        logger.info('booking cancellation send result for booking=%s sent=%s', booking.id, sent)
        return True
    except Exception as e:
        logger.exception('Failed to send booking cancellation for booking=%s to=%s', booking.id, booking.client_email)
        return False


def send_booking_reminder(booking):
    """Send booking reminder email to client (typically 24h before)."""
    if not booking.client_email:
        return False
    
    context = {
        'booking': booking,
        'site_url': getattr(settings, 'SITE_URL', 'http://localhost:8000'),
    }
    
    logger = logging.getLogger(__name__)
    subject = f"Reminder: Upcoming Booking - {booking.organization.name}"
    html_content = render_to_string('bookings/emails/booking_reminder.html', context)
    from_email = settings.DEFAULT_FROM_EMAIL
    recipient_list = [booking.client_email]
    msg = EmailMessage(subject, html_content, from_email, recipient_list)
    msg.content_subtype = "html"
    try:
        msg.extra_headers = {**getattr(msg, 'extra_headers', {}), 'X-CircleCal-Booking-ID': str(booking.id)}
    except Exception:
        pass

    try:
        logger.info('Sending booking reminder for booking=%s to=%s', booking.id, booking.client_email)
        sent = msg.send()
        logger.info('booking reminder send result for booking=%s sent=%s', booking.id, sent)
        return True
    except Exception as e:
        logger.exception('Failed to send booking reminder for booking=%s to=%s', booking.id, booking.client_email)
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

    logger = logging.getLogger(__name__)
    html_content = render_to_string('bookings/emails/booking_owner_notification.html', context)
    from_email = settings.DEFAULT_FROM_EMAIL
    recipient_list = [org.owner.email]
    msg = EmailMessage(subject, html_content, from_email, recipient_list)
    msg.content_subtype = "html"
    try:
        msg.extra_headers = {**getattr(msg, 'extra_headers', {}), 'X-CircleCal-Booking-ID': str(booking.id)}
    except Exception:
        pass
    try:
        logger.info('Sending owner notification for booking=%s to=%s', booking.id, org.owner.email)
        msg.send(fail_silently=True)
    except Exception:
        logger.exception('Failed to send owner notification for booking=%s', booking.id)


def send_booking_rescheduled(new_booking, old_booking_id=None):
    """Notify client that their booking was rescheduled. Uses a combined/reschedule template."""
    booking = new_booking
    if not booking.client_email:
        return False

    # Determine a human-friendly identifier for the old booking: prefer public_ref if available
    old_booking_display = None
    try:
        if old_booking_id:
            from .models import AuditBooking, Booking as BookingModel
            try:
                old = BookingModel.objects.filter(id=old_booking_id).first()
                if old and getattr(old, 'public_ref', None):
                    old_booking_display = old.public_ref
                elif old:
                    old_booking_display = str(old.id)
                else:
                    ab = AuditBooking.objects.filter(booking_id=old_booking_id).order_by('-created_at').first()
                    if ab and getattr(ab, 'booking_snapshot', None):
                        snap = ab.booking_snapshot
                        try:
                            # booking_snapshot is typically a dict
                            if isinstance(snap, dict):
                                old_booking_display = snap.get('public_ref') or snap.get('booking_ref') or str(old_booking_id)
                            else:
                                old_booking_display = str(old_booking_id)
                        except Exception:
                            old_booking_display = str(old_booking_id)
            except Exception:
                old_booking_display = str(old_booking_id)
    except Exception:
        old_booking_display = None

    context = {
        'booking': booking,
        'public_ref': getattr(booking, 'public_ref', None),
        'site_url': getattr(settings, 'SITE_URL', 'http://localhost:8000'),
        'old_booking_id': old_booking_id,
        'old_booking_display': old_booking_display,
    }

    # Compute organization-localized display strings for start/end and duration
    try:
        org_tz_name = getattr(booking.organization, 'timezone', getattr(settings, 'TIME_ZONE', 'UTC'))
        org_tz = ZoneInfo(org_tz_name)
    except Exception:
        org_tz = timezone.get_current_timezone()

    try:
        start_local = booking.start.astimezone(org_tz)
        end_local = booking.end.astimezone(org_tz)
        start_display = start_local.strftime('%A, %B %d, %Y at %I:%M %p')
        end_display = end_local.strftime('%A, %B %d, %Y at %I:%M %p')
    except Exception:
        start_display = None
        end_display = None

    # Duration: prefer service.duration, fallback to computation
    try:
        if booking.service and getattr(booking.service, 'duration', None) is not None:
            duration_minutes = int(booking.service.duration)
        else:
            duration_minutes = int((booking.end - booking.start).total_seconds() / 60)
    except Exception:
        duration_minutes = None

    context.update({
        'start_display': start_display,
        'end_display': end_display,
        'duration_minutes': duration_minutes,
    })

    # If we couldn't determine a friendly old booking id earlier, try audit snapshots
    if not old_booking_display and old_booking_id:
        try:
            from .models import AuditBooking
            ab = AuditBooking.objects.filter(booking_id=old_booking_id).order_by('-created_at').first()
            if ab and isinstance(ab.booking_snapshot, dict):
                snap = ab.booking_snapshot
                # prefer explicit public_ref key
                candidate = snap.get('public_ref') or snap.get('booking_ref') or snap.get('publicRef')
                if not candidate:
                    # scan values for plausible public_ref pattern
                    import re
                    for v in snap.values():
                        if isinstance(v, str) and re.match(r'^[0-9A-Z]{6,12}$', v):
                            candidate = v
                            break
                if candidate:
                    old_booking_display = candidate
                    context['old_booking_display'] = old_booking_display
        except Exception:
            pass
    # No deterministic fallback: only present a previous booking id when
    # we can resolve a `public_ref`-style value from the booking or audit snapshot.

    # Add cancel/reschedule links (mirror confirmation email behavior)
    try:
        signer = TimestampSigner()
        token = signer.sign(str(booking.id))
        cancel_path = reverse('bookings:cancel_booking', args=[booking.id])
        reschedule_path = reverse('bookings:reschedule_booking', args=[booking.id])
        base_url = getattr(settings, 'SITE_URL', 'http://localhost:8000')
        cancel_url = f"{base_url}{cancel_path}?token={token}"
        # For consistency, make reschedule button point to cancel flow (require cancel first)
        reschedule_url = cancel_url
        context.update({'cancel_url': cancel_url, 'reschedule_url': reschedule_url})
    except Exception:
        pass

    logger = logging.getLogger(__name__)
    subject = f"Booking Rescheduled - {booking.organization.name}"
    html_content = render_to_string('bookings/emails/booking_rescheduled.html', context)
    from_email = settings.DEFAULT_FROM_EMAIL
    recipient_list = [booking.client_email]
    msg = EmailMessage(subject, html_content, from_email, recipient_list)
    msg.content_subtype = 'html'
    try:
        msg.extra_headers = {**getattr(msg, 'extra_headers', {}), 'X-CircleCal-Booking-ID': str(booking.id)}
    except Exception:
        pass

    try:
        logger.info('Sending booking rescheduled for booking=%s to=%s', booking.id, booking.client_email)
        sent = msg.send()
        logger.info('booking rescheduled send result for booking=%s sent=%s', booking.id, sent)
        return True
    except Exception:
        logger.exception('Failed to send booking rescheduled for booking=%s to=%s', booking.id, booking.client_email)
        return False
