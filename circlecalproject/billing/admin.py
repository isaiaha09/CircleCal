from django.contrib import admin
from billing.models import (
    Plan,
    Subscription,
    SubscriptionChange,
    InvoiceMeta,
    InvoiceActionLog,
    DiscountCode,
)
from billing.models import AppliedDiscount
from django import forms
from django.forms import ModelChoiceField
from django.utils.html import format_html
from accounts.models import Business
from django.contrib import messages
from accounts.models import Business as Organization
from django import forms
from django.contrib.admin import widgets as admin_widgets
from django.contrib.auth import get_user_model
from accounts.models import Membership
from django.urls import path
from django.http import JsonResponse
from django.utils.safestring import mark_safe

User = get_user_model()


class UserWithDetailsChoiceField(forms.ModelMultipleChoiceField):
    def label_from_instance(self, user):
        # Username
        username = getattr(user, 'username', '')
        # Full name if present
        full_name = (getattr(user, 'first_name', '') or '')
        last = getattr(user, 'last_name', '')
        if full_name or last:
            full = (full_name + ' ' + last).strip()
        else:
            full = ''

        # Determine a primary organization (first active membership)
        org = None
        try:
            m = Membership.objects.filter(user=user, is_active=True).select_related('organization').first()
            if m:
                org = m.organization
        except Exception:
            org = None

        org_name = org.name if org else 'No business'
        # Determine current plan
        plan_name = 'No subscription'
        try:
            if org and hasattr(org, 'subscription') and getattr(org.subscription, 'plan', None):
                plan_name = org.subscription.plan.name
        except Exception:
            plan_name = 'No subscription'

        parts = [username]
        if full:
            parts.append(full)
        parts.append(f"{org_name}")
        parts.append(f"Plan: {plan_name}")
        return ' — '.join(parts)


class DiscountCodeAdminForm(forms.ModelForm):
    users = UserWithDetailsChoiceField(
        queryset=User.objects.all().order_by('username'),
        required=False,
        widget=admin_widgets.FilteredSelectMultiple('Users', is_stacked=False)
    )

    class Meta:
        model = DiscountCode
        fields = '__all__'


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "slug",
        "price",
        "billing_period",
        "max_coaches",
        "max_services",
        "max_bookings_per_month",
        "is_active",
    )
    list_filter = ("billing_period", "is_active")
    search_fields = ("name", "slug")


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = (
        "organization",
        "plan",
        "status",
        "start_date",
        "end_date",
        "stripe_subscription_id",
    )
    list_filter = ("status",)
    search_fields = ("organization__name", "plan__name", "stripe_subscription_id")


@admin.register(SubscriptionChange)
class SubscriptionChangeAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'organization',
        'subscription',
        'change_type',
        'new_plan',
        'effective_at',
        'amount_cents',
        'status',
        'created_at',
    )
    list_filter = ('change_type', 'status', 'created_at')
    search_fields = ('organization__name', 'subscription__stripe_subscription_id', 'new_plan__name')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(InvoiceMeta)
class InvoiceMetaAdmin(admin.ModelAdmin):
    list_display = ('id', 'organization', 'stripe_invoice_id', 'subscription_change', 'hidden', 'voided', 'voided_at')
    list_filter = ('hidden', 'voided', 'organization')
    search_fields = ('stripe_invoice_id', 'organization__name')


@admin.register(InvoiceActionLog)
class InvoiceActionLogAdmin(admin.ModelAdmin):
    list_display = ('invoice_meta', 'user', 'action', 'created_at')
    list_filter = ('action', 'created_at')
    search_fields = ('invoice_meta__stripe_invoice_id', 'user__username')


