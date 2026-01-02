from django.db import models
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.utils import timezone
from django.conf import settings
from django.contrib.auth import get_user_model
User = get_user_model()
from accounts.models import Business as Organization, Membership
import secrets

_PUBLIC_REF_ALPHABET = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'

def generate_public_ref(n=8):
    return ''.join(secrets.choice(_PUBLIC_REF_ALPHABET) for _ in range(n))

class Service(models.Model):
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="services"
    )

    name = models.CharField(max_length=120)
    slug = models.SlugField(unique=True)

    description = models.TextField(blank=True)

    duration = models.PositiveIntegerField(help_text="Duration in minutes")
    price = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    buffer_before = models.PositiveIntegerField(default=0)
    buffer_after = models.PositiveIntegerField(default=0)
    # If true, allow a booking that starts inside the availability window
    # even if its end extends past the configured window end. Owners can
    # enable this when they are ok with appointments finishing after their
    # listed availability end time.
    allow_ends_after_availability = models.BooleanField(default=False)

    # Per-service client-visible slot increment (minutes). This is stored
    # per-service so coaches can persist a preferred increments value.
    # When `use_fixed_increment` is True, the system will use the
    # service duration + buffer to compute increments instead of this value.
    time_increment_minutes = models.PositiveIntegerField(default=30, help_text="Client-visible slot increment in minutes")

    # If true, use fixed increments equal to service duration + buffer settings
    # (i.e., slots step by duration+buffer). When false, `time_increment_minutes`
    # controls the visible increments for clients.
    use_fixed_increment = models.BooleanField(default=False)

    # Allow bookings that fit the service duration even if they would violate
    # buffer rules (i.e., 'squished' bookings). When True, such bookings are
    # allowed but should generate non-blocking warnings to the owner.
    allow_squished_bookings = models.BooleanField(default=False)

    min_notice_hours = models.PositiveIntegerField(default=1)
    max_booking_days = models.PositiveIntegerField(default=60)

    is_active = models.BooleanField(default=True)

    # Refund policy
    refunds_allowed = models.BooleanField(default=True, help_text="Whether clients can receive refunds on cancellation.")
    refund_cutoff_hours = models.PositiveIntegerField(default=24, help_text="Hours before start time within which refunds are NOT permitted.")
    refund_policy_text = models.TextField(blank=True, help_text="Optional custom refund policy text shown to clients.")

    # Payment method controls (per service)
    # - allow_stripe_payments controls whether clients can pay by card via Stripe for this service
    # - allowed_offline_payment_methods controls which offline methods are accepted for this service
    #   * None => inherit organization settings (default, preserves legacy behavior)
    #   * []   => offline payments disabled for this service
    #   * [..] => explicit allowed subset (e.g. ["cash","venmo"])
    allow_stripe_payments = models.BooleanField(default=True)
    allowed_offline_payment_methods = models.JSONField(null=True, blank=True, default=None)

    def __str__(self):
        try:
            org_name = self.organization.name
        except Exception:
            org_name = f"org_id={self.organization_id}"
        return f"{self.name} ({org_name})"

    class Meta:
        indexes = [
            models.Index(fields=["organization", "is_active"]),
        ]


class FacilityResource(models.Model):
    """A discrete bookable facility resource (e.g., Cage #1, Room A).

    Resources are organization-scoped and can be linked to one or more Services.
    """
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="facility_resources"
    )
    name = models.CharField(max_length=120)
    slug = models.SlugField()
    is_active = models.BooleanField(default=True)
    # How many distinct services may link to this resource.
    # - 1 means exclusive to one service (default)
    # - 2+ allows sharing across a fixed number of services
    # - 0 means unlimited sharing
    max_services = models.IntegerField(
        default=1,
        validators=[MinValueValidator(0)],
        help_text="Maximum number of services that can link to this resource (0 = unlimited).",
    )

    class Meta:
        unique_together = ("organization", "slug")
        indexes = [
            models.Index(fields=["organization", "is_active"]),
        ]

    def __str__(self):
        try:
            org_slug = self.organization.slug
        except Exception:
            org_slug = f"org_id={self.organization_id}"
        return f"{self.name} ({org_slug})"


