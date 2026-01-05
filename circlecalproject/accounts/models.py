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
    display_name = models.CharField(max_length=255, blank=True, null=True, help_text='Optional display name used for client-facing messages')
    email_alerts = models.BooleanField(default=True)
    booking_reminders = models.BooleanField(default=True)
    # If a user cancels at the end of their free trial, we can schedule their
    # account for automatic deletion when the trial ends (unless they subscribe).
    scheduled_account_deletion_at = models.DateTimeField(null=True, blank=True)
    scheduled_account_deletion_reason = models.CharField(max_length=64, blank=True, null=True)
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

    # Stripe Connect (for taking card payments from clients into the business's Stripe account)
    stripe_connect_account_id = models.CharField(max_length=255, blank=True, null=True, db_index=True)
    stripe_connect_details_submitted = models.BooleanField(default=False)
    stripe_connect_charges_enabled = models.BooleanField(default=False)
    stripe_connect_payouts_enabled = models.BooleanField(default=False)

    # Public embed widget (Pro/Team only). Use a revocable key so businesses can
    # embed CircleCal pages on their own websites without exposing admin access.
    embed_enabled = models.BooleanField(default=False)
    embed_key = models.CharField(max_length=64, blank=True, null=True, db_index=True)

    # Custom domain support (Pro/Team add-on / Team feature).
    # Example: booking.example.com
    custom_domain = models.CharField(max_length=255, blank=True, null=True, unique=True, db_index=True)
    custom_domain_verification_token = models.CharField(max_length=64, blank=True, null=True)
    custom_domain_verified = models.BooleanField(default=False)
    custom_domain_verified_at = models.DateTimeField(null=True, blank=True)
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


class BusinessSlugRedirect(models.Model):
    """Stores previous slugs for a Business so old links can redirect.

    This enables a safe, explicit "Change public URL" action without breaking
    old bookmarks, embeds, or shared links.
    """

    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name='slug_redirects')
    old_slug = models.SlugField(max_length=255, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.old_slug} -> {self.business.slug}"
    

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


class Team(models.Model):
    """A logical grouping of staff/manager users within a Business.

    Teams can be used to assign shared schedules and bookings to groups
    of staff (duos, small teams) or individual staff members.
    """
    organization = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="teams")
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('organization', 'slug')

    def __str__(self):
        return f"{self.name} @ {self.organization.name}"


class TeamMembership(models.Model):
    ROLE_CHOICES = (
        ('member', 'Member'),
        ('lead', 'Lead'),
    )
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name='memberships')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='team_memberships')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='member')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('team', 'user')

    def __str__(self):
        return f"{self.user} in {self.team} ({self.role})"
