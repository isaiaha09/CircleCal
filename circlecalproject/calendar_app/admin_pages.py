from __future__ import annotations

import json
from datetime import timedelta

from django.contrib import admin
from django.db.models import Count, Q
from django.db.models.functions import TruncDate
from django.http import HttpRequest, HttpResponse
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html

from accounts.models import Business, Membership
from billing.models import Subscription
from bookings.models import Booking


def admin_dashboard(request: HttpRequest) -> HttpResponse:
    now = timezone.now()
    today = timezone.localdate()
    start_30d = now - timedelta(days=30)
    end_7d = now + timedelta(days=7)

    orgs_total = Business.objects.count()
    new_orgs_30d = Business.objects.filter(created_at__gte=start_30d, created_at__lt=now).count()
    memberships_active = Membership.objects.filter(is_active=True).count()

    subs_active = Subscription.objects.filter(
        Q(active=True)
        | Q(status="trialing", trial_end__isnull=False, trial_end__gt=now)
    ).count()

    subs_trialing = Subscription.objects.filter(
        status="trialing", trial_end__isnull=False, trial_end__gt=now
    ).count()
    subs_past_due = Subscription.objects.filter(status="past_due").count()

    bookings_today = Booking.objects.filter(start__date=today).count()
    bookings_next_7d = Booking.objects.filter(start__gte=now, start__lt=end_7d).count()
    bookings_last_30d = Booking.objects.filter(start__gte=start_30d, start__lt=now).count()
    paid_last_30d = Booking.objects.filter(
        start__gte=start_30d, start__lt=now, payment_status="paid"
    ).count()
    pending_last_30d = Booking.objects.filter(
        start__gte=start_30d, start__lt=now, payment_status__in=["pending", "offline_due"]
    ).count()
    blocks_next_7d = Booking.objects.filter(
        start__gte=now, start__lt=end_7d, is_blocking=True
    ).count()

    recent = (
        Booking.objects.select_related("organization", "service", "assigned_user", "assigned_team")
        .order_by("-start")[:10]
    )

    recent_rows: list[list] = []
    for b in recent:
        booking_url = reverse("admin:bookings_booking_change", args=[b.pk])
        org_name = getattr(b.organization, "name", str(b.organization_id))
        org_url = reverse("admin:accounts_business_change", args=[b.organization_id])

        recent_rows.append(
            [
                format_html(
                    '<a class="text-primary-600 hover:underline" href="{}">{}</a>',
                    booking_url,
                    b.public_ref or b.pk,
                ),
                format_html(
                    '<a class="text-primary-600 hover:underline" href="{}">{}</a>',
                    org_url,
                    org_name,
                ),
                timezone.localtime(b.start).strftime("%Y-%m-%d %H:%M"),
                (b.client_name or "-")[:40],
                b.payment_status or "-",
            ]
        )

    context = {
        **admin.site.each_context(request),
        "title": "Dashboard",
        "kpis": [
            {"label": "Organizations", "value": orgs_total, "icon": "apartment"},
            {"label": "New orgs (30d)", "value": new_orgs_30d, "icon": "add_business"},
            {"label": "Active memberships", "value": memberships_active, "icon": "group"},
            {"label": "Active subscriptions", "value": subs_active, "icon": "credit_card"},
            {"label": "Trialing subs", "value": subs_trialing, "icon": "timer"},
            {"label": "Past due subs", "value": subs_past_due, "icon": "warning"},
            {"label": "Bookings today", "value": bookings_today, "icon": "event"},
            {"label": "Bookings next 7 days", "value": bookings_next_7d, "icon": "calendar_month"},
            {"label": "Bookings last 30 days", "value": bookings_last_30d, "icon": "query_stats"},
            {"label": "Paid bookings (30d)", "value": paid_last_30d, "icon": "paid"},
            {"label": "Pending/offline due (30d)", "value": pending_last_30d, "icon": "hourglass_empty"},
            {"label": "Blocks next 7 days", "value": blocks_next_7d, "icon": "block"},
        ],
        "recent_bookings_table": {
            "headers": ["Ref", "Organization", "Start", "Client", "Payment"],
            "rows": recent_rows,
        },
    }

    return TemplateResponse(request, "admin/dashboard.html", context)