@admin.register(DiscountCode)
class DiscountCodeAdmin(admin.ModelAdmin):
    list_display = ('code', 'description_short', 'percent_off', 'amount_off_cents', 'currency', 'active', 'start_date', 'expires_at', 'created_at')
    list_filter = ('active', 'start_date', 'expires_at')
    search_fields = ('code', 'description', 'stripe_coupon_id')
    # Use a custom ModelForm with a richer users field
    form = DiscountCodeAdminForm
    readonly_fields = ('created_at',)
    actions = ('make_active', 'make_inactive')

    from django import forms
    from django.contrib.admin import widgets as admin_widgets
    from django.contrib.auth import get_user_model
    from accounts.models import Membership

    User = get_user_model()

    class UserWithDetailsChoiceField(forms.ModelMultipleChoiceField):
        def label_from_instance(self, user):
            # Username
            username = getattr(user, 'username', '')
            # Full name if present
            full_name = (getattr(user, 'first_name', '') or '')
            last = getattr(user, 'last_name', '')
            if full_name or last:
                full = (full_name + ' ' + last).strip()
            else:
                full = ''

            # Determine a primary organization (first active membership)
            org = None
            try:
                m = Membership.objects.filter(user=user, is_active=True).select_related('organization').first()
                if m:
                    org = m.organization
            except Exception:
                org = None

            org_name = org.name if org else 'No business'
            # Determine current plan
            plan_name = 'No subscription'
            try:
                if org and hasattr(org, 'subscription') and getattr(org.subscription, 'plan', None):
                    plan_name = org.subscription.plan.name
            except Exception:
                plan_name = 'No subscription'

            parts = [username]
            if full:
                parts.append(full)
            parts.append(f"{org_name}")
            parts.append(f"Plan: {plan_name}")
            return ' — '.join(parts)

    
    def description_short(self, obj):
        return (obj.description[:60] + '...') if obj.description and len(obj.description) > 60 else obj.description
    description_short.short_description = 'Description'

    def make_active(self, request, queryset):
        updated = queryset.update(active=True)
        self.message_user(request, f"Marked {updated} discount(s) active")
    make_active.short_description = 'Mark selected discounts active'

    def make_inactive(self, request, queryset):
        updated = queryset.update(active=False)
        self.message_user(request, f"Marked {updated} discount(s) inactive")
    make_inactive.short_description = 'Mark selected discounts inactive'

    def create_stripe_resources(self, request, queryset):
        """Admin action: create Stripe Coupon + PromotionCode for selected DiscountCodes."""
        successes = 0
        errors = []
        for d in queryset:
            try:
                d.create_stripe_coupon_and_promotion()
                successes += 1
            except Exception as e:
                errors.append(f"{d.code}: {e}")
        msg = f"Created Stripe resources for {successes} discount(s)."
        if errors:
            msg += " Errors: " + "; ".join(errors[:5])
        self.message_user(request, msg)
    create_stripe_resources.short_description = 'Create Stripe Coupon/Promotion for selected discounts'

    def apply_to_users_prorate(self, request, queryset):
        """Admin action: apply selected DiscountCodes to their assigned users' organizations with proration."""
        results = []
        for d in queryset:
            # ensure stripe coupon exists
            try:
                d.create_stripe_coupon_and_promotion()
            except Exception as e:
                results.append(f"{d.code}: stripe create failed: {e}")
                continue
            for u in d.users.all():
                # find active memberships
                for m in u.memberships.filter(is_active=True):
                    org = m.organization
                    ok, msg = d.apply_to_organization(org, proration_behavior='create_prorations', applied_by=request.user, source_user=u)
                    results.append(f"{d.code} -> {org.slug}: {ok} ({msg})")
        self.message_user(request, "\n".join(results[:20]))
    apply_to_users_prorate.short_description = 'Apply discounts to selected users (immediate/prorate)'

    def apply_to_users_period_end(self, request, queryset):
        """Admin action: apply selected DiscountCodes to their assigned users' organizations at period end (no proration)."""
        results = []
        for d in queryset:
            try:
                d.create_stripe_coupon_and_promotion()
            except Exception as e:
                results.append(f"{d.code}: stripe create failed: {e}")
                continue
            for u in d.users.all():
                for m in u.memberships.filter(is_active=True):
                    org = m.organization
                    ok, msg = d.apply_to_organization(org, proration_behavior='none', applied_by=request.user, source_user=u)
                    results.append(f"{d.code} -> {org.slug}: {ok} ({msg})")
        self.message_user(request, "\n".join(results[:20]))
    apply_to_users_period_end.short_description = 'Apply discounts to selected users (period end/no proration)'

    actions = ('make_active', 'make_inactive', 'create_stripe_resources', 'apply_to_users_prorate', 'apply_to_users_period_end')

    def deactivate_applied_discounts(self, request, queryset):
        """Admin action: mark AppliedDiscount records as inactive for organizations/users tied to selected DiscountCodes (local only)."""
        results = []
        for d in queryset:
            # find applied discounts for this code
            ads = AppliedDiscount.objects.filter(discount_code=d, active=True)
            for ad in ads:
                ad.deactivate(removed_by=request.user)
                results.append(f"Deactivated {d.code} @ {ad.subscription.organization.slug}")
        if not results:
            self.message_user(request, "No active applied discounts found for selected codes.")
        else:
            self.message_user(request, "\n".join(results[:50]))
    deactivate_applied_discounts.short_description = 'Deactivate applied discounts (local only)'

    def remove_coupon_and_deactivate(self, request, queryset):
        """Admin action: remove coupon from subscription in Stripe (if possible) and deactivate local AppliedDiscount.

        This will attempt to call `stripe.Subscription.delete_discount(subscription_id)`
        for each active AppliedDiscount tied to the selected DiscountCodes. Failures are reported per-org.
        """
        import stripe
        from django.conf import settings
        stripe.api_key = settings.STRIPE_SECRET_KEY

        results = []
        for d in queryset:
            ads = AppliedDiscount.objects.filter(discount_code=d, active=True)
            for ad in ads:
                sub = ad.subscription
                try:
                    if sub.stripe_subscription_id:
                        try:
                            stripe.Subscription.delete_discount(sub.stripe_subscription_id)
                            results.append(f"Removed coupon for {sub.organization.slug}")
                        except Exception as e:
                            results.append(f"Stripe remove failed for {sub.organization.slug}: {e}")
                    else:
                        results.append(f"No stripe_subscription_id for {sub.organization.slug}")
                except Exception as e:
                    results.append(f"Error for {sub.organization.slug}: {e}")
                try:
                    ad.deactivate(removed_by=request.user)
                except Exception:
                    pass

        if not results:
            self.message_user(request, "No active applied discounts found for selected codes.")
        else:
            self.message_user(request, "\n".join(results[:50]))
    remove_coupon_and_deactivate.short_description = 'Remove coupon in Stripe and deactivate applied discounts'

    # extend actions
    actions = ('make_active', 'make_inactive', 'create_stripe_resources', 'apply_to_users_prorate', 'apply_to_users_period_end', 'deactivate_applied_discounts', 'remove_coupon_and_deactivate')


