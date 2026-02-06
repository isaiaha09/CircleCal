from __future__ import annotations

from django.contrib import admin
from django.utils import timezone

try:
	from unfold.admin import ModelAdmin as UnfoldModelAdmin

	BaseAdmin = UnfoldModelAdmin
except Exception:  # pragma: no cover
	BaseAdmin = admin.ModelAdmin

from .models import Booking, FacilityResource, Service, ServiceResource


@admin.register(Booking)
class BookingAdmin(BaseAdmin):
	list_display = (
		"public_ref",
		"organization",
		"start",
		"end",
		"client_name",
		"service",
		"resource",
		"assigned_user",
		"assigned_team",
		"payment_status",
		"is_blocking",
	)
	list_select_related = (
		"organization",
		"service",
		"resource",
		"assigned_user",
		"assigned_team",
	)
	ordering = ("-start",)
	date_hierarchy = "start"
	search_fields = (
		"public_ref",
		"title",
		"client_name",
		"client_email",
		"organization__name",
		"organization__slug",
		"service__name",
		"resource__name",
		"assigned_user__username",
		"assigned_user__email",
		"assigned_team__name",
	)
	list_filter = (
		"organization",
		"is_blocking",
		"payment_status",
		"payment_method",
		"offline_payment_method",
		"service",
		"resource",
		"assigned_user",
		"assigned_team",
		("start", admin.DateFieldListFilter),
	)
	autocomplete_fields = (
		"organization",
		"service",
		"resource",
		"assigned_user",
		"assigned_team",
	)

	# Unfold niceties (safe defaults for normal Django admin too)
	list_filter_submit = True
	list_fullwidth = True

	readonly_fields = ("created_at",)
	fields = (
		"organization",
		"title",
		"start",
		"end",
		"client_name",
		"client_email",
		"is_blocking",
		"service",
		"resource",
		"assigned_user",
		"assigned_team",
		"public_ref",
		"payment_method",
		"offline_payment_method",
		"payment_status",
		"stripe_checkout_session_id",
		"rescheduled_from_booking_id",
		"created_at",
	)

	def get_queryset(self, request):
		qs = super().get_queryset(request)
		return qs


@admin.register(Service)
class ServiceAdmin(BaseAdmin):
	list_display = (
		"name",
		"organization",
		"duration",
		"price",
		"is_active",
		"show_on_public_calendar",
		"requires_facility_resources",
		"allow_stripe_payments",
	)
	list_select_related = ("organization",)
	ordering = ("organization", "name")
	search_fields = (
		"name",
		"slug",
		"organization__name",
		"organization__slug",
	)
	list_filter = (
		"organization",
		"is_active",
		"show_on_public_calendar",
		"requires_facility_resources",
		"allow_stripe_payments",
		"refunds_allowed",
		"use_fixed_increment",
	)
	autocomplete_fields = ("organization",)
	prepopulated_fields = {"slug": ("name",)}

	list_filter_submit = True
	list_fullwidth = True


@admin.register(FacilityResource)
class FacilityResourceAdmin(BaseAdmin):
	list_display = (
		"name",
		"organization",
		"slug",
		"is_active",
		"max_services",
	)
	list_select_related = ("organization",)
	ordering = ("organization", "name")
	search_fields = (
		"name",
		"slug",
		"organization__name",
		"organization__slug",
	)
	list_filter = (
		"organization",
		"is_active",
	)
	autocomplete_fields = ("organization",)
	prepopulated_fields = {"slug": ("name",)}

	list_filter_submit = True
	list_fullwidth = True


@admin.register(ServiceResource)
class ServiceResourceAdmin(BaseAdmin):
	list_display = (
		"service",
		"resource",
	)
	list_select_related = ("service", "resource")
	autocomplete_fields = ("service", "resource")
	search_fields = (
		"service__name",
		"resource__name",
		"service__organization__name",
	)

