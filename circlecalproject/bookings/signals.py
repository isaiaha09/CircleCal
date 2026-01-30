from django.db.models.signals import post_save, post_delete, pre_save
from django.dispatch import receiver
from django.db import transaction
from django.utils import timezone
from django.utils.timezone import make_aware
from zoneinfo import ZoneInfo
from datetime import datetime
from django.conf import settings
from accounts.models import Business as Organization
from accounts.models import Membership
from accounts.push import send_push_to_user
from .models import OrgSettings, Booking, ServiceSettingFreeze, AuditBooking, Service
from .emails import send_booking_confirmation, send_booking_cancellation, send_internal_booking_cancellation_notification


@receiver(post_save, sender=Organization)
def create_org_settings(sender, instance, created, **kwargs):
    if created:
        OrgSettings.objects.create(organization=instance)


def _service_signature_tuple(svc) -> tuple:
    """Mirror calendar_app.views._service_schedule_signature without importing it."""
    try:
        return (
            int(getattr(svc, 'duration', 0) or 0),
            int(getattr(svc, 'buffer_before', 0) or 0),
            int(getattr(svc, 'buffer_after', 0) or 0),
        )
    except Exception:
        return (0, 0, 0)


@receiver(pre_save, sender=Service)
def service_signature_updated_at(sender, instance, **kwargs):
    """Update Service.signature_updated_at when signature-affecting fields change."""
    try:
        if not hasattr(instance, 'signature_updated_at'):
            return
    except Exception:
        return

    # New service: ensure it has a timestamp.
    try:
        if not getattr(instance, 'pk', None):
            if getattr(instance, 'signature_updated_at', None) is None:
                instance.signature_updated_at = timezone.now()
            return
    except Exception:
        return

    try:
        prev = Service.objects.filter(pk=instance.pk).first()
    except Exception:
        prev = None
    if not prev:
        return

    try:
        if _service_signature_tuple(prev) != _service_signature_tuple(instance):
            instance.signature_updated_at = timezone.now()
    except Exception:
        # Fail-open: if we can't compare, don't mutate.
        return


# Confirmation emails are sent explicitly from views to avoid duplicates.


@receiver(post_save, sender=Booking)
def push_notify_assigned_user_on_create(sender, instance: Booking, created: bool, **kwargs):
    """Send a push to the assigned user when a new booking is created.

    Product rules:
    - Never notify clients via push.
    - Never notify uninvolved staff.
    Therefore we ONLY notify `assigned_user` (if set).
    """

    if not created:
        return

    # Skip internal override markers / blocks.
    if getattr(instance, 'service_id', None) is None:
        return
    if bool(getattr(instance, 'is_blocking', False)):
        return

    assigned = getattr(instance, 'assigned_user', None)
    if not assigned:
        return

    # Build a short human-friendly time string in org timezone when possible.
    when_str = None
    try:
        org = getattr(instance, 'organization', None)
        tz_name = getattr(org, 'timezone', None) if org else None
        org_tz = ZoneInfo(tz_name) if tz_name else ZoneInfo(getattr(settings, 'TIME_ZONE', 'UTC'))
        dt = getattr(instance, 'start', None)
        if dt is not None:
            when_str = dt.astimezone(org_tz).strftime('%a %b %d, %I:%M %p')
    except Exception:
        when_str = None

    title = 'New booking'
    base = (getattr(instance, 'title', None) or 'Booking').strip() or 'Booking'
    body = f"{base}{' • ' + when_str if when_str else ''}"

    data = {
        'orgSlug': getattr(getattr(instance, 'organization', None), 'slug', None),
        'bookingId': getattr(instance, 'id', None),
        'kind': 'booking_created',
    }

    # Only navigate on tap when orgSlug+bookingId are present.
    if not data.get('orgSlug') or not data.get('bookingId'):
        return

    def _send():
        try:
            send_push_to_user(user=assigned, title=title, body=body, data=data)
        except Exception:
            # Best-effort: never block booking creation.
            pass

    try:
        transaction.on_commit(_send)
    except Exception:
        _send()


@receiver(pre_save, sender=Booking)
def booking_capture_prev_state(sender, instance: Booking, **kwargs):
    """Capture previous assignment/schedule info so post_save can emit targeted push notifications."""

    try:
        if not getattr(instance, 'pk', None):
            return
    except Exception:
        return

    try:
        prev = (
            Booking.objects.filter(pk=instance.pk)
            .only('assigned_user_id', 'assigned_team_id', 'start', 'end')
            .first()
        )
    except Exception:
        prev = None

    if not prev:
        return

    try:
        instance._prev_assigned_user_id = getattr(prev, 'assigned_user_id', None)
        instance._prev_assigned_team_id = getattr(prev, 'assigned_team_id', None)
        instance._prev_start = getattr(prev, 'start', None)
        instance._prev_end = getattr(prev, 'end', None)
    except Exception:
        pass


