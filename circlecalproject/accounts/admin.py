from django.contrib import admin
from .models import (
    Business,
    Membership,
    Invite,
    BusinessSlugRedirect,
    Team,
    TeamMembership,
)
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html

from circlecalproject.unfold_admin import UnfoldActionForm

try:
    from unfold.admin import ModelAdmin as UnfoldModelAdmin

    BaseAdmin = UnfoldModelAdmin
except Exception:  # pragma: no cover
    BaseAdmin = admin.ModelAdmin

@admin.register(Business)
class OrganizationAdmin(BaseAdmin):
    list_display = (
        "name",
        "slug",
        "owner",
        "is_archived",
        "timezone",
        "custom_domain_verified",
        "created_at",
    )
    search_fields = ("name", "slug", "owner__username", "owner__email", "custom_domain")
    list_filter = (
        "is_archived",
        "embed_enabled",
        "custom_domain_verified",
        "stripe_connect_charges_enabled",
        "stripe_connect_payouts_enabled",
    )
    ordering = ("-created_at",)
    autocomplete_fields = ("owner",)

    list_filter_submit = True
    list_fullwidth = True

    readonly_fields = (
        "created_at",
        "ops_subscription",
        "ops_stripe_connect",
        "ops_custom_domain",
        "ops_embed",
        "ops_bookings_30d",
        "ops_last_booking",
        "ops_quick_links",
    )

    fieldsets = (
        (
            "General",
            {
                "classes": ("tab",),
                "fields": (
                    "name",
                    "slug",
                    "owner",
                    "timezone",
                    "is_archived",
                    "created_at",
                ),
            },
        ),
        (
            "Integrations",
            {
                "classes": ("tab",),
                "fields": (
                    "stripe_customer_id",
                    "stripe_connect_account_id",
                    "stripe_connect_details_submitted",
                    "stripe_connect_charges_enabled",
                    "stripe_connect_payouts_enabled",
                    "embed_enabled",
                    "embed_key",
                    "custom_domain",
                    "custom_domain_verification_token",
                    "custom_domain_verified",
                    "custom_domain_verified_at",
                ),
            },
        ),
        (
            "Ops",
            {
                "classes": ("tab",),
                "fields": (
                    "ops_subscription",
                    "ops_stripe_connect",
                    "ops_custom_domain",
                    "ops_embed",
                    "ops_bookings_30d",
                    "ops_last_booking",
                    "ops_quick_links",
                ),
            },
        ),
    )

    def ops_subscription(self, obj: Business) -> str:
        try:
            sub = getattr(obj, "subscription", None)
        except Exception:
            sub = None
        if not sub:
            return "No subscription"
        plan = getattr(getattr(sub, "plan", None), "name", None) or "No plan"
        status = getattr(sub, "status", None) or "unknown"
        active_flag = getattr(sub, "active", None)
        if active_flag is True:
            active = "active=True"
        elif active_flag is False:
            active = "active=False"
        else:
            active = "active=?"
        return f"{plan} • {status} • {active}"

    ops_subscription.short_description = "Subscription"

    def ops_stripe_connect(self, obj: Business) -> str:
        acct = (getattr(obj, "stripe_connect_account_id", None) or "").strip()
        submitted = bool(getattr(obj, "stripe_connect_details_submitted", False))
        charges = bool(getattr(obj, "stripe_connect_charges_enabled", False))
        payouts = bool(getattr(obj, "stripe_connect_payouts_enabled", False))
        if not acct:
            return "Not connected"
        return f"{acct} • submitted={submitted} • charges={charges} • payouts={payouts}"

    ops_stripe_connect.short_description = "Stripe Connect"

    def ops_custom_domain(self, obj: Business) -> str:
        domain = (getattr(obj, "custom_domain", None) or "").strip() or "(none)"
        verified = bool(getattr(obj, "custom_domain_verified", False))
        return f"{domain} • verified={verified}"

    ops_custom_domain.short_description = "Custom domain"

    def ops_embed(self, obj: Business) -> str:
        enabled = bool(getattr(obj, "embed_enabled", False))
        key = (getattr(obj, "embed_key", None) or "").strip()
        return f"enabled={enabled} • key={'set' if key else 'unset'}"

    ops_embed.short_description = "Embed"

    def ops_bookings_30d(self, obj: Business) -> int:
        from datetime import timedelta

        from bookings.models import Booking

        now = timezone.now()
        start = now - timedelta(days=30)
        return Booking.objects.filter(organization=obj, start__gte=start, start__lt=now).count()

    ops_bookings_30d.short_description = "Bookings (30d)"

    def ops_last_booking(self, obj: Business) -> str:
        from bookings.models import Booking

        last = (
            Booking.objects.filter(organization=obj)
            .order_by("-start")
            .values_list("start", flat=True)
            .first()
        )
        if not last:
            return "-"
        return timezone.localtime(last).strftime("%Y-%m-%d %H:%M")

    ops_last_booking.short_description = "Last booking"

    def ops_quick_links(self, obj: Business) -> str:
        public_org = reverse("bookings:public_org_page", kwargs={"org_slug": obj.slug})
        org_dashboard = reverse("calendar_app:dashboard", kwargs={"org_slug": obj.slug})
        org_bookings = reverse("calendar_app:bookings_list", kwargs={"org_slug": obj.slug})
        org_services = reverse("calendar_app:services_page", kwargs={"org_slug": obj.slug})
        org_resources = reverse("calendar_app:resources_page", kwargs={"org_slug": obj.slug})

        return format_html(
            " ".join(
                [
                    '<a class="text-primary-600 hover:underline" href="{}" target="_blank" rel="noopener">Public page</a>',
                    '<a class="text-primary-600 hover:underline" href="{}" target="_blank" rel="noopener">Org dashboard</a>',
                    '<a class="text-primary-600 hover:underline" href="{}" target="_blank" rel="noopener">Bookings</a>',
                    '<a class="text-primary-600 hover:underline" href="{}" target="_blank" rel="noopener">Services</a>',
                    '<a class="text-primary-600 hover:underline" href="{}" target="_blank" rel="noopener">Resources</a>',
                ]
            ),
            public_org,
            org_dashboard,
            org_bookings,
            org_services,
            org_resources,
        )

    ops_quick_links.short_description = "Quick links"