@admin.register(AppliedDiscount)
class AppliedDiscountAdmin(admin.ModelAdmin):
    list_display = ('discount_code', 'users_applied', 'business_name', 'current_plan', 'applied_at', 'applied_by', 'active')
    list_filter = ('active', 'applied_at')
    search_fields = ('discount_code__code', 'subscription__organization__name', 'subscription__stripe_subscription_id')
    readonly_fields = ('applied_at',)

    def users_applied(self, obj):
        """Return comma-separated list of usernames and full names for users
        who were the source of this applied discount for the subscription's org.
        """
        try:
            # Prefer the recorded source_user on the AppliedDiscount record
            if getattr(obj, 'source_user', None):
                u = obj.source_user
                uname = getattr(u, 'username', '')
                first = getattr(u, 'first_name', '') or ''
                last = getattr(u, 'last_name', '') or ''
                full = (first + ' ' + last).strip()
                return f"{uname} ({full})" if full else uname
            # Fallback: try to list discount_code.users who are members of this org
            users = obj.discount_code.users.filter(memberships__organization=obj.subscription.organization).distinct()
            labels = []
            for u in users:
                uname = getattr(u, 'username', '')
                first = getattr(u, 'first_name', '') or ''
                last = getattr(u, 'last_name', '') or ''
                full = (first + ' ' + last).strip()
                if full:
                    labels.append(f"{uname} ({full})")
                else:
                    labels.append(f"{uname}")
            return ', '.join(labels) if labels else '(none)'
        except Exception:
            return '(error)'
    users_applied.short_description = 'User(s)'

    def business_name(self, obj):
        try:
            return obj.subscription.organization.name
        except Exception:
            return ''
    business_name.short_description = 'Business'

    def current_plan(self, obj):
        try:
            p = getattr(obj.subscription, 'plan', None)
            return p.name if p else 'No subscription'
        except Exception:
            return 'Unknown'
    current_plan.short_description = 'Plan'
    # Use custom form so the subscription chooser shows user/org/plan details
    form = None  # will be set after AppliedDiscountAdminForm is defined

    class Media:
        js = ('admin/js/jquery.init.js', 'billing/admin_discount_dynamic.js')

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('subscriptions-for-code/', self.admin_site.admin_view(self.subscriptions_for_code), name='billing_applieddiscount_subscriptions_for_code'),
        ]
        return custom_urls + urls

    def subscriptions_for_code(self, request):
        """Return JSON list of subscriptions relevant to the DiscountCode id passed as ?code_id=.."""
        code_id = request.GET.get('code_id')
        data = []
        try:
            print(f"[subscriptions_for_code] code_id={code_id}")
            dc = DiscountCode.objects.get(id=code_id)
            users = list(dc.users.all())
            print(f"[subscriptions_for_code] users=", [(u.id, getattr(u,'username',None)) for u in users])
            org_ids = set(Membership.objects.filter(user__in=users).values_list('organization_id', flat=True))
            print(f"[subscriptions_for_code] org_ids=", org_ids)
            subs = list(Subscription.objects.filter(organization_id__in=org_ids).select_related('organization', 'plan'))
            print(f"[subscriptions_for_code] found subs=", [(s.id, getattr(s.organization,'id',None)) for s in subs])
            field = SubscriptionWithUserChoiceField(queryset=Subscription.objects.none())
            for s in subs:
                label = SubscriptionWithUserChoiceField(queryset=Subscription.objects.none()).label_from_instance(s)
                data.append({'id': s.id, 'label': label})
        except Exception as e:
            print(f"[subscriptions_for_code] exception: {e}")
            raise
        return JsonResponse({'data': data})


