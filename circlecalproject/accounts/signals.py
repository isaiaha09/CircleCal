from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.contrib.auth.models import User
from django.contrib.auth.signals import user_logged_in
from .models import Profile
from .models import LoginActivity

@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        Profile.objects.create(user=instance)

@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    # Ensure profile exists for users created before this feature
    profile, _ = Profile.objects.get_or_create(user=instance)
    profile.save()


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


