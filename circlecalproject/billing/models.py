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
    # If a downgrade (or any plan change) is requested to take effect at the
    # end of the current billing period, store the target plan and the date
    # when the change should be applied. This supports the "upgrades now,
    # downgrades at period end" policy.
    scheduled_plan = models.ForeignKey(Plan, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    scheduled_change_at = models.DateTimeField(null=True, blank=True)
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


class PaymentMethod(models.Model):
    """Cached non-sensitive payment method metadata for faster UI and invoice rendering.

    Stores only metadata (brand, last4, exp_month, exp_year) and the Stripe payment_method id.
    Keep this in sync via webhooks and update operations.
    """
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='payment_methods')
    stripe_pm_id = models.CharField(max_length=255, unique=True)
    brand = models.CharField(max_length=50, blank=True, null=True)
    last4 = models.CharField(max_length=8, blank=True, null=True)
    exp_month = models.IntegerField(null=True, blank=True)
    exp_year = models.IntegerField(null=True, blank=True)
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["organization", "is_default"]),
        ]

    def __str__(self):
        return f"PM {self.stripe_pm_id} @ {self.organization}" 


class SubscriptionChange(models.Model):
    """Represents a scheduled or processed subscription change (upgrade/downgrade).

    This is a local, non-Stripe object used to surface scheduled changes in the
    billing UI as pseudo-invoices (commonly shown as $0.00 entries with no card).
    """
    STATUS_CHOICES = [
        ("scheduled", "Scheduled"),
        ("processed", "Processed"),
        ("cancelled", "Cancelled"),
    ]

    CHANGE_TYPE_CHOICES = [
        ("downgrade", "Downgrade"),
        ("upgrade", "Upgrade"),
        ("other", "Other"),
    ]

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='subscription_changes')
    subscription = models.ForeignKey('Subscription', on_delete=models.SET_NULL, null=True, blank=True, related_name='changes')
    change_type = models.CharField(max_length=20, choices=CHANGE_TYPE_CHOICES, default='other')
    new_plan = models.ForeignKey(Plan, on_delete=models.SET_NULL, null=True, blank=True)
    effective_at = models.DateTimeField(null=True, blank=True)
    amount_cents = models.IntegerField(default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='scheduled')
    note = models.TextField(blank=True, null=True)
    card_brand = models.CharField(max_length=50, blank=True, null=True)
    card_last4 = models.CharField(max_length=8, blank=True, null=True)
    stripe_invoice_id = models.CharField(max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"SubscriptionChange {self.id} for {self.organization} -> {self.new_plan.name if self.new_plan else 'N/A'}"