class SubscriptionWithUserChoiceField(ModelChoiceField):
    def label_from_instance(self, sub):
        # subscription string (organization — plan)
        org = getattr(sub, 'organization', None)
        plan = getattr(sub, 'plan', None)
        org_name = org.name if org else 'Unknown org'
        plan_name = plan.name if plan else 'No plan'

        # Try to show a representative username and full name: prefer organization.owner
        uname = ''
        full = ''
        try:
            owner = getattr(org, 'owner', None)
            if owner:
                uname = getattr(owner, 'username', '')
                first = getattr(owner, 'first_name', '') or ''
                last = getattr(owner, 'last_name', '') or ''
                full = (first + ' ' + last).strip()
        except Exception:
            pass

        # If no owner username found, try any active membership user for the org
        if not uname:
            try:
                m = Membership.objects.filter(organization=org, is_active=True).select_related('user').first()
                if m and getattr(m, 'user', None):
                    u = m.user
                    uname = getattr(u, 'username', '')
                    first = getattr(u, 'first_name', '') or ''
                    last = getattr(u, 'last_name', '') or ''
                    full = (first + ' ' + last).strip()
            except Exception:
                pass

        # Build list of membership users for this org (username (Full Name / role / email))
        member_labels = []
        try:
            mems = Membership.objects.filter(organization=org, is_active=True).select_related('user')
            for m in mems:
                u = getattr(m, 'user', None)
                if not u:
                    continue
                uname_m = getattr(u, 'username', '')
                first_m = getattr(u, 'first_name', '') or ''
                last_m = getattr(u, 'last_name', '') or ''
                full_m = (first_m + ' ' + last_m).strip()
                email = getattr(u, 'email', '')
                role = getattr(m, 'role', '')
                parts_user = uname_m
                extras = []
                if full_m:
                    extras.append(full_m)
                if role:
                    extras.append(role)
                if email:
                    extras.append(email)
                if extras:
                    parts_user = f"{parts_user} ({' / '.join(extras)})"
                member_labels.append(parts_user)
        except Exception:
            member_labels = []

        parts = []
        if member_labels:
            parts.append(', '.join(member_labels))
        parts.append(org_name)
        parts.append(f"Plan: {plan_name}")

        return ' — '.join(parts)


class AppliedDiscountAdminForm(forms.ModelForm):
    # Render as a select dropdown server-side (so UI is a dropdown even if JS doesn't run).
    subscription = SubscriptionWithUserChoiceField(
        queryset=Subscription.objects.none(),
        widget=forms.Select(attrs={'class': 'vLargeTextField'})
    )

    class Meta:
        model = AppliedDiscount
        # Ensure discount_code appears above subscription in the admin form
        fields = (
            'discount_code',
            'subscription',
            'source_user',
            'applied_by',
            'proration_behavior',
            'stripe_coupon_id',
            'stripe_promotion_code_id',
            'active',
            'removed_at',
            'removed_by',
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # If editing an existing AppliedDiscount, include its subscription in the queryset
        try:
            inst = getattr(self, 'instance', None)
            if inst and getattr(inst, 'subscription', None):
                self.fields['subscription'].queryset = Subscription.objects.filter(pk=inst.subscription.pk).select_related('organization', 'plan')
            else:
                # If this is a POST (form submission) and a subscription id was provided,
                # include that subscription in the queryset so ModelChoiceField validation succeeds.
                try:
                    sub_val = None
                    if hasattr(self, 'data') and self.data:
                        sub_val = self.data.get('subscription')
                    if sub_val:
                        self.fields['subscription'].queryset = Subscription.objects.filter(pk=sub_val).select_related('organization', 'plan')
                except Exception:
                    # keep empty queryset on failure
                    pass
        except Exception:
            # fall back to empty queryset
            self.fields['subscription'].queryset = Subscription.objects.none()

# Now set the form on the admin class
AppliedDiscountAdmin.form = AppliedDiscountAdminForm
