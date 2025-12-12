from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.db import transaction
from django.utils import timezone
from django.utils.timezone import make_aware
from zoneinfo import ZoneInfo
from datetime import datetime
from django.conf import settings
from accounts.models import Business as Organization
from .models import OrgSettings, Booking, ServiceSettingFreeze, AuditBooking
from .emails import send_booking_confirmation, send_booking_cancellation


@receiver(post_save, sender=Organization)
def create_org_settings(sender, instance, created, **kwargs):
    if created:
        OrgSettings.objects.create(organization=instance)


# Confirmation emails are sent explicitly from views to avoid duplicates.


@receiver(post_delete, sender=Booking)
def send_cancellation_email(sender, instance, **kwargs):
    """Send cancellation email when booking is deleted."""
    if instance.client_email and not instance.is_blocking:
        send_booking_cancellation(instance)


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
    svc = booking_instance.service
    if not svc:
        return
    org = booking_instance.organization
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
            snapshot = {
                'id': instance.id,
                'title': getattr(instance, 'title', None),
                'start': instance.start.isoformat() if getattr(instance, 'start', None) else None,
                'end': instance.end.isoformat() if getattr(instance, 'end', None) else None,
                'client_name': getattr(instance, 'client_name', None),
                'client_email': getattr(instance, 'client_email', None),
                'is_blocking': bool(getattr(instance, 'is_blocking', False)),
                'service_id': instance.service.id if getattr(instance, 'service', None) else None,
                'service_slug': instance.service.slug if getattr(instance, 'service', None) else None,
                'created_at': instance.created_at.isoformat() if getattr(instance, 'created_at', None) else None,
            }

            AuditBooking.objects.create(
                organization=instance.organization,
                booking_id=instance.id,
                event_type=AuditBooking.EVENT_DELETED,
                booking_snapshot=snapshot,
                service=instance.service if getattr(instance, 'service', None) else None,
                start=instance.start if getattr(instance, 'start', None) else None,
                end=instance.end if getattr(instance, 'end', None) else None,
                client_name=getattr(instance, 'client_name', ''),
                client_email=getattr(instance, 'client_email', ''),
            )
        except Exception:
            # Do not let auditing break the delete flow
            pass

    try:
        transaction.on_commit(_create_audit)
    except Exception:
        _create_audit()