def _booking_when_str(instance: Booking) -> str | None:
    try:
        org = getattr(instance, 'organization', None)
        tz_name = getattr(org, 'timezone', None) if org else None
        org_tz = ZoneInfo(tz_name) if tz_name else ZoneInfo(getattr(settings, 'TIME_ZONE', 'UTC'))
        dt = getattr(instance, 'start', None)
        if dt is None:
            return None
        return dt.astimezone(org_tz).strftime('%a %b %d, %I:%M %p')
    except Exception:
        return None


def _has_active_membership(*, user, org) -> bool:
    try:
        if not user or not org:
            return False
    except Exception:
        return False

    try:
        return Membership.objects.filter(user=user, organization=org, is_active=True).exists()
    except Exception:
        return False


def _involved_staff_users_for_booking(instance: Booking) -> list:
    """Return involved internal users for a booking.

    Product rule: notify only staff involved in the booking.
    We treat involved as:
    - assigned_user
    - all active members of assigned_team (if present)
    """
    users = []
    org = getattr(instance, 'organization', None)

    try:
        au = getattr(instance, 'assigned_user', None)
        if au:
            if _has_active_membership(user=au, org=org):
                users.append(au)
    except Exception:
        pass

    try:
        team = getattr(instance, 'assigned_team', None)
        if team:
            try:
                for m in team.memberships.filter(is_active=True).select_related('user'):
                    u = getattr(m, 'user', None)
                    if u:
                        if _has_active_membership(user=u, org=org):
                            users.append(u)
            except Exception:
                pass
    except Exception:
        pass

    # De-dup by user id.
    uniq = []
    seen = set()
    for u in users:
        try:
            uid = getattr(u, 'id', None)
        except Exception:
            uid = None
        if not uid or uid in seen:
            continue
        seen.add(uid)
        uniq.append(u)
    return uniq


@receiver(post_save, sender=Booking)
def push_notify_assigned_users_on_update(sender, instance: Booking, created: bool, **kwargs):
    """Send push notifications for booking updates (reassigned/rescheduled).

    Only involved assignees receive pushes:
    - assigned_user
    - members of assigned_team
    - previous assigned_user (when reassigned away)
    """

    if created:
        return

    # Skip internal override markers / blocks.
    if getattr(instance, 'service_id', None) is None:
        return
    if bool(getattr(instance, 'is_blocking', False)):
        return

    prev_user_id = getattr(instance, '_prev_assigned_user_id', None)
    prev_team_id = getattr(instance, '_prev_assigned_team_id', None)
    prev_start = getattr(instance, '_prev_start', None)
    prev_end = getattr(instance, '_prev_end', None)

    # If we couldn't capture previous state, do nothing.
    if prev_user_id is None and prev_team_id is None and prev_start is None and prev_end is None:
        return

    org_slug = getattr(getattr(instance, 'organization', None), 'slug', None)
    booking_id = getattr(instance, 'id', None)
    if not org_slug or not booking_id:
        return

    base = (getattr(instance, 'title', None) or 'Booking').strip() or 'Booking'
    when_str = _booking_when_str(instance)

    # Reassignment detection
    new_user_id = getattr(instance, 'assigned_user_id', None)
    new_team_id = getattr(instance, 'assigned_team_id', None)
    reassigned = (prev_user_id != new_user_id) or (prev_team_id != new_team_id)

    # Rescheduled detection
    rescheduled = (prev_start != getattr(instance, 'start', None)) or (prev_end != getattr(instance, 'end', None))

    def _send_to(user, title: str, body: str, data: dict):
        try:
            send_push_to_user(user=user, title=title, body=body, data=data)
        except Exception:
            pass

    def _work():
        # Dedupe: compute a single message per recipient for this save.
        recipients = _involved_staff_users_for_booking(instance)

        if reassigned and rescheduled:
            title = 'Booking updated'
            body = f"{base}{' • ' + when_str if when_str else ''}"
            data = {
                'orgSlug': org_slug,
                'bookingId': booking_id,
                'kind': 'booking_updated',
                'changes': ['reassigned', 'rescheduled'],
            }
            for u in recipients:
                _send_to(u, title, body, data)
        elif reassigned:
            title = 'Booking assigned'
            body = f"{base}{' • ' + when_str if when_str else ''}"
            data = {
                'orgSlug': org_slug,
                'bookingId': booking_id,
                'kind': 'booking_reassigned',
            }
            for u in recipients:
                _send_to(u, title, body, data)
        elif rescheduled:
            title = 'Booking rescheduled'
            body = f"{base}{' • ' + when_str if when_str else ''}"
            data = {
                'orgSlug': org_slug,
                'bookingId': booking_id,
                'kind': 'booking_rescheduled',
            }
            for u in recipients:
                _send_to(u, title, body, data)

        # If the booking was reassigned away from a specific user, notify that user too,
        # but open the list (they may no longer have access to the detail).
        if reassigned and prev_user_id and prev_user_id != new_user_id:
            try:
                from django.contrib.auth import get_user_model
                User = get_user_model()
                prev_user = User.objects.filter(id=prev_user_id).first()
            except Exception:
                prev_user = None

            if prev_user and _has_active_membership(user=prev_user, org=getattr(instance, 'organization', None)):
                title2 = 'Booking reassigned'
                body2 = f"{base}{' • ' + when_str if when_str else ''}"
                data2 = {
                    'orgSlug': org_slug,
                    'open': 'Bookings',
                    'kind': 'booking_reassigned_away',
                }
                _send_to(prev_user, title2, body2, data2)

    try:
        transaction.on_commit(_work)
    except Exception:
        _work()


