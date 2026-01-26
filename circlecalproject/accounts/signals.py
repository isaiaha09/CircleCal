from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.contrib.auth.models import User
from django.contrib.auth.signals import user_logged_in
from .models import Profile
from .models import LoginActivity
import logging


logger = logging.getLogger(__name__)

@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    # Never block user creation on profile provisioning.
    if not created:
        return
    try:
        Profile.objects.create(user=instance)
    except Exception as exc:
        logger.exception("Failed to create Profile for user_id=%s: %s", getattr(instance, 'id', None), exc)

@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    # Ensure profile exists for users created before this feature.
    # Avoid calling profile.save() here — it is unnecessary and can cause
    # unexpected failures (storage/migrations) after any User save (e.g. email update).
    try:
        Profile.objects.get_or_create(user=instance)
    except Exception as exc:
        logger.exception("Failed to ensure Profile exists for user_id=%s: %s", getattr(instance, 'id', None), exc)


@receiver(user_logged_in)
def log_user_login(sender, request, user, **kwargs):
    # Capture IP and user agent
    ip = None
    if request:
        # X-Forwarded-For if behind proxy
        xff = request.META.get('HTTP_X_FORWARDED_FOR')
        if xff:
            ip = xff.split(',')[0].strip()
        else:
            ip = request.META.get('REMOTE_ADDR')
        ua = request.META.get('HTTP_USER_AGENT', '')
    else:
        ua = ''
    try:
        LoginActivity.objects.create(user=user, ip_address=ip, user_agent=ua)
    except Exception:
        # Never block login on logging issues
        pass


@receiver(post_delete, sender=Profile)
def delete_profile_avatar_file(sender, instance, **kwargs):
    """Delete avatar blob/object when a Profile is deleted.

    Django does not automatically delete FileField storage objects when model rows
    are deleted. We clean up explicitly so user account deletion doesn't leave
    orphaned avatar files in Cloudinary/GCS/local MEDIA.
    """
    try:
        f = getattr(instance, 'avatar', None)
        # FieldFile is truthy only if it has a name
        if f:
            f.delete(save=False)
    except Exception:
        # Never block deletion on storage errors
        pass


