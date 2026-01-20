import logging
from urllib.parse import quote, urlencode
import datetime
import re
from django.core.mail import EmailMessage
from django.template.loader import render_to_string
from django.conf import settings
from django.utils import timezone
from zoneinfo import ZoneInfo
from django.core.signing import TimestampSigner
from django.urls import reverse
from bookings.models import build_offline_payment_instructions, filter_offline_payment_instructions_for_method


def _dedupe_emails(emails):
    """Return a stable, de-duped list of non-empty emails."""
    seen = set()
    out = []
    for e in (emails or []):
        try:
            e = (e or '').strip()
        except Exception:
            e = ''
        if not e:
            continue
        key = e.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def _booking_internal_recipients(booking):
    """Compute internal notification recipients for a booking.

    Rules:
    - Always include org owner (if email exists)
    - Always include org managers (Membership.role == 'manager')
    - If service has any assignments, include all assigned members' emails
    - Also include booking.assigned_user email if present

    For unassigned services, only owner+managers receive internal notifications.
    """
    # During cascaded deletes (e.g., org deletion), the related Business row may
    # already be gone by the time on_commit hooks run. Accessing the FK relation
    # can then raise Business.DoesNotExist; treat that as "no org" and just
    # return an empty/partial recipient list instead of crashing.
    try:
        org = getattr(booking, 'organization', None)
    except Exception:
        org = None
    # Important: don't dereference booking.service here.
    # During cascaded deletes/cleanup, the Service row can be missing while the
    # Booking instance still has a service_id value; accessing the FK relation
    # raises Service.DoesNotExist and can crash on_commit hooks.
    try:
        service_id = getattr(booking, 'service_id', None)
    except Exception:
        service_id = None

    base = []
    try:
        owner = getattr(org, 'owner', None)
        if owner and getattr(owner, 'email', None):
            base.append(owner.email)
    except Exception:
        pass

    # Managers in this organization
    try:
        from accounts.models import Membership
        mgrs = (
            Membership.objects
            .filter(organization=org, is_active=True, role='manager')
            .select_related('user')
            .values_list('user__email', flat=True)
        )
        base.extend(list(mgrs))
    except Exception:
        pass

    assigned = []
    try:
        u = getattr(booking, 'assigned_user', None)
        if u and getattr(u, 'email', None):
            assigned.append(u.email)
    except Exception:
        pass

    # Service assignments (team members assigned to the service)
    has_assignments = False
    try:
        if service_id is not None:
            from bookings.models import ServiceAssignment
            rows = (
                ServiceAssignment.objects
                .filter(service_id=service_id)
                .select_related('membership__user')
                .values_list('membership__user__email', flat=True)
            )
            rows_list = [e for e in list(rows) if e]
            if rows_list:
                has_assignments = True
                assigned.extend(rows_list)
    except Exception:
        # Table might not exist if migrations haven't been applied.
        has_assignments = False

    recipients = list(base)
    if has_assignments:
        recipients.extend(assigned)

    recipients = _dedupe_emails(recipients)
    # Never send internal notifications to the client email by accident.
    try:
        client_email = (getattr(booking, 'client_email', '') or '').strip()
        if client_email:
            recipients = [e for e in recipients if e.lower() != client_email.lower()]
    except Exception:
        pass
    return recipients


def _send_html_email(subject: str, html_content: str, to_emails, booking_id=None, fail_silently=False):
    """Send a single HTML email using Django EmailMessage.

    Uses BCC to avoid leaking recipient lists to each other.
    """
    to_emails = _dedupe_emails(to_emails)
    if not to_emails:
        return 0
    from_email = settings.DEFAULT_FROM_EMAIL
    msg = EmailMessage(subject, html_content, from_email, [from_email], bcc=to_emails)
    msg.content_subtype = 'html'
    try:
        if booking_id is not None:
            msg.extra_headers = {**getattr(msg, 'extra_headers', {}), 'X-CircleCal-Booking-ID': str(booking_id)}
    except Exception:
        pass
    try:
        return msg.send(fail_silently=fail_silently)
    except Exception:
        if fail_silently:
            return 0
        raise