@receiver(post_delete, sender=Booking)
def send_cancellation_email(sender, instance, **kwargs):
    """Send cancellation email when booking is deleted."""
    # Per-date overrides (service NULL) are internal schedule annotations.
    # They should not generate customer cancellation emails.
    # Use service_id to avoid triggering a Service fetch (which can raise
    # DoesNotExist during cascading deletes).
    if getattr(instance, 'service_id', None) is None:
        return
    # If organization is missing (inconsistent DB or cascading org delete),
    # do not attempt to email.
    org_id = getattr(instance, 'organization_id', None)
    if not org_id or not Organization.objects.filter(id=org_id).exists():
        return
    if instance.is_blocking:
        return

    # Push notification to involved internal assignees only (no clients, no uninvolved staff).
    try:
        org_slug = getattr(getattr(instance, 'organization', None), 'slug', None)
        if org_slug:
            base = (getattr(instance, 'title', None) or 'Booking').strip() or 'Booking'
            when_str = _booking_when_str(instance)
            title = 'Booking cancelled'
            body = f"{base}{' • ' + when_str if when_str else ''}"
            # Cancellation deletes the booking, so open the Bookings list instead of detail.
            data = {
                'orgSlug': org_slug,
                'open': 'Bookings',
                'kind': 'booking_cancelled',
            }

            def _push_work():
                try:
                    for u in _involved_staff_users_for_booking(instance):
                        send_push_to_user(user=u, title=title, body=body, data=data)
                except Exception:
                    pass

            try:
                transaction.on_commit(_push_work)
            except Exception:
                _push_work()
    except Exception:
        pass

    # Always notify internal recipients (owner/managers, and assignees when assigned).
    try:
        transaction.on_commit(lambda: send_internal_booking_cancellation_notification(instance))
    except Exception:
        try:
            send_internal_booking_cancellation_notification(instance)
        except Exception:
            pass

    # Client-facing cancellation email (only when we have a client email).
    if instance.client_email:
        try:
            # Schedule sending after transaction commit to ensure deletion persisted
            transaction.on_commit(lambda: send_booking_cancellation(instance))
        except Exception:
            # Fallback to immediate send if on_commit unavailable
            try:
                send_booking_cancellation(instance)
            except Exception:
                pass


# ------------------------------------------------------------------
# Remove per-date ServiceSettingFreeze when last booking for that
# service/date is deleted so days become editable again automatically.
# ------------------------------------------------------------------

def _org_local_date_for(dt, org):
    try:
        org_tz = ZoneInfo(getattr(org, 'timezone', getattr(settings, 'TIME_ZONE', 'UTC')))
    except Exception:
        org_tz = timezone.get_current_timezone()
    if dt.tzinfo is None:
        dt = make_aware(dt, org_tz)
    else:
        dt = dt.astimezone(org_tz)
    return dt.date()


