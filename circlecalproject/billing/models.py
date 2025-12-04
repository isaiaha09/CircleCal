from django.db import models
from django.utils import timezone
from accounts.models import Business as Organization

class Plan(models.Model):
    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True)
    description = models.TextField(blank=True)

    price = models.DecimalField(max_digits=8, decimal_places=2, default=0.00)
    billing_period = models.CharField(max_length=20, choices=[("monthly", "Monthly"), ("yearly", "Yearly")])

    # Feature limits
    max_coaches = models.PositiveIntegerField(default=1)
    max_services = models.PositiveIntegerField(default=5)
    max_bookings_per_month = models.PositiveIntegerField(default=50)

    allow_custom_branding = models.BooleanField(default=False)
    allow_priority_support = models.BooleanField(default=False)
    allow_payment_processing = models.BooleanField(default=False)

    # Stripe integration
    stripe_price_id = models.CharField(max_length=255, blank=True, null=True)

    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name



class Subscription(models.Model):
    organization = models.OneToOneField(
        Organization,
        on_delete=models.CASCADE,
        related_name="subscription"
    )

    plan = models.ForeignKey(Plan, on_delete=models.SET_NULL, null=True, blank=True)
    status = models.CharField(max_length=20, default="active", choices=[
        ("active", "Active"),
        ("trialing", "Trialing"),
        ("past_due", "Past Due"),
        ("canceled", "Canceled"),
        ("expired", "Expired"),
    ])

    start_date = models.DateTimeField(default=timezone.now)
    end_date = models.DateTimeField(null=True, blank=True)

    stripe_subscription_id = models.CharField(max_length=255, blank=True, null=True)
    cancel_at_period_end = models.BooleanField(default=False)
    # Added fields referenced by webhook logic
    active = models.BooleanField(default=True)
    current_period_end = models.DateTimeField(null=True, blank=True)
    trial_end = models.DateTimeField(null=True, blank=True)

    def is_active(self):
        # Prefer explicit active flag if maintained by webhook; fallback to status/end_date
        if self.active:
            return True
        # Trial: allow until trial_end
        if self.status == "trialing" and self.trial_end and timezone.now() < self.trial_end:
            return True
        if self.end_date and timezone.now() < self.end_date:
            return True
        return False

    def __str__(self):
        return f"{self.organization.name} — {self.plan.name if self.plan else 'No plan'}"
