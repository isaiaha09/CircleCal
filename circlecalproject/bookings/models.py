from django.db import models
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.contrib.auth.models import User
from accounts.models import Business as Organization, Membership

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

    def __str__(self):
        return f"{self.name} ({self.organization.name})"

    class Meta:
        indexes = [
            models.Index(fields=["organization", "is_active"]),
        ]


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

	def __str__(self):
		return f"{self.title or 'Booking'} ({self.start.date()})"

	class Meta:
		indexes = [
			models.Index(fields=["organization", "start"]),
			models.Index(fields=["service", "start"]),
		]
	

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

    def __str__(self):
        return f"Settings for {self.organization.name}"


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
        return f"{self.organization.slug} {self.weekday} {self.start_time}-{self.end_time}"

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
        return f"svc-{self.service.id} {self.weekday} {self.start_time}-{self.end_time}"

    def clean(self):
        if self.end_time <= self.start_time:
            raise ValidationError("end_time must be after start_time")

        # Validate that this service window is within the organization's weekly availability
        try:
            org = self.service.organization
        except Exception:
            org = None

        if org:
            # find any org weekly window that fully contains this service window
            matches = WeeklyAvailability.objects.filter(
                organization=org,
                weekday=self.weekday,
                is_active=True,
                start_time__lte=self.start_time,
                end_time__gte=self.end_time,
            )
            if not matches.exists():
                raise ValidationError("Service availability window must be within the organization's weekly availability. Update the organization's calendar to allow this time.")


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
        return f"Freeze for {self.service} on {self.date}"