@admin.register(Membership)
class MembershipAdmin(BaseAdmin):
    list_display = ("user", "organization", "role", "is_active", "created_at")
    list_filter = ("role", "is_active")
    search_fields = ("user__username", "organization__name")

    list_filter_submit = True
    list_fullwidth = True


@admin.register(Team)
class TeamAdmin(BaseAdmin):
    list_display = ("name", "organization", "slug", "created_at")
    search_fields = ("name", "slug", "organization__name", "organization__slug")
    list_filter = ("organization",)
    ordering = ("organization", "name")
    autocomplete_fields = ("organization",)

    list_filter_submit = True
    list_fullwidth = True


@admin.register(TeamMembership)
class TeamMembershipAdmin(BaseAdmin):
    list_display = ("team", "user", "role", "is_active", "created_at")
    search_fields = (
        "team__name",
        "user__username",
        "user__email",
        "team__organization__name",
    )
    list_filter = ("role", "is_active")
    autocomplete_fields = ("team", "user")

    list_filter_submit = True
    list_fullwidth = True

# Business is now the primary model; no separate proxy registration needed.

admin.site.register(Invite)


@admin.register(BusinessSlugRedirect)
class BusinessSlugRedirectAdmin(BaseAdmin):
    list_display = ("old_slug", "business", "created_at")
    search_fields = ("old_slug", "business__name", "business__slug")
    ordering = ("-created_at",)

    list_fullwidth = True


# Ensure Unfold bulk actions work in the built-in User changelist.
User = get_user_model()
try:
    admin.site.unregister(User)
except admin.sites.NotRegistered:
    pass


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    action_form = UnfoldActionForm
