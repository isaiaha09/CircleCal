from __future__ import annotations

from django.conf import settings
from django.urls import reverse

from accounts.models import Business, Membership
from billing.models import PaymentMethod, Plan

try:
    import stripe
except Exception as exc:  # pragma: no cover
    stripe = None  # type: ignore[assignment]

try:
    from rest_framework.exceptions import ValidationError
    from rest_framework.permissions import IsAuthenticated
    from rest_framework.response import Response
    from rest_framework.views import APIView
except Exception as exc:  # pragma: no cover
    raise RuntimeError(
        "Django REST Framework is required for API views. "
        "Install 'djangorestframework' and ensure 'rest_framework' is in INSTALLED_APPS.'"
    ) from exc


def _is_app_request(request) -> bool:
    """Detect requests originating from the native CircleCal mobile app."""

    try:
        ua = (request.META.get("HTTP_USER_AGENT") or "")
        return "circlecalapp" in ua.lower()
    except Exception:
        return False


def _deny_in_app_billing(request) -> None:
    """Prevent subscription purchase/management from inside the mobile app.

    CircleCal does not offer pricing or billing flows inside the mobile app.
    Use the web app in a browser for subscription management.
    """

    if _is_app_request(request):
        raise ValidationError({"detail": "Pricing and billing are not available in the mobile app."})


def _get_org_and_membership(*, user, org_param: str | None):
    if not org_param:
        raise ValidationError({"org": "This query param is required (org slug or id)."})

    org: Business | None
    if str(org_param).isdigit():
        org = Business.objects.filter(id=int(org_param)).first()
    else:
        org = Business.objects.filter(slug=str(org_param)).first()

    if not org:
        raise ValidationError({"org": "Unknown organization."})

    membership = Membership.objects.filter(user=user, organization=org, is_active=True).first()
    if not membership:
        raise ValidationError({"detail": "You do not have access to this organization."})

    return org, membership


def _require_billing_admin(membership: Membership):
    if membership.role not in {"owner"}:
        raise ValidationError({"detail": "Only owners can manage billing."})


def _stripe_enabled() -> bool:
    return bool(getattr(settings, "STRIPE_SECRET_KEY", None)) and stripe is not None


def _ensure_stripe_customer_id(*, org: Business, email: str | None):
    if org.stripe_customer_id:
        return org.stripe_customer_id
    if not _stripe_enabled():
        raise ValidationError({"detail": "Stripe is not configured on this server."})

    stripe.api_key = settings.STRIPE_SECRET_KEY
    customer = stripe.Customer.create(
        email=email or "",
        metadata={"organization_id": str(org.id), "org_slug": str(org.slug)},
    )
    org.stripe_customer_id = getattr(customer, "id", None) or customer.get("id")
    org.save(update_fields=["stripe_customer_id"])
    return org.stripe_customer_id


