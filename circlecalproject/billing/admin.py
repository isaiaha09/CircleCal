from django.contrib import admin
from billing.models import Plan, Subscription


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