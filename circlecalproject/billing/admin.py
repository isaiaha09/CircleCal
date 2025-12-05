from django.contrib import admin
from billing.models import Plan, Subscription, SubscriptionChange


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