class BillingSummaryView(APIView):
    """Org-scoped billing summary for mobile."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        org, membership = _get_org_and_membership(user=request.user, org_param=request.query_params.get("org"))
        _require_billing_admin(membership)

        sub = None
        plan = None
        plan_slug = "basic"
        can_add_service = False
        can_add_staff = False
        can_edit_weekly_availability = False
        can_use_offline_payment_methods = False
        can_use_resources = False

        try:
            from billing.utils import (
                can_add_service as _can_add_service,
                can_add_staff as _can_add_staff,
                can_edit_weekly_availability as _can_edit_weekly_availability,
                can_use_offline_payment_methods as _can_use_offline_payment_methods,
                can_use_resources as _can_use_resources,
                get_plan_slug as _get_plan_slug,
                get_subscription as _get_subscription,
            )

            sub = _get_subscription(org)
            plan_slug = _get_plan_slug(org)
            can_add_service = bool(_can_add_service(org))
            can_add_staff = bool(_can_add_staff(org))
            can_edit_weekly_availability = bool(_can_edit_weekly_availability(org))
            can_use_offline_payment_methods = bool(_can_use_offline_payment_methods(org))
            can_use_resources = bool(_can_use_resources(org))
        except Exception:
            sub = None

        if sub is not None:
            try:
                plan = sub.plan
            except Exception:
                plan = None

        payment_methods = list(
            PaymentMethod.objects.filter(organization=org).order_by("-is_default", "-updated_at")
        )

        active_services_count = 0
        try:
            active_services_count = org.services.filter(is_active=True).count()
        except Exception:
            active_services_count = 0

        active_members_count = 0
        try:
            active_members_count = Membership.objects.filter(organization=org, is_active=True).count()
        except Exception:
            active_members_count = 0

        return Response(
            {
                "org": {"id": org.id, "slug": org.slug, "name": org.name},
                "plan": {
                    "slug": plan_slug,
                    "name": getattr(plan, "name", None),
                    "price": str(getattr(plan, "price", "0")),
                    "billing_period": getattr(plan, "billing_period", None),
                },
                "subscription": (
                    {
                        "status": getattr(sub, "status", None),
                        "cancel_at_period_end": bool(getattr(sub, "cancel_at_period_end", False)),
                        "current_period_end": getattr(sub, "current_period_end", None),
                        "trial_end": getattr(sub, "trial_end", None),
                        "scheduled_plan": (
                            {
                                "id": getattr(getattr(sub, "scheduled_plan", None), "id", None),
                                "slug": getattr(getattr(sub, "scheduled_plan", None), "slug", None),
                                "name": getattr(getattr(sub, "scheduled_plan", None), "name", None),
                            }
                            if getattr(sub, "scheduled_plan", None)
                            else None
                        ),
                        "scheduled_change_at": getattr(sub, "scheduled_change_at", None),
                    }
                    if sub is not None
                    else None
                ),
                "features": {
                    "can_add_service": can_add_service,
                    "can_add_staff": can_add_staff,
                    "can_edit_weekly_availability": can_edit_weekly_availability,
                    "can_use_offline_payment_methods": can_use_offline_payment_methods,
                    "can_use_resources": can_use_resources,
                },
                "usage": {
                    "active_services_count": active_services_count,
                    "active_members_count": active_members_count,
                },
                "stripe": {
                    "enabled": _stripe_enabled(),
                    "customer_id": bool(getattr(org, "stripe_customer_id", None)),
                    "connect_account_id": bool(getattr(org, "stripe_connect_account_id", None)),
                    "connect_details_submitted": bool(getattr(org, "stripe_connect_details_submitted", False)),
                    "connect_charges_enabled": bool(getattr(org, "stripe_connect_charges_enabled", False)),
                    "connect_payouts_enabled": bool(getattr(org, "stripe_connect_payouts_enabled", False)),
                },
                "payment_methods": [
                    {
                        "id": pm.id,
                        "brand": pm.brand,
                        "last4": pm.last4,
                        "exp_month": pm.exp_month,
                        "exp_year": pm.exp_year,
                        "is_default": bool(pm.is_default),
                    }
                    for pm in payment_methods
                ],
            }
        )


class BillingPlansView(APIView):
    """List active plans (used by mobile to show upgrade options)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        _deny_in_app_billing(request)
        org, membership = _get_org_and_membership(user=request.user, org_param=request.query_params.get("org"))
        _require_billing_admin(membership)

        plans = list(Plan.objects.filter(is_active=True).order_by("price", "name"))
        return Response(
            {
                "org": {"id": org.id, "slug": org.slug, "name": org.name},
                "plans": [
                    {
                        "id": p.id,
                        "name": p.name,
                        "slug": p.slug,
                        "price": str(p.price),
                        "billing_period": p.billing_period,
                        "description": p.description,
                    }
                    for p in plans
                ],
            }
        )


