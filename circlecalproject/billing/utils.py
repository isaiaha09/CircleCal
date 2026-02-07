from accounts.models import Business as Organization
from billing.models import Subscription

BASIC_SLUG = "basic"
PRO_SLUG = "pro"
TEAM_SLUG = "team"

REQUIRED_WEEKLY_AVAILABILITY_PLANS = {PRO_SLUG, TEAM_SLUG}
MULTIPLE_SERVICE_PLANS = {PRO_SLUG, TEAM_SLUG}
MULTI_STAFF_PLANS = {TEAM_SLUG}


def _is_stripe_managed_subscription(sub: Subscription | None) -> bool:
    return bool(sub and getattr(sub, "stripe_subscription_id", None))


def _can_use_pro_team_features_not_trial(org: Organization) -> bool:
    """Feature gate for Embed Widget + Custom Domains.

    Business intent (per UI copy): requires an active Pro/Team plan (not trial).

    Practical nuance:
    - For Stripe-managed subscriptions, we enforce "not trial" and "active".
    - For manually-administered subscriptions (no Stripe subscription id), treat
      Pro/Team as eligible even if status wasn't perfectly updated in admin.
    """

    sub = get_subscription(org)
    slug = get_plan_slug(org)
    if slug not in {PRO_SLUG, TEAM_SLUG}:
        return False
    if sub is None:
        return False

    status = (getattr(sub, "status", "") or "").lower()
    stripe_managed = _is_stripe_managed_subscription(sub)

    if stripe_managed:
        # Stripe trials should not unlock these features.
        if status == "trialing":
            return False
        try:
            return bool(sub.is_active())
        except Exception:
            return True

    # Manual/admin-assigned plan (no Stripe subscription id).
    # Allow Pro/Team even if admin left status as trialing.
    if status in {"canceled", "expired"}:
        return False
    return True


def can_use_embed_widget(org: Organization) -> bool:
    return _can_use_pro_team_features_not_trial(org)


def can_use_custom_domain(org: Organization) -> bool:
    return _can_use_pro_team_features_not_trial(org)


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
    except Subscription.DoesNotExist:  # type: ignore[attr-defined]
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
