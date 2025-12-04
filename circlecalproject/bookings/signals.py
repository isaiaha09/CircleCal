from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from accounts.models import Business as Organization
from .models import OrgSettings, Booking
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