class BillingPortalSessionView(APIView):
    """Create a Stripe Billing Portal session URL for this org."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        _deny_in_app_billing(request)
        org, membership = _get_org_and_membership(user=request.user, org_param=request.query_params.get("org"))
        _require_billing_admin(membership)

        customer_id = _ensure_stripe_customer_id(org=org, email=getattr(request.user, "email", None))
        if not customer_id:
            raise ValidationError({"detail": "Unable to create Stripe customer."})

        if not _stripe_enabled():
            raise ValidationError({"detail": "Stripe is not configured on this server."})

        stripe.api_key = settings.STRIPE_SECRET_KEY
        return_url = request.build_absolute_uri(
            reverse("calendar_app:dashboard", kwargs={"org_slug": org.slug})
        )
        portal_session = stripe.billing_portal.Session.create(customer=customer_id, return_url=return_url)
        url = getattr(portal_session, "url", None) or portal_session.get("url")
        if not url:
            raise ValidationError({"detail": "Stripe did not return a portal session URL."})
        return Response({"url": url})


class BillingCheckoutSessionView(APIView):
    """Create a Stripe Checkout session URL to subscribe/upgrade to a plan."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        _deny_in_app_billing(request)
        org, membership = _get_org_and_membership(user=request.user, org_param=request.query_params.get("org"))
        _require_billing_admin(membership)

        if not _stripe_enabled():
            raise ValidationError({"detail": "Stripe is not configured on this server."})

        plan_id = None
        try:
            plan_id = int((request.data or {}).get("plan_id"))
        except Exception:
            raise ValidationError({"plan_id": "plan_id is required."})

        plan = Plan.objects.filter(id=plan_id).first()
        if not plan:
            raise ValidationError({"plan_id": "Unknown plan."})
        if not getattr(plan, "stripe_price_id", None):
            raise ValidationError({"detail": "Plan has no Stripe price id."})

        stripe.api_key = settings.STRIPE_SECRET_KEY
        customer_id = _ensure_stripe_customer_id(org=org, email=getattr(request.user, "email", None))

        # Mirror the web flow: success -> dashboard, cancel -> pricing.
        success_url = request.build_absolute_uri(
            reverse("calendar_app:dashboard", kwargs={"org_slug": org.slug})
        ) + "?checkout=success"
        cancel_url = request.build_absolute_uri(
            reverse("calendar_app:pricing_page", kwargs={"org_slug": org.slug})
        ) + "?checkout=cancel"

        session_kwargs = {
            "customer": customer_id,
            "mode": "subscription",
            "line_items": [{"price": str(plan.stripe_price_id), "quantity": 1}],
            "success_url": success_url,
            "cancel_url": cancel_url,
            "metadata": {"organization_id": str(org.id), "plan_id": str(plan.id)},
        }

        session = stripe.checkout.Session.create(**session_kwargs)
        url = getattr(session, "url", None) or session.get("url")
        if not url:
            raise ValidationError({"detail": "Stripe did not return a checkout URL."})
        return Response({"url": url})


class BillingPlanHealthView(APIView):
    """Lightweight health check so we can confirm default plans exist in production."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        # Requires org context mainly to keep the API consistent with the rest of mobile endpoints.
        _org, membership = _get_org_and_membership(user=request.user, org_param=request.query_params.get("org"))
        _require_billing_admin(membership)

        required = ["basic", "pro", "team"]
        plans = list(Plan.objects.filter(slug__in=required).order_by("slug"))
        by_slug = {p.slug.lower(): p for p in plans}

        missing_slugs = [s for s in required if s not in by_slug]
        missing_stripe_price_ids = [
            s for s in required if (s in by_slug and not bool(getattr(by_slug[s], "stripe_price_id", None)))
        ]

        return Response(
            {
                "required_slugs": required,
                "present_slugs": list(by_slug.keys()),
                "missing_slugs": missing_slugs,
                "missing_stripe_price_id_slugs": missing_stripe_price_ids,
                "plans": [
                    {
                        "id": p.id,
                        "slug": p.slug,
                        "name": p.name,
                        "is_active": bool(p.is_active),
                        "has_stripe_price_id": bool(p.stripe_price_id),
                    }
                    for p in plans
                ],
                "suggested_command": "python manage.py seed_plans --force",
            }
        )


class StripeExpressDashboardLinkView(APIView):
    """Create a one-time Stripe Express Dashboard login link for the connected account."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        _deny_in_app_billing(request)
        org, membership = _get_org_and_membership(user=request.user, org_param=request.query_params.get("org"))
        _require_billing_admin(membership)

        acct_id = getattr(org, "stripe_connect_account_id", None)
        if not acct_id:
            raise ValidationError({"detail": "No Stripe connected account found for this business."})
        if not _stripe_enabled():
            raise ValidationError({"detail": "Stripe is not configured on this server."})

        try:
            stripe.api_key = settings.STRIPE_SECRET_KEY
            link = stripe.Account.create_login_link(acct_id)
            url = getattr(link, "url", None) or link.get("url")
            if not url:
                raise ValueError("Stripe did not return a login link URL.")
            return Response({"url": url})
        except ValidationError:
            raise
        except Exception:
            raise ValidationError({"detail": "Could not open Stripe Express Dashboard. Please try again."})