def _maybe_remove_freeze(booking_instance):
    # Use service_id and safe lookup so this cleanup never crashes if the
    # Service row is already deleted (e.g., cascading org delete).
    service_id = getattr(booking_instance, 'service_id', None)
    if not service_id:
        return
    try:
        from .models import Service
        svc = Service.objects.filter(id=service_id).first()
    except Exception:
        return
    if not svc:
        return
    org_id = getattr(booking_instance, 'organization_id', None)
    if not org_id:
        return
    try:
        org = Organization.objects.filter(id=org_id).first()
    except Exception:
        return
    if not org:
        return
    try:
        target_date = _org_local_date_for(booking_instance.start, org)
    except Exception:
        return

    try:
        exists = Booking.objects.filter(service=svc, organization=org, start__date=target_date).exists()
    except Exception:
        return

    if not exists:
        try:
            ServiceSettingFreeze.objects.filter(service=svc, date=target_date).delete()
        except Exception:
            pass


@receiver(post_delete, sender=Booking)
def booking_post_delete_cleanup(sender, instance, **kwargs):
    # Run after transaction commit to ensure deletion persisted
    def work():
        try:
            _maybe_remove_freeze(instance)
        except Exception:
            pass

    try:
        transaction.on_commit(work)
    except Exception:
        work()


@receiver(post_delete, sender=Booking)
def booking_post_delete_audit(sender, instance, **kwargs):
    """Create an immutable audit record when a booking is deleted.

    We schedule the write with transaction.on_commit so the deletion
    is durable before the audit row is appended. The audit stores a
    JSON snapshot of the useful booking fields to aid debugging and
    owner-facing history UIs.
    """
    def _create_audit():
        try:
            # Per-date overrides (service NULL) are internal schedule annotations.
            # Do not show them in the "Deleted bookings" audit trail.
            service_id = getattr(instance, 'service_id', None)
            if service_id is None:
                return

            svc = None
            try:
                # May raise Service.DoesNotExist during cascading deletes.
                svc = instance.service
            except Exception:
                svc = None

            snapshot = {
                'id': instance.id,
                'public_ref': getattr(instance, 'public_ref', None),
                'title': getattr(instance, 'title', None),
                'start': instance.start.isoformat() if getattr(instance, 'start', None) else None,
                'end': instance.end.isoformat() if getattr(instance, 'end', None) else None,
                'client_name': getattr(instance, 'client_name', None),
                'client_email': getattr(instance, 'client_email', None),
                'is_blocking': bool(getattr(instance, 'is_blocking', False)),
                'service_id': service_id,
                'service_slug': getattr(svc, 'slug', None),
                'created_at': instance.created_at.isoformat() if getattr(instance, 'created_at', None) else None,
            }

            # Allow callers to mark the deletion as a client cancellation by
            # setting `instance._audit_event_type = 'cancelled'` prior to delete.
            et = getattr(instance, '_audit_event_type', None)
            if et == 'cancelled':
                event_type = AuditBooking.EVENT_CANCELLED
            else:
                event_type = AuditBooking.EVENT_DELETED

            # Optional audit explanation (e.g. bulk action reason)
            extra = getattr(instance, '_audit_extra', None)
            try:
                if extra is not None:
                    extra = str(extra)
            except Exception:
                extra = None
            if extra:
                try:
                    snapshot['explanation'] = extra
                except Exception:
                    pass

            # If caller forced a refund (admin bulk cancellation), record it so
            # the audit UI doesn't incorrectly label it as non-refunded.
            try:
                if getattr(instance, '_audit_refund_forced', False):
                    snapshot['refund_forced'] = True
            except Exception:
                pass
            try:
                rid = getattr(instance, '_audit_refund_id', None)
                if rid:
                    snapshot['refund_id'] = str(rid)
            except Exception:
                pass

            org_id = getattr(instance, 'organization_id', None)
            if not org_id:
                return
            org = Organization.objects.filter(id=org_id).first()
            if not org:
                return

            AuditBooking.objects.create(
                organization=org,
                booking_id=instance.id,
                event_type=event_type,
                booking_snapshot=snapshot,
                service=svc,
                start=instance.start if getattr(instance, 'start', None) else None,
                end=instance.end if getattr(instance, 'end', None) else None,
                client_name=getattr(instance, 'client_name', ''),
                client_email=getattr(instance, 'client_email', ''),
                created_by=getattr(instance, '_audit_created_by', None),
                extra=extra or '',
            )
        except Exception:
            # Do not let auditing break the delete flow
            pass

    try:
        transaction.on_commit(_create_audit)
    except Exception:
        _create_audit()
