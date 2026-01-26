from __future__ import annotations

from django.urls import path

from .api_views import HealthView, HelloView, MeView
from .api_bookings import BookingDetailView, BookingsListView
from .api_orgs import OrgsListView
from .api_profile import ProfileAvatarUploadView, ProfileView
from .api_services import ServiceDetailView, ServicesListCreateView
from .api_resources import ResourceDetailView, ResourcesListCreateView
from .api_team import TeamInvitesView, TeamMemberDetailView, TeamMembersView
from .api_billing import (
    BillingCheckoutSessionView,
    BillingPlanHealthView,
    BillingPlansView,
    BillingPortalSessionView,
    BillingSummaryView,
)

urlpatterns = [
    path("health/", HealthView.as_view(), name="api_health"),
    path("hello/", HelloView.as_view(), name="api_hello"),
    path("me/", MeView.as_view(), name="api_me"),
    path("orgs/", OrgsListView.as_view(), name="api_orgs"),
    path("bookings/", BookingsListView.as_view(), name="api_bookings_list"),
    path("bookings/<int:booking_id>/", BookingDetailView.as_view(), name="api_booking_detail"),
    path("profile/", ProfileView.as_view(), name="api_profile"),
    path("profile/avatar/", ProfileAvatarUploadView.as_view(), name="api_profile_avatar"),
    path("services/", ServicesListCreateView.as_view(), name="api_services"),
    path("services/<int:service_id>/", ServiceDetailView.as_view(), name="api_service_detail"),

    path("resources/", ResourcesListCreateView.as_view(), name="api_resources"),
    path("resources/<int:resource_id>/", ResourceDetailView.as_view(), name="api_resource_detail"),

    path("team/members/", TeamMembersView.as_view(), name="api_team_members"),
    path("team/members/<int:member_id>/", TeamMemberDetailView.as_view(), name="api_team_member_detail"),
    path("team/invites/", TeamInvitesView.as_view(), name="api_team_invites"),

    path("billing/summary/", BillingSummaryView.as_view(), name="api_billing_summary"),
    path("billing/plans/", BillingPlansView.as_view(), name="api_billing_plans"),
    path("billing/portal/", BillingPortalSessionView.as_view(), name="api_billing_portal"),
    path("billing/checkout/", BillingCheckoutSessionView.as_view(), name="api_billing_checkout"),
    path("billing/plan-health/", BillingPlanHealthView.as_view(), name="api_billing_plan_health"),
]

# JWT endpoints (optional): only register if SimpleJWT is installed.
try:
    from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

    urlpatterns += [
        path("auth/token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
        path("auth/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    ]
except Exception:
    # SimpleJWT not installed; keep API functional without auth endpoints.
    pass
