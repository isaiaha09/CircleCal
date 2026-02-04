from __future__ import annotations

from django.urls import path

from .api_views import HealthView, HelloView, MeView
from .api_bookings import BookingDetailView, BookingsAuditListView, BookingsListView
from .api_orgs import OrgsListView
from .api_profile import ProfileAvatarUploadView, ProfileView
from .api_profile import ProfileOverviewView
from .api_org_settings import OrgOfflinePaymentsView
from .api_push import PushStatusView, PushTokensView
from .api_services import ServiceDetailView, ServicesListCreateView
from .api_resources import ResourceDetailView, ResourcesListCreateView
from .api_team import TeamInviteDetailView, TeamInvitesView, TeamMemberDetailView, TeamMembersView
from .api_mobile_sso import MobileSSOLinkView
from .api_billing import (
    BillingCheckoutSessionView,
    BillingPlanHealthView,
    BillingPlansView,
    BillingPortalSessionView,
    BillingSummaryView,
    StripeExpressDashboardLinkView,
)

urlpatterns = [
    path("health/", HealthView.as_view(), name="api_health"),
    path("hello/", HelloView.as_view(), name="api_hello"),
    path("me/", MeView.as_view(), name="api_me"),
    path("orgs/", OrgsListView.as_view(), name="api_orgs"),
    path("bookings/", BookingsListView.as_view(), name="api_bookings_list"),
    path("bookings/audit/", BookingsAuditListView.as_view(), name="api_bookings_audit_list"),
    path("bookings/<int:booking_id>/", BookingDetailView.as_view(), name="api_booking_detail"),
    path("profile/", ProfileView.as_view(), name="api_profile"),
    path("profile/overview/", ProfileOverviewView.as_view(), name="api_profile_overview"),
    path("profile/avatar/", ProfileAvatarUploadView.as_view(), name="api_profile_avatar"),

    path("push/tokens/", PushTokensView.as_view(), name="api_push_tokens"),
    path("push/status/", PushStatusView.as_view(), name="api_push_status"),

    path("mobile/sso-link/", MobileSSOLinkView.as_view(), name="api_mobile_sso_link"),

    path("org/offline-payments/", OrgOfflinePaymentsView.as_view(), name="api_org_offline_payments"),
    path("services/", ServicesListCreateView.as_view(), name="api_services"),
    path("services/<int:service_id>/", ServiceDetailView.as_view(), name="api_service_detail"),

    path("resources/", ResourcesListCreateView.as_view(), name="api_resources"),
    path("resources/<int:resource_id>/", ResourceDetailView.as_view(), name="api_resource_detail"),

    path("team/members/", TeamMembersView.as_view(), name="api_team_members"),
    path("team/members/<int:member_id>/", TeamMemberDetailView.as_view(), name="api_team_member_detail"),
    path("team/invites/", TeamInvitesView.as_view(), name="api_team_invites"),
    path("team/invites/<int:invite_id>/", TeamInviteDetailView.as_view(), name="api_team_invite_detail"),

    path("billing/summary/", BillingSummaryView.as_view(), name="api_billing_summary"),
    path("billing/plans/", BillingPlansView.as_view(), name="api_billing_plans"),
    path("billing/portal/", BillingPortalSessionView.as_view(), name="api_billing_portal"),
    path("billing/checkout/", BillingCheckoutSessionView.as_view(), name="api_billing_checkout"),
    path("billing/plan-health/", BillingPlanHealthView.as_view(), name="api_billing_plan_health"),
    path(
        "billing/stripe/express-dashboard/",
        StripeExpressDashboardLinkView.as_view(),
        name="api_billing_stripe_express_dashboard",
    ),
]

# JWT endpoints (optional): only register if SimpleJWT is installed.
try:
    from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
    from .api_auth_mobile import MobileTokenView

    urlpatterns += [
        path("auth/token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
        path("auth/mobile/token/", MobileTokenView.as_view(), name="token_obtain_pair_mobile"),
        path("auth/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    ]
except Exception:
    # SimpleJWT not installed; keep API functional without auth endpoints.
    pass
