from django.db import models
from django.conf import settings
from django.utils import timezone
from .storage import OverwriteStorage
import os

User = settings.AUTH_USER_MODEL

class Profile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='profile')
    def _profile_upload_to(instance, filename):
        # Store all profile pictures at MEDIA_ROOT/profile_pictures/profile_pic.jpg
        # Preserve extension if present
        _, ext = os.path.splitext(filename)
        if not ext:
            ext = '.jpg'
        return f'profile_pictures/profile_pic{ext}'

    avatar = models.ImageField(upload_to=_profile_upload_to, storage=OverwriteStorage(), blank=True, null=True)
    timezone = models.CharField(max_length=63, default='UTC', help_text="User's timezone (e.g., America/Los_Angeles)")
    email_alerts = models.BooleanField(default=True)
    booking_reminders = models.BooleanField(default=True)
    # Add more fields as needed

    def __str__(self):
        return f"Profile for {self.user}"

class Business(models.Model):
    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    owner = models.ForeignKey(User, related_name="owned_businesses", on_delete=models.SET_NULL, null=True)
    # Add billing fields if needed (stripe_customer_id etc.)
    stripe_customer_id = models.CharField(max_length=255, blank=True, null=True)
    # Timezone for the organization (e.g., 'America/Los_Angeles', 'America/New_York')
    # Defaults to 'UTC' if not set - organizations should update this to their local timezone
    timezone = models.CharField(max_length=63, default='UTC', help_text="Business's timezone (e.g., America/Los_Angeles)")
    # Soft-delete / archive flag. Database already contains this column in some
    # environments; keep it here with a default to avoid NOT NULL errors.
    is_archived = models.BooleanField(default=False)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Business"
        verbose_name_plural = "Businesses"

class Membership(models.Model):
    ROLE_CHOICES = (
        ('owner', 'Owner'),
        ('admin', 'Admin'),
        ('manager', 'Manager'),
        ('staff', 'Staff'),
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="memberships")
    organization = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="members")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='staff')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'organization')

    def __str__(self):
        return f"{self.user} @ {self.organization} ({self.role})"
    

class Invite(models.Model):
    organization = models.ForeignKey(
        Business,
        on_delete=models.CASCADE,
        related_name="invites"
    )
    email = models.EmailField()
    token = models.CharField(max_length=64, unique=True)
    role = models.CharField(
        max_length=20,
        choices=Membership.ROLE_CHOICES,
        default='staff'
    )
    accepted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Invite to {self.email} for {self.organization.name}"


class LoginActivity(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="login_activities")
    timestamp = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"Login by {self.user} at {self.timestamp:%Y-%m-%d %H:%M:%S}"
