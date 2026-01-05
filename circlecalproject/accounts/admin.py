from django.contrib import admin
from .models import Business, Membership, Invite, BusinessSlugRedirect

@admin.register(Business)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "owner", "created_at")
    search_fields = ("name", "slug", "owner__username")
    ordering = ("-created_at",)


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "organization", "role", "is_active", "created_at")
    list_filter = ("role", "is_active")
    search_fields = ("user__username", "organization__name")

# Business is now the primary model; no separate proxy registration needed.

admin.site.register(Invite)


@admin.register(BusinessSlugRedirect)
class BusinessSlugRedirectAdmin(admin.ModelAdmin):
    list_display = ("old_slug", "business", "created_at")
    search_fields = ("old_slug", "business__name", "business__slug")
    ordering = ("-created_at",)