def admin_analytics(request: HttpRequest) -> HttpResponse:
    now = timezone.now()
    start_30d = now - timedelta(days=30)

    per_day = (
        Booking.objects.filter(start__gte=start_30d, start__lt=now)
        .annotate(day=TruncDate("start"))
        .values("day")
        .annotate(count=Count("id"))
        .order_by("day")
    )

    labels = [str(row["day"]) for row in per_day]
    values = [row["count"] for row in per_day]

    line_data = json.dumps(
        {
            "labels": labels,
            "datasets": [{"label": "Bookings", "data": values, "tension": 0.2}],
        }
    )
    line_options = json.dumps(
        {
            "responsive": True,
            "maintainAspectRatio": False,
            "plugins": {"legend": {"display": False}},
            "scales": {"y": {"beginAtZero": True}},
        }
    )

    status_counts = (
        Booking.objects.filter(start__gte=start_30d, start__lt=now)
        .values("payment_status")
        .annotate(count=Count("id"))
        .order_by("payment_status")
    )

    status_labels = [row["payment_status"] or "(blank)" for row in status_counts]
    status_values = [row["count"] for row in status_counts]

    bar_data = json.dumps(
        {
            "labels": status_labels,
            "datasets": [{"label": "Payment status", "data": status_values}],
        }
    )
    bar_options = json.dumps(
        {
            "responsive": True,
            "maintainAspectRatio": False,
            "plugins": {"legend": {"display": False}},
            "scales": {"y": {"beginAtZero": True}},
        }
    )

    top_orgs = (
        Booking.objects.filter(start__gte=start_30d, start__lt=now)
        .values("organization__name", "organization_id")
        .annotate(count=Count("id"))
        .order_by("-count")[:10]
    )

    top_org_rows: list[list] = []
    for row in top_orgs:
        org_url = reverse("admin:accounts_business_change", args=[row["organization_id"]])
        top_org_rows.append(
            [
                format_html(
                    '<a class="text-primary-600 hover:underline" href="{}">{}</a>',
                    org_url,
                    row["organization__name"] or f"org_id={row['organization_id']}",
                ),
                row["count"],
            ]
        )

    top_services = (
        Booking.objects.filter(start__gte=start_30d, start__lt=now, service__isnull=False)
        .values("service__name", "service_id")
        .annotate(count=Count("id"))
        .order_by("-count")[:10]
    )
    top_service_rows: list[list] = []
    for row in top_services:
        svc_url = reverse("admin:bookings_service_change", args=[row["service_id"]])
        top_service_rows.append(
            [
                format_html(
                    '<a class="text-primary-600 hover:underline" href="{}">{}</a>',
                    svc_url,
                    row["service__name"] or f"service_id={row['service_id']}",
                ),
                row["count"],
            ]
        )

    top_resources = (
        Booking.objects.filter(start__gte=start_30d, start__lt=now, resource__isnull=False)
        .values("resource__name", "resource_id")
        .annotate(count=Count("id"))
        .order_by("-count")[:10]
    )
    top_resource_rows: list[list] = []
    for row in top_resources:
        res_url = reverse("admin:bookings_facilityresource_change", args=[row["resource_id"]])
        top_resource_rows.append(
            [
                format_html(
                    '<a class="text-primary-600 hover:underline" href="{}">{}</a>',
                    res_url,
                    row["resource__name"] or f"resource_id={row['resource_id']}",
                ),
                row["count"],
            ]
        )

    top_staff = (
        Booking.objects.filter(start__gte=start_30d, start__lt=now, assigned_user__isnull=False)
        .values("assigned_user__username", "assigned_user_id")
        .annotate(count=Count("id"))
        .order_by("-count")[:10]
    )
    top_staff_rows: list[list] = []
    for row in top_staff:
        user_url = reverse("admin:auth_user_change", args=[row["assigned_user_id"]])
        top_staff_rows.append(
            [
                format_html(
                    '<a class="text-primary-600 hover:underline" href="{}">{}</a>',
                    user_url,
                    row["assigned_user__username"] or f"user_id={row['assigned_user_id']}",
                ),
                row["count"],
            ]
        )

    context = {
        **admin.site.each_context(request),
        "title": "Analytics",
        "bookings_per_day": {"data": line_data, "options": line_options},
        "payment_status": {"data": bar_data, "options": bar_options},
        "top_orgs_table": {
            "headers": ["Organization", "Bookings (30d)"],
            "rows": top_org_rows,
        },
        "top_services_table": {
            "headers": ["Service", "Bookings (30d)"],
            "rows": top_service_rows,
        },
        "top_resources_table": {
            "headers": ["Resource", "Bookings (30d)"],
            "rows": top_resource_rows,
        },
        "top_staff_table": {
            "headers": ["Staff user", "Bookings (30d)"],
            "rows": top_staff_rows,
        },
    }

    return TemplateResponse(request, "admin/analytics.html", context)
