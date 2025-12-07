from django.db import models
from django.utils import timezone
from accounts.models import Business as Organization
from django.conf import settings
from django.contrib.auth import get_user_model


class InvoiceMeta(models.Model):
    """Local metadata for invoices and pseudo-invoices to allow hiding/voiding and audits.

    This does not duplicate Stripe data; it simply stores flags tied to either a
    `stripe_invoice_id` or a `subscription_change` (pseudo-invoice) for UI purposes.
    """
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='invoice_meta')
    stripe_invoice_id = models.CharField(max_length=255, blank=True, null=True, db_index=True)
    subscription_change = models.ForeignKey('SubscriptionChange', on_delete=models.CASCADE, null=True, blank=True, related_name='+')
    hidden = models.BooleanField(default=False)
    voided = models.BooleanField(default=False)
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    void_reason = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['organization', 'stripe_invoice_id'])]

    def __str__(self):
        if self.stripe_invoice_id:
            return f"InvoiceMeta {self.stripe_invoice_id} @ {self.organization}"
        if self.subscription_change_id:
            return f"InvoiceMeta change:{self.subscription_change_id} @ {self.organization}"
        return f"InvoiceMeta {self.id} @ {self.organization}"


class InvoiceActionLog(models.Model):
    invoice_meta = models.ForeignKey(InvoiceMeta, on_delete=models.CASCADE, related_name='actions')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=50)  # e.g., 'hide', 'void'
    reason = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.action} by {self.user} on {self.created_at}"

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


class DiscountCode(models.Model):
    """Admin-created discount codes that can be applied to specific users.

    These are stored locally; applying them to real billing (Stripe) is
    optional and can be added later by creating Stripe Coupons/PromotionCodes
    and linking via `stripe_coupon_id`.
    """
    code = models.CharField(max_length=64, unique=True, db_index=True)
    description = models.TextField(blank=True, null=True)

    # Either percent_off (0-100) or amount_off_cents will be used
    percent_off = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    amount_off_cents = models.IntegerField(null=True, blank=True)
    currency = models.CharField(max_length=10, default='USD')

    # Duration: number of days the discount applies after being applied (null => indefinite)
    duration_days = models.IntegerField(null=True, blank=True)

    # Which users this discount applies to (admin selects users in admin UI)
    users = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name='discount_codes')

    active = models.BooleanField(default=True)
    start_date = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    # Optional: store created_by for audit
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    # Optional linkage to Stripe coupon/promotion for future integration
    stripe_coupon_id = models.CharField(max_length=255, null=True, blank=True)
    stripe_promotion_code_id = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Discount {self.code} ({'active' if self.active else 'inactive'})"

    def is_valid(self):
        if not self.active:
            return False
        now = timezone.now()
        if self.start_date and now < self.start_date:
            return False
        if self.expires_at and now > self.expires_at:
            return False
        return True

    def create_stripe_coupon_and_promotion(self):
        """Create a Stripe Coupon (and PromotionCode) for this DiscountCode and save the IDs.

        Duration mapping: if `duration_days` is None -> 'forever'. If set -> use 'repeating'
        and approximate months = max(1, ceil(days/30)). This is a pragmatic approximation;
        admins should be aware it's month-based on Stripe side.
        """
        import stripe
        from django.conf import settings
        from math import ceil

        stripe.api_key = settings.STRIPE_SECRET_KEY

        if self.stripe_coupon_id:
            # Already created
            return self.stripe_coupon_id, self.stripe_promotion_code_id

        coupon_kwargs = {}
        if self.percent_off:
            coupon_kwargs['percent_off'] = float(self.percent_off)
        elif self.amount_off_cents:
            coupon_kwargs['amount_off'] = int(self.amount_off_cents)
            coupon_kwargs['currency'] = self.currency.lower()
        else:
            raise ValueError('Discount must have percent_off or amount_off_cents')

        if self.duration_days:
            months = max(1, int(ceil(self.duration_days / 30.0)))
            coupon_kwargs['duration'] = 'repeating'
            coupon_kwargs['duration_in_months'] = months
        else:
            coupon_kwargs['duration'] = 'forever'

        # Create coupon
        c = stripe.Coupon.create(name=self.code, **coupon_kwargs)
        self.stripe_coupon_id = c.id
        self.save(update_fields=['stripe_coupon_id'])

        # Create promotion code so admins can reference codes (optional)
        try:
            pc = stripe.PromotionCode.create(coupon=c.id, code=self.code)
            self.stripe_promotion_code_id = pc.id
            self.save(update_fields=['stripe_promotion_code_id'])
        except Exception:
            # Promotion code creation is optional; swallow exceptions but keep coupon
            pass

        return self.stripe_coupon_id, self.stripe_promotion_code_id

    def apply_to_organization(self, org, proration_behavior='create_prorations', applied_by=None, source_user=None):
        """Apply this discount to an Organization's active subscription in Stripe.

        This will attempt to set the coupon on the subscription. Returns (success, message).
        """
        import stripe
        from django.conf import settings
        from billing.models import Subscription

        stripe.api_key = settings.STRIPE_SECRET_KEY

        if not self.stripe_coupon_id:
            self.create_stripe_coupon_and_promotion()

        if not org.stripe_customer_id:
            return False, f"Org {org} has no Stripe customer id"

        # Find active subscription for org
        sub = Subscription.objects.filter(organization=org).first()
        if not sub or not sub.stripe_subscription_id:
            return False, f"No Stripe subscription found for org {org}"

        try:
            stripe.Subscription.modify(
                sub.stripe_subscription_id,
                coupon=self.stripe_coupon_id,
                proration_behavior=proration_behavior,
            )
            # Record local AppliedDiscount audit
            try:
                AppliedDiscount.objects.create(
                    subscription=sub,
                    discount_code=self,
                    applied_by=applied_by,
                    source_user=source_user,
                    proration_behavior=proration_behavior,
                    stripe_coupon_id=self.stripe_coupon_id,
                    stripe_promotion_code_id=self.stripe_promotion_code_id,
                    active=True,
                )
            except Exception:
                # Don't fail the operation if the local audit record cannot be created
                pass
            return True, "Applied to subscription"
        except Exception as e:
            return False, str(e)


class AppliedDiscount(models.Model):
    """Records when a DiscountCode was applied to a Subscription (local audit).

    Keep this separate from InvoiceMeta/InvoiceActionLog because it represents
    a persistent application of a discount to a subscription rather than an
    action tied to a specific invoice.
    """
    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE, related_name='applied_discounts')
    discount_code = models.ForeignKey('DiscountCode', on_delete=models.CASCADE, related_name='applied_instances')
    applied_at = models.DateTimeField(auto_now_add=True)
    applied_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    # The user for whom this discount was applied (the target user selected by admin)
    source_user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    proration_behavior = models.CharField(max_length=32, choices=[('create_prorations','create_prorations'),('none','none')], default='create_prorations')
    stripe_coupon_id = models.CharField(max_length=255, null=True, blank=True)
    stripe_promotion_code_id = models.CharField(max_length=255, null=True, blank=True)
    active = models.BooleanField(default=True)
    removed_at = models.DateTimeField(null=True, blank=True)
    removed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        ordering = ['-applied_at']

    def __str__(self):
        return f"{self.discount_code.code} @ {self.subscription.organization}"

    def deactivate(self, removed_by=None):
        self.active = False
        from django.utils import timezone as dj_tz
        self.removed_at = dj_tz.now()
        if removed_by:
            self.removed_by = removed_by
        self.save()
