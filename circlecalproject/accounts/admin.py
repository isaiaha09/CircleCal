from django.contrib import admin
from django import forms
from django.contrib.auth import get_user_model
from .models import Business, Membership, Invite

User = get_user_model()


def deactivate_users(modeladmin, request, queryset):
    queryset.update(is_active=False)
deactivate_users.short_description = "Deactivate selected users"


def reactivate_users(modeladmin, request, queryset):
    queryset.update(is_active=True)
reactivate_users.short_description = "Reactivate selected users"


def archive_businesses(modeladmin, request, queryset):
    queryset.update(is_archived=True)
archive_businesses.short_description = "Archive selected businesses"


class ReassignBusinessesForm(forms.Form):
    _selected_action = forms.CharField(widget=forms.MultipleHiddenInput)
    new_owner = forms.ModelChoiceField(queryset=User.objects.filter(is_active=True), required=True)


def reassign_businesses(modeladmin, request, queryset):
    form = None
    if 'apply' in request.POST:
        form = ReassignBusinessesForm(request.POST)
        if form.is_valid():
            new_owner = form.cleaned_data['new_owner']
            count = queryset.update(owner=new_owner)
            modeladmin.message_user(request, f"Reassigned {count} businesses to {new_owner}.")
            return
    if not form:
        form = ReassignBusinessesForm(initial={'_selected_action': request.POST.getlist(admin.ACTION_CHECKBOX_NAME)})
    return admin.helpers.render_action_form(request, context={'action': 'reassign_businesses', 'objects': queryset, 'form': form})
reassign_businesses.short_description = "Reassign selected businesses to another user"


@admin.register(Business)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "owner", "is_archived", "created_at")
    search_fields = ("name", "slug", "owner__username")
    ordering = ("-created_at",)
    list_filter = ("is_archived",)
    actions = [archive_businesses, reassign_businesses]


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "organization", "role", "is_active", "created_at")
    list_filter = ("role", "is_active")
    search_fields = ("user__username", "organization__name")


admin.site.register(Invite)