class ServiceResource(models.Model):
    """Links a Service to allowed facility resources."""
    service = models.ForeignKey(
        'Service', on_delete=models.CASCADE, related_name='resource_links'
    )
    resource = models.ForeignKey(
        'FacilityResource', on_delete=models.CASCADE, related_name='service_links'
    )

    class Meta:
        unique_together = ("service", "resource")
        indexes = [
            models.Index(fields=["service"]),
            models.Index(fields=["resource"]),
        ]

    def __str__(self):
        # Avoid dereferencing potentially-orphaned FKs during admin delete
        # confirmation, which can raise DoesNotExist.
        return f"service_id={self.service_id} -> resource_id={self.resource_id}"



class Booking(models.Model):
    """Simple booking/event model used by the calendar frontend.

    - `is_blocking` can be used for coach-side full-day blocks or unavailable markers.
    """
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='bookings')
    title = models.CharField(max_length=200, blank=True)
    start = models.DateTimeField()
    end = models.DateTimeField()
    client_name = models.CharField(max_length=200, blank=True)
    client_email = models.EmailField(blank=True)
    is_blocking = models.BooleanField(default=False)
    service = models.ForeignKey(
        Service, on_delete=models.SET_NULL, null=True, blank=True, related_name="bookings"
    )
    created_at = models.DateTimeField(default=timezone.now)
    public_ref = models.CharField(max_length=16, unique=True, null=True, blank=True, db_index=True, help_text='Public booking reference shown to clients')
    # Optional assignment to a specific staff user or a Team (group of staff).
    assigned_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_bookings'
    )
    assigned_team = models.ForeignKey(
        'accounts.Team',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_bookings'
    )

    # Optional discrete facility resource assignment (cage/room/etc).
    resource = models.ForeignKey(
        'FacilityResource',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='bookings'
    )

    # Client payment tracking (public booking flow)
    # - 'none' for free services
    # - 'offline' for instructions-based methods (cash/Venmo/Zelle)
    # - 'stripe' when Stripe Checkout is used
    payment_method = models.CharField(max_length=20, blank=True, default='none', db_index=True)
    # For offline payments only, the specific method the client selected:
    # 'cash' | 'venmo' | 'zelle' | ''
    offline_payment_method = models.CharField(max_length=20, blank=True, default='', db_index=True)
    # 'not_required' | 'offline_due' | 'pending' | 'paid'
    payment_status = models.CharField(max_length=20, blank=True, default='not_required', db_index=True)
    stripe_checkout_session_id = models.CharField(max_length=255, blank=True, default='', db_index=True)

    # When a booking is created via the public reschedule flow, store the old booking id
    # so we can defer reschedule cleanup/emails until payment clears (Stripe).
    rescheduled_from_booking_id = models.IntegerField(null=True, blank=True, db_index=True)

    def __str__(self):
        return f"{self.title or 'Booking'} ({self.start.date()})"

    class Meta:
        indexes = [
            models.Index(fields=["organization", "start"]),
            models.Index(fields=["service", "start"]),
        ]

    def save(self, *args, **kwargs):
        # Ensure a short public reference exists for external use
        if not getattr(self, 'public_ref', None):
            # try a few times to avoid collisions
            for _ in range(8):
                candidate = generate_public_ref(8)
                if not Booking.objects.filter(public_ref=candidate).exists():
                    self.public_ref = candidate
                    break
        super().save(*args, **kwargs)


class PublicBookingIntent(models.Model):
    """A short-lived intent created when a client starts a paid booking.

    For Stripe payments, we create an intent first, then create the real Booking
    only after Stripe confirms payment.
    """

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='public_booking_intents')
    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name='public_booking_intents')

    start = models.DateTimeField()
    end = models.DateTimeField()
    client_name = models.CharField(max_length=200, blank=True)
    client_email = models.EmailField(blank=True)

    # 'stripe' only for now (offline confirmations create Bookings immediately)
    payment_method = models.CharField(max_length=20, blank=True, default='stripe', db_index=True)
    # 'pending' | 'paid' | 'cancelled' | 'expired'
    payment_status = models.CharField(max_length=20, blank=True, default='pending', db_index=True)
    stripe_checkout_session_id = models.CharField(max_length=255, blank=True, default='', db_index=True)

    # Reschedule support: defer cleanup until payment succeeds.
    rescheduled_from_booking_id = models.IntegerField(null=True, blank=True, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['organization', 'created_at']),
            models.Index(fields=['service', 'start']),
        ]

    def __str__(self):
        return f"PublicBookingIntent {self.id} ({self.organization_id}) {self.start}"