def _extract_offline_line_for_method(raw: str, method: str) -> str:
    """Extract the line for a given offline method (venmo/zelle) from instructions."""
    try:
        m = (method or '').strip().lower()
        if not m:
            return ''
        for line in (raw or '').splitlines():
            line = (line or '').strip()
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
    base_url = getattr(settings, 'SITE_URL', 'https://circlecal.app')
    cancel_url = f"{base_url}{cancel_path}?token={token}"
    # Make the reschedule button in the confirmation email behave like the cancel button
    # so clients must cancel first before rescheduling. Point reschedule_url to cancel_url.
    reschedule_url = cancel_url
    payment_method = (getattr(booking, 'payment_method', '') or '').strip().lower()
    context = {
        'booking': booking,
        'public_ref': getattr(booking, 'public_ref', None),
            'site_url': getattr(settings, 'SITE_URL', 'https://circlecal.app'),
        'cancel_url': cancel_url,
        'reschedule_url': reschedule_url,
        'ics_url': f"{base_url}{reverse('bookings:booking_ics', args=[booking.id])}?token={quote(token)}",
        'payment_method': payment_method,
        'stripe_card_brand': '',
        'stripe_card_last4': '',
        'outlook_web_url': '',
    }

    # Add a Google Calendar quick-link (TEMPLATE action)
    try:
        dtstart = booking.start.astimezone(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        dtend = booking.end.astimezone(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        gcal_base = 'https://www.google.com/calendar/render?action=TEMPLATE'
        g_params = {
            'text': (getattr(booking.service, 'name', None) or getattr(booking, 'title', 'Booking')),
            'dates': f"{dtstart}/{dtend}",
            'details': f"Booking at {getattr(booking.organization, 'name', '')}\\nRef: {getattr(booking, 'public_ref', '')}",
            'location': getattr(booking.organization, 'name', ''),
        }
        context['google_calendar_url'] = gcal_base + '&' + urlencode(g_params)

        outlook_base = 'https://outlook.live.com/calendar/0/deeplink/compose'
        outlook_params = {
            'rru': 'addevent',
            'subject': (getattr(booking.service, 'name', None) or getattr(booking, 'title', 'Booking')),
            'startdt': booking.start.astimezone(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'enddt': booking.end.astimezone(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'body': f"Booking at {getattr(booking.organization, 'name', '')}\\nRef: {getattr(booking, 'public_ref', '')}",
            'location': getattr(booking.organization, 'name', ''),
        }
        context['outlook_web_url'] = outlook_base + '?' + urlencode(outlook_params)
    except Exception:
        context['google_calendar_url'] = ''
        context['outlook_web_url'] = ''

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
    base_url = getattr(settings, 'SITE_URL', 'https://circlecal.app')
    reschedule_url = f"{base_url}{reschedule_path}?token={token}"

    context = {
        'booking': booking,
        'public_ref': getattr(booking, 'public_ref', None),
            'site_url': getattr(settings, 'SITE_URL', 'https://circlecal.app'),
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


def send_internal_booking_cancellation_notification(booking, refund_info=None):
    """Send booking cancellation notification to internal recipients.

    Recipients depend on whether the service is assigned (see _booking_internal_recipients).
    Uses the same HTML template/styling as the client cancellation email.
    """
    recipients = _booking_internal_recipients(booking)
    if not recipients:
        return False

    signer = TimestampSigner()
    token = signer.sign(str(booking.id))
    reschedule_path = reverse('bookings:reschedule_booking', args=[booking.id])
    base_url = getattr(settings, 'SITE_URL', 'https://circlecal.app')
    reschedule_url = f"{base_url}{reschedule_path}?token={token}"

    context = {
        'booking': booking,
        'public_ref': getattr(booking, 'public_ref', None),
        'site_url': getattr(settings, 'SITE_URL', 'https://circlecal.app'),
        'refund_info': refund_info,
        'reschedule_url': reschedule_url,
    }

    logger = logging.getLogger(__name__)
    subject = f"Booking Cancelled - {booking.organization.name}"
    html_content = render_to_string('bookings/emails/booking_cancellation.html', context)
    try:
        logger.info('Sending INTERNAL booking cancellation for booking=%s to=%s', getattr(booking, 'id', None), recipients)
        _send_html_email(subject, html_content, recipients, booking_id=getattr(booking, 'id', None), fail_silently=True)
        return True
    except Exception:
        logger.exception('Failed to send INTERNAL booking cancellation for booking=%s', getattr(booking, 'id', None))
        return False


def send_booking_reminder(booking):
    """Send booking reminder email to client (typically 24h before)."""
    if not booking.client_email:
        return False
    
    context = {
        'booking': booking,
        'site_url': getattr(settings, 'SITE_URL', 'https://circlecal.app'),
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


def send_internal_booking_reminder_notification(booking):
    """Send booking reminder notification to internal recipients.

    Uses the same HTML template/styling as the client reminder email.
    """
    recipients = _booking_internal_recipients(booking)
    if not recipients:
        return False
    context = {
        'booking': booking,
        'site_url': getattr(settings, 'SITE_URL', 'https://circlecal.app'),
    }
    logger = logging.getLogger(__name__)
    subject = f"Reminder: Upcoming Booking - {booking.organization.name}"
    html_content = render_to_string('bookings/emails/booking_reminder.html', context)
    try:
        logger.info('Sending INTERNAL booking reminder for booking=%s to=%s', getattr(booking, 'id', None), recipients)
        _send_html_email(subject, html_content, recipients, booking_id=getattr(booking, 'id', None), fail_silently=True)
        return True
    except Exception:
        logger.exception('Failed to send INTERNAL booking reminder for booking=%s', getattr(booking, 'id', None))
        return False

def send_owner_booking_notification(booking):
    """Send a styled HTML notification to internal recipients for a new booking.

    Recipient rules:
    - If service has NO assignments: owner + managers
    - If service HAS assignments: assignees + managers + owner
    """
    org = booking.organization
    recipients = _booking_internal_recipients(booking)
    if not recipients:
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

    # Provide calendar links for owners (ICS + Google)
    try:
        signer = TimestampSigner()
        token = signer.sign(str(booking.id))
        base_url = getattr(settings, 'SITE_URL', 'https://circlecal.app')
        context['ics_url'] = f"{base_url}{reverse('bookings:booking_ics', args=[booking.id])}?token={quote(token)}"
        try:
            dtstart = booking.start.astimezone(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
            dtend = booking.end.astimezone(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
            # Google Calendar link
            gcal_base = 'https://www.google.com/calendar/render?action=TEMPLATE'
            g_params = {
                'text': (getattr(booking.service, 'name', None) or getattr(booking, 'title', 'Booking')),
                'dates': f"{dtstart}/{dtend}",
                'details': f"Booking at {getattr(booking.organization, 'name', '')}\\nRef: {getattr(booking, 'public_ref', '')}",
                'location': getattr(booking.organization, 'name', ''),
            }
            context['google_calendar_url'] = gcal_base + '&' + urlencode(g_params)

            # Outlook Web deeplink
            outlook_base = 'https://outlook.live.com/calendar/0/deeplink/compose'
            outlook_params = {
                'rru': 'addevent',
                'subject': (getattr(booking.service, 'name', None) or getattr(booking, 'title', 'Booking')),
                'startdt': booking.start.astimezone(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                'enddt': booking.end.astimezone(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                'body': f"Booking at {getattr(booking.organization, 'name', '')}\\nRef: {getattr(booking, 'public_ref', '')}",
                'location': getattr(booking.organization, 'name', ''),
            }
            context['outlook_web_url'] = outlook_base + '?' + urlencode(outlook_params)
        except Exception:
            context['google_calendar_url'] = ''
            context['outlook_web_url'] = ''
    except Exception:
        context['ics_url'] = ''
        context['google_calendar_url'] = ''

    logger = logging.getLogger(__name__)
    html_content = render_to_string('bookings/emails/booking_owner_notification.html', context)
    from_email = settings.DEFAULT_FROM_EMAIL
    try:
        logger.info('Sending INTERNAL new-booking notification for booking=%s to=%s', booking.id, recipients)
        _send_html_email(subject, html_content, recipients, booking_id=booking.id, fail_silently=True)
    except Exception:
        logger.exception('Failed to send INTERNAL new-booking notification for booking=%s', booking.id)


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
        'site_url': getattr(settings, 'SITE_URL', 'https://circlecal.app'),
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
        base_url = getattr(settings, 'SITE_URL', 'https://circlecal.app')
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
        # Internal notification copy (same styling/template)
        try:
            recipients = _booking_internal_recipients(booking)
            if recipients:
                logger.info('Sending INTERNAL booking rescheduled for booking=%s to=%s', booking.id, recipients)
                _send_html_email(subject, html_content, recipients, booking_id=booking.id, fail_silently=True)
        except Exception:
            logger.exception('Failed to send INTERNAL booking rescheduled for booking=%s', getattr(booking, 'id', None))

        return True
    except Exception:
        logger.exception('Failed to send booking rescheduled for booking=%s to=%s', booking.id, booking.client_email)
        return False
