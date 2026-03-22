from accounts.models import Business as Organization
from django.db import DatabaseError
from billing.models import Subscription

BASIC_SLUG = "basic"
PRO_SLUG = "pro"
TEAM_SLUG = "team"

REQUIRED_WEEKLY_AVAILABILITY_PLANS = {PRO_SLUG, TEAM_SLUG}
MULTIPLE_SERVICE_PLANS = {PRO_SLUG, TEAM_SLUG}
MULTI_STAFF_PLANS = {TEAM_SLUG}


def _is_stripe_managed_subscription(sub: Subscription | None) -> bool:
    return bool(sub and getattr(sub, "stripe_subscription_id", None))


def _has_active_non_trial_subscription(org: Organization) -> bool:
    """Return True when org has an active, non-trial subscription.

    This is the purchase eligibility gate for the booking-flow bundle add-on.
    It is intentionally plan-agnostic (Basic/Pro/Team all eligible), but trial
    users are excluded.
    """
    sub = get_subscription(org)
    if sub is None:
        return False

    if not getattr(sub, "plan", None):
        return False

    status = (getattr(sub, "status", "") or "").lower()
    if status in {"trialing", "canceled", "expired"}:
        return False

    stripe_managed = _is_stripe_managed_subscription(sub)

    if stripe_managed:
        try:
            return bool(sub.is_active())
        except Exception:
            return True

    # Manual/admin-assigned subscription (no Stripe subscription id).
    if getattr(sub, "active", None) is False:
        return False
    return True


def can_purchase_booking_flow_bundle(org: Organization) -> bool:
    """Whether org can purchase the booking-flow bundle add-on."""
    return _has_active_non_trial_subscription(org)


def has_booking_flow_bundle(org: Organization) -> bool:
    """Whether booking-flow bundle is enabled for this org.

    Bundle unlocks:
    - Hosted subdomain flow
    - Embed widget flow
    - Subdomain settings access
    """
    sub = get_subscription(org)
    # Admin manual override: toggling this flag on should immediately unlock
    # the bundle even without an active paid Stripe-managed subscription.
    if sub and bool(getattr(sub, "custom_domain_addon_enabled", False)):
        return True

    # Otherwise, no bundle access.
    return False


def can_use_embed_widget(org: Organization) -> bool:
    return has_booking_flow_bundle(org)


def can_use_custom_domain(org: Organization) -> bool:
    """Subdomain feature gate (legacy name kept for compatibility).

    In bundle mode, access requires:
    - active non-trial subscription (any plan), and
    - booking-flow bundle add-on enabled.
    """
    return can_use_hosted_subdomain(org)


def can_use_hosted_subdomain(org: Organization) -> bool:
    """CircleCal-hosted subdomain feature gate."""
    return has_booking_flow_bundle(org)


def can_use_offline_payment_methods(org: Organization) -> bool:
    """Allow offering offline payment instructions (cash/Venmo/Zelle) to clients.

    Requirement:
    - Trial/Basic: Stripe only
    - Pro/Team: Stripe + offline methods
    """
    sub = get_subscription(org)
    # Treat trialing like Basic for payment method gating.
    if sub and getattr(sub, "status", "") == "trialing":
        return False
    return get_plan_slug(org) in {PRO_SLUG, TEAM_SLUG}


def can_use_resources(org: Organization) -> bool:
    """Facility resource booking (rooms/cages) is a Team-only feature."""
    return get_plan_slug(org) == TEAM_SLUG


def get_subscription(org: Organization) -> Subscription | None:
    try:
        return org.subscription
    except (Subscription.DoesNotExist, DatabaseError):  # type: ignore[attr-defined]
        return None


def get_plan_slug(org: Organization) -> str:
    sub = get_subscription(org)
    if sub and sub.plan and sub.plan.slug:
        return sub.plan.slug.lower()
    # Fallback: treat as basic until upgraded
    return BASIC_SLUG


def can_edit_weekly_availability(org: Organization) -> bool:
    return get_plan_slug(org) in REQUIRED_WEEKLY_AVAILABILITY_PLANS


def can_add_service(org: Organization) -> bool:
    sub = get_subscription(org)
    # Trialing is treated like Basic for feature gates.
    if sub and getattr(sub, "status", "") == "trialing":
        return org.services.filter(is_active=True).count() < 1

    slug = get_plan_slug(org)
    if slug in MULTIPLE_SERVICE_PLANS:
        return True
    # Basic plan: only 1 active service allowed
    return org.services.filter(is_active=True).count() < 1


def can_add_staff(org: Organization) -> bool:
    return get_plan_slug(org) in MULTI_STAFF_PLANS


def enforce_service_limit(org: Organization) -> tuple[bool, str | None]:
    if can_add_service(org):
        return True, None
    return False, "Basic plan allows only 1 active service. Upgrade to Pro or Team to add more."


def enforce_weekly_availability(org: Organization) -> tuple[bool, str | None]:
    # Allow during trial regardless of plan to improve onboarding experience
    sub = get_subscription(org)
    if sub and sub.status == "trialing":
        return True, None
    if can_edit_weekly_availability(org):
        return True, None
    return False, "Weekly availability customization requires a Pro or Team subscription. Upgrade to unlock advanced scheduling."