class OrgSettings(models.Model):
    organization = models.OneToOneField(
        Organization,
        on_delete=models.CASCADE,
        related_name="settings"
    )

    work_start = models.IntegerField(default=9)  # 9am
    work_end = models.IntegerField(default=17)   # 5pm
    block_size = models.IntegerField(default=30) # 30 min blocks

    # Org-wide refund policy (applies to all services unless service overrides)
    org_refunds_allowed = models.BooleanField(default=True)
    org_refund_cutoff_hours = models.PositiveIntegerField(default=24)
    org_refund_policy_text = models.TextField(blank=True)

    # Pro/Team: allow offline payment methods (instructions only).
    # This is feature-gated in billing.utils.can_use_offline_payment_methods.
    offline_payment_methods = models.JSONField(default=list, blank=True)
    offline_payment_instructions = models.TextField(blank=True, default='')

    # Optional: method-specific payment info used to generate QR codes and
    # provide clearer client-facing instructions.
    offline_venmo = models.TextField(blank=True, default='')
    offline_zelle = models.TextField(blank=True, default='')

    def __str__(self):
        try:
            org_name = self.organization.name
        except Exception:
            org_name = f"org_id={self.organization_id}"
        return f"Settings for {org_name}"


def build_offline_payment_instructions(org_settings: 'OrgSettings') -> str:
    """Build the effective offline payment instructions shown to clients.

    Priority:
    - Use explicit Venmo/Zelle values when present (to support QR generation)
    - Append any free-form `offline_payment_instructions`

    This keeps backwards-compatibility with existing deployments that only
    used `offline_payment_instructions`.
    """
    try:
        if not org_settings:
            return ''

        venmo = (getattr(org_settings, 'offline_venmo', '') or '').strip()
        zelle = (getattr(org_settings, 'offline_zelle', '') or '').strip()
        extra = (getattr(org_settings, 'offline_payment_instructions', '') or '').strip()

        lines = []
        if venmo:
            lines.append(f"Venmo: {venmo}")
        if zelle:
            lines.append(f"Zelle: {zelle}")

        if extra:
            extra_lines = [ln.strip() for ln in extra.splitlines() if (ln or '').strip()]
            # Avoid duplicating method lines when explicit fields exist.
            if venmo:
                extra_lines = [ln for ln in extra_lines if not ln.lower().startswith('venmo')]
            if zelle:
                extra_lines = [ln for ln in extra_lines if not ln.lower().startswith('zelle')]
            lines.extend(extra_lines)

        return "\n".join([ln for ln in lines if (ln or '').strip()]).strip()
    except Exception:
        return ''


class WeeklyAvailability(models.Model):
    """Defines one available time window for an organization on a given weekday.

    Weekday: 0=Monday ... 6=Sunday
    Multiple windows per day are allowed (e.g., 09:00-12:00 and 14:00-17:00).
    """
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="weekly_availability")
    weekday = models.PositiveSmallIntegerField()  # 0-6
    start_time = models.TimeField()
    end_time = models.TimeField()
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["organization", "weekday", "start_time"]
        indexes = [
            models.Index(fields=["organization", "weekday"]),
        ]

    def __str__(self):
        try:
            org_slug = self.organization.slug
        except Exception:
            org_slug = f"org_id={self.organization_id}"
        return f"{org_slug} {self.weekday} {self.start_time}-{self.end_time}"

    def clean(self):
        if self.end_time <= self.start_time:
            raise ValidationError("end_time must be after start_time")


class ServiceWeeklyAvailability(models.Model):
    """Per-service weekly availability windows.

    Weekday: 0=Monday ... 6=Sunday
    """
    service = models.ForeignKey('Service', on_delete=models.CASCADE, related_name='weekly_availability')
    weekday = models.PositiveSmallIntegerField()  # 0-6
    start_time = models.TimeField()
    end_time = models.TimeField()
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["service", "weekday", "start_time"]
        indexes = [
            models.Index(fields=["service", "weekday"]),
        ]

    def __str__(self):
        # Use *_id to avoid triggering a DB fetch (and possible DoesNotExist)
        # when rows are inconsistent.
        return f"svc-{self.service_id} {self.weekday} {self.start_time}-{self.end_time}"

    def clean(self):
        if self.end_time <= self.start_time:
            raise ValidationError("end_time must be after start_time")
        # Validate that this service window is within the organization's weekly availability
        try:
            org = self.service.organization
        except Exception:
            org = None

        if org:
            matches = WeeklyAvailability.objects.filter(
                organization=org,
                weekday=self.weekday,
                is_active=True,
                start_time__lte=self.start_time,
                end_time__gte=self.end_time,
            )
            if not matches.exists():
                raise ValidationError("Service availability window must be within the organization's weekly availability. Update the organization's calendar to allow this time.")


