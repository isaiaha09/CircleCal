from django.db.models.signals import post_save, post_delete, pre_save
from django.dispatch import receiver
from django.db import transaction
from django.utils import timezone
from django.utils.timezone import make_aware
from zoneinfo import ZoneInfo
from datetime import datetime
from django.conf import settings
from accounts.models import Business as Organization
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
                extra=extra or '',
            )
        except Exception:
            # Do not let auditing break the delete flow
            pass

    try:
        transaction.on_commit(_create_audit)
    except Exception:
        _create_audit()