class MemberWeeklyAvailability(models.Model):
    """Per-membership weekly availability windows.

    Weekday: 0=Monday ... 6=Sunday
    """
    membership = models.ForeignKey('accounts.Membership', on_delete=models.CASCADE, related_name='weekly_availability')
    weekday = models.PositiveSmallIntegerField()  # 0-6
    start_time = models.TimeField()
    end_time = models.TimeField()
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["membership", "weekday", "start_time"]
        indexes = [
            models.Index(fields=["membership", "weekday"]),
        ]

    def __str__(self):
        return f"mem-{self.membership_id} {self.weekday} {self.start_time}-{self.end_time}"

    def clean(self):
        if self.end_time <= self.start_time:
            raise ValidationError("end_time must be after start_time")
        # Ensure membership is linked and validate against org weekly windows
        try:
            org = self.membership.organization
        except Exception:
            org = None

        if org:
            matches = WeeklyAvailability.objects.filter(
                organization=org,
                weekday=self.weekday,
                is_active=True,
                start_time__lte=self.start_time,
                end_time__gte=self.end_time,
            )
            if not matches.exists():
                raise ValidationError("Member availability window must be within the organization's weekly availability. Update the organization's calendar to allow this time.")


class ServiceSettingFreeze(models.Model):
    """Preserve service slot-related settings for a specific date so that
    changes to a Service do not retroactively affect days that already have
    bookings. The `frozen_settings` JSON stores the old values for the keys
    that influence slot generation (duration, buffer_after, time_increment_minutes,
    use_fixed_increment, allow_ends_after_availability, allow_squished_bookings).
    """
    service = models.ForeignKey('Service', on_delete=models.CASCADE, related_name='setting_freezes')
    date = models.DateField()
    frozen_settings = models.JSONField(default=dict)

    class Meta:
        unique_together = ('service', 'date')

    def __str__(self):
        # Avoid dereferencing service FK; admin delete confirmation calls str()
        # on related objects and should not crash if data is inconsistent.
        return f"Freeze for service_id={self.service_id} on {self.date}"


class ServiceAssignment(models.Model):
    """Assign a Service to a specific Membership (team member) within the organization.

    This model allows services to be scoped to one or more members without
    modifying the core `Service` schema. Migrations are required to create
    this table in production, but the view code will gracefully handle the
    absence of the table if migrations have not been applied.
    """
    service = models.ForeignKey('Service', on_delete=models.CASCADE, related_name='assignments')
    membership = models.ForeignKey(Membership, on_delete=models.CASCADE, related_name='service_assignments')

    class Meta:
        unique_together = ('service', 'membership')

    def __str__(self):
        # service may be missing in an inconsistent DB; use IDs to keep str() safe.
        return f"service_id={self.service_id} assigned to membership_id={self.membership_id}"


class AuditBooking(models.Model):
    """Immutable audit log of booking deletions/cancellations.

    Records a JSON snapshot of the booking and some indexed fields to
    support quick queries in the UI. This is an append-only record; the
    application will write entries here when bookings are deleted or
    cancelled so owners can review historical changes.
    """
    EVENT_DELETED = 'deleted'
    EVENT_CANCELLED = 'cancelled'
    EVENT_CHOICES = [
        (EVENT_DELETED, 'deleted'),
        (EVENT_CANCELLED, 'cancelled'),
    ]

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='audit_bookings')
    booking_id = models.IntegerField(null=True, blank=True, help_text='Original Booking.id when available')
    event_type = models.CharField(max_length=32, choices=EVENT_CHOICES)
    booking_snapshot = models.JSONField(default=dict)
    service = models.ForeignKey('Service', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    start = models.DateTimeField(null=True, blank=True)
    end = models.DateTimeField(null=True, blank=True)
    client_name = models.CharField(max_length=200, blank=True)
    client_email = models.EmailField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    extra = models.TextField(blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['organization', 'created_at']),
            models.Index(fields=['service', 'start']),
        ]

    def __str__(self):
        return f"Audit {self.event_type} booking {self.booking_id or 'unknown'} @ {self.start or 'unknown'}"