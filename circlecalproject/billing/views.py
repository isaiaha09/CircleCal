import stripe
from django.conf import settings
from django.utils import timezone
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect, get_object_or_404, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from accounts.models import Business as Organization
from billing.models import Plan, Subscription, PaymentMethod
from calendar_app.utils import user_has_role

stripe.api_key = settings.STRIPE_SECRET_KEY


def _require_org_owner_or_admin(request, org):
    if not user_has_role(request.user, org, ["owner", "admin"]):
        return HttpResponseForbidden("Only owners/admins can manage billing.")
    return None


@require_http_methods(["GET"])
def create_checkout_session(request, org_slug, plan_id):
    """
    Redirects to Stripe Checkout for a subscription.
    URL: /billing/org/<org_slug>/checkout/<plan_id>/
    """
    org = get_object_or_404(Organization, slug=org_slug)
    err = _require_org_owner_or_admin(request, org)
    if err:
        return err

    plan = get_object_or_404(Plan, id=plan_id)
    if not plan.stripe_price_id:
        return HttpResponseBadRequest("Plan has no Stripe price id.")

    # Ensure Stripe customer exists
    if not org.stripe_customer_id:
        customer = stripe.Customer.create(
            email=request.user.email,
            metadata={"organization_id": str(org.id)}
        )
        org.stripe_customer_id = customer.id
        org.save()

    # Redirects: success → dashboard, cancel → pricing page
    from django.urls import reverse
    success_url = request.build_absolute_uri(
        reverse("calendar_app:dashboard", kwargs={"org_slug": org.slug})
    ) + "?checkout=success"
    cancel_url = request.build_absolute_uri(
        reverse("calendar_app:pricing_page", kwargs={"org_slug": org.slug})
    ) + "?checkout=cancel"

    session = stripe.checkout.Session.create(
        customer=org.stripe_customer_id,
        mode="subscription",
        line_items=[{"price": plan.stripe_price_id, "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={"organization_id": str(org.id), "plan_id": str(plan.id)},
    )

    return redirect(session.url)


@require_http_methods(["GET"])
def billing_portal(request, org_slug):
    """
    Sends user to Stripe Billing Portal to manage plan/cancel/update card.
    URL: /billing/org/<org_slug>/portal/
    """
    org = get_object_or_404(Organization, slug=org_slug)
    err = _require_org_owner_or_admin(request, org)
    if err:
        return err

    if not org.stripe_customer_id:
        return HttpResponseBadRequest("Organization has no Stripe customer yet.")

    portal_session = stripe.billing_portal.Session.create(
        customer=org.stripe_customer_id,
        return_url=f"{settings.SITE_URL}/bus/{org.slug}/calendar/"
    )

    return redirect(portal_session.url)


@csrf_exempt
@require_http_methods(["POST"])
def stripe_webhook(request):
    """
    Stripe webhook endpoint.
    Register in Stripe dashboard to:
      /billing/webhook/
    """
    payload = request.body
    sig_header = request.META.get("HTTP_STRIPE_SIGNATURE", "")
    secret = settings.STRIPE_WEBHOOK_SECRET

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, secret)
    except Exception:
        return HttpResponse(status=400)

    event_type = event["type"]
    data = event["data"]["object"]

    # 1) Checkout completed -> subscription created
    if event_type == "checkout.session.completed":
        org_id = data.get("metadata", {}).get("organization_id")
        plan_id = data.get("metadata", {}).get("plan_id")
        subscription_id = data.get("subscription")

        if org_id and plan_id:
            org = Organization.objects.get(id=org_id)
            plan = Plan.objects.get(id=plan_id)

            Subscription.objects.update_or_create(
                organization=org,
                defaults={
                    "plan": plan,
                    "stripe_subscription_id": subscription_id,
                    "active": True,
                }
            )

    # 2) Subscription updated/canceled
    if event_type in ("customer.subscription.updated", "customer.subscription.deleted"):
        subscription_id = data["id"]
        status = data["status"]  # active, canceled, past_due, etc.

        try:
            sub = Subscription.objects.get(stripe_subscription_id=subscription_id)
            sub.status = status
            sub.active = (status == "active" or status == "trialing")
            sub.cancel_at_period_end = data.get("cancel_at_period_end", False)
            sub.current_period_end = None
            if data.get("current_period_end"):
                # Stripe gives unix timestamp
                from datetime import datetime
                from django.utils import timezone as django_tz
                sub.current_period_end = django_tz.make_aware(datetime.fromtimestamp(data["current_period_end"]))
            # Capture trial_end timestamp if present (unix seconds) and in future
            trial_end_ts = data.get("trial_end")
            if trial_end_ts:
                from datetime import datetime
                from django.utils import timezone as django_tz
                trial_dt = django_tz.make_aware(datetime.fromtimestamp(trial_end_ts))
                sub.trial_end = trial_dt
            sub.save()
        except Subscription.DoesNotExist:
            pass

    # 5) PaymentMethod attached/detached -> update cache
    if event_type == 'payment_method.attached':
        pm = data
        cust_id = pm.get('customer')
        if cust_id:
            try:
                org = Organization.objects.filter(stripe_customer_id=cust_id).first()
                if org:
                    card = pm.get('card', {}) or pm.get('card', {})
                    PaymentMethod.objects.update_or_create(
                        organization=org,
                        stripe_pm_id=pm.get('id'),
                        defaults={
                            'brand': card.get('brand'),
                            'last4': card.get('last4'),
                            'exp_month': card.get('exp_month'),
                            'exp_year': card.get('exp_year'),
                        }
                    )
            except Exception:
                pass

    if event_type == 'payment_method.detached':
        pm = data
        pm_id = pm.get('id')
        try:
            PaymentMethod.objects.filter(stripe_pm_id=pm_id).delete()
        except Exception:
            pass

    # 6) Customer updated (e.g., invoice_settings.default_payment_method)
    if event_type == 'customer.updated':
        cust = data
        cust_id = cust.get('id')
        try:
            org = Organization.objects.filter(stripe_customer_id=cust_id).first()
            if org:
                default_pm = cust.get('invoice_settings', {}).get('default_payment_method')
                # Clear existing defaults
                PaymentMethod.objects.filter(organization=org).update(is_default=False)
                if default_pm:
                    PaymentMethod.objects.filter(stripe_pm_id=default_pm).update(is_default=True)
        except Exception:
            pass

    # 3) Invoice payment succeeded -> ensure active
    if event_type == "invoice.paid":
        subscription_id = data.get("subscription")
        if subscription_id:
            Subscription.objects.filter(
                stripe_subscription_id=subscription_id
            ).update(active=True, status="active")

    # 4) Invoice payment failed -> mark past_due
    if event_type == "invoice.payment_failed":
        subscription_id = data.get("subscription")
        if subscription_id:
            Subscription.objects.filter(
                stripe_subscription_id=subscription_id
            ).update(active=False, status="past_due")

    return HttpResponse(status=200)


@require_http_methods(["GET"])
def embedded_checkout_page(request, org_slug, plan_id):
    """Render embedded Payment Element page under base.html."""
    org = get_object_or_404(Organization, slug=org_slug)
    err = _require_org_owner_or_admin(request, org)
    if err:
        return err

    plan = get_object_or_404(Plan, id=plan_id)
    if not plan.stripe_price_id:
        return HttpResponseBadRequest("Plan has no Stripe price id.")

    publishable_key = settings.STRIPE_PUBLISHABLE_KEY
    return render(request, "calendar_app/embedded_checkout.html", {
        "organization": org,
        "plan": plan,
        "stripe_publishable_key": publishable_key,
    })


from django.views.decorators.csrf import csrf_exempt

@csrf_exempt
@require_http_methods(["POST"])
def create_embedded_subscription(request, org_slug, plan_id):
    """Create an incomplete subscription and return client_secret for Payment Element."""
    org = get_object_or_404(Organization, slug=org_slug)
    err = _require_org_owner_or_admin(request, org)
    if err:
        return err

    plan = get_object_or_404(Plan, id=plan_id)
    if not plan.stripe_price_id:
        return HttpResponseBadRequest("Plan has no Stripe price id.")

    # Ensure customer exists
    if not org.stripe_customer_id:
        customer = stripe.Customer.create(
            email=request.user.email,
            metadata={"organization_id": str(org.id)}
        )
        org.stripe_customer_id = customer.id
        org.save()

    # Create subscription in incomplete state; Stripe will require payment confirmation client-side
    try:
        sub = stripe.Subscription.create(
            customer=org.stripe_customer_id,
            items=[{"price": plan.stripe_price_id}],
            payment_behavior="default_incomplete",
            expand=["latest_invoice.payment_intent"],
            metadata={"organization_id": str(org.id), "plan_id": str(plan.id)},
        )
    except Exception as e:
        return HttpResponseBadRequest(str(e))

    pi = sub["latest_invoice"]["payment_intent"]
    client_secret = pi["client_secret"]

    return JsonResponse({
        "subscription_id": sub["id"],
        "client_secret": client_secret,
    })


# --- Custom Embedded Billing Management ---
@require_http_methods(["GET"])
def manage_billing(request, org_slug):
    org = get_object_or_404(Organization, slug=org_slug)
    err = _require_org_owner_or_admin(request, org)
    if err:
        return err

    subscription = getattr(org, "subscription", None)
    plans = Plan.objects.filter(is_active=True).order_by("price")

    publishable_key = settings.STRIPE_PUBLISHABLE_KEY

    payment_methods = []
    default_payment_method_id = None
    invoices = []
    upcoming_invoice = None
    trial_remaining_seconds = None
    trial_remaining_days = None
    trial_end_iso = None
    show_invoices = True
    show_upcoming_invoice = True

    now = timezone.now()

    # Determine trial countdown regardless of Stripe subscription presence
    if subscription and subscription.status == "trialing" and subscription.trial_end and subscription.trial_end > now:
        delta = subscription.trial_end - now
        trial_remaining_seconds = int(delta.total_seconds())
        trial_remaining_days = delta.days
        trial_end_iso = subscription.trial_end.isoformat()
        show_invoices = False
        show_upcoming_invoice = False

    # Prefer reading cached payment methods from DB to avoid extra Stripe calls
    try:
        cached = list(PaymentMethod.objects.filter(organization=org).order_by('-is_default', '-updated_at'))
        if cached:
            payment_methods = []
            for pm in cached:
                payment_methods.append({
                    'id': pm.stripe_pm_id,
                    'card': {
                        'last4': pm.last4,
                        'brand': pm.brand,
                        'exp_month': pm.exp_month,
                        'exp_year': pm.exp_year,
                    }
                })
            default = next((p for p in cached if p.is_default), None)
            default_payment_method_id = default.stripe_pm_id if default else None
        else:
            # Fallback to Stripe live API
            if org.stripe_customer_id:
                try:
                    pms = stripe.PaymentMethod.list(customer=org.stripe_customer_id, type="card")
                    payment_methods = pms.get("data", [])
                    cust = stripe.Customer.retrieve(org.stripe_customer_id)
                    default_payment_method_id = cust.get("invoice_settings", {}).get("default_payment_method")
                except Exception:
                    payment_methods = []
    except Exception:
        # If cache lookup fails for any reason, fall back to Stripe
        try:
            if org.stripe_customer_id:
                pms = stripe.PaymentMethod.list(customer=org.stripe_customer_id, type="card")
                payment_methods = pms.get("data", [])
                cust = stripe.Customer.retrieve(org.stripe_customer_id)
                default_payment_method_id = cust.get("invoice_settings", {}).get("default_payment_method")
        except Exception:
            payment_methods = []

        # Only pull invoices if a real Stripe subscription exists
        if subscription and subscription.stripe_subscription_id:
            try:
                if show_invoices:
                    invs = stripe.Invoice.list(customer=org.stripe_customer_id, limit=10)
                    raw_invoices = invs.get("data", [])
                    invoices = [
                        {
                            "created": i.get("created"),
                            "amount_due_dollars": (i.get("amount_due", 0) / 100.0),
                                           "status": i.get("status"),
                                           "hosted_invoice_url": i.get("hosted_invoice_url"),
                                           # Card info may be available via the invoice.payment_intent -> charges
                                           "card_brand": None,
                                           "card_last4": None,
                        }
                        for i in raw_invoices
                    ]
                    # Try to enrich invoices with card details where available
                    for idx, raw in enumerate(raw_invoices):
                        try:
                            card_brand = None
                            card_last4 = None
                            # Prefer payment_intent -> charges
                            pi = raw.get('payment_intent')
                            if pi:
                                try:
                                    pi_obj = stripe.PaymentIntent.retrieve(pi)
                                    charges = pi_obj.get('charges', {}).get('data', [])
                                    if charges:
                                        ch = charges[0]
                                        pm_card = ch.get('payment_method_details', {}).get('card', {})
                                        card_brand = pm_card.get('brand')
                                        card_last4 = pm_card.get('last4')
                                except Exception:
                                    # ignore retrieval issues
                                    pass
                            # Fallback: invoice.charge (older API) -> Charge.retrieve
                            if not card_last4:
                                charge_id = raw.get('charge')
                                if charge_id:
                                    try:
                                        ch = stripe.Charge.retrieve(charge_id)
                                        pm_card = ch.get('payment_method_details', {}).get('card', {})
                                        card_brand = pm_card.get('brand')
                                        card_last4 = pm_card.get('last4')
                                    except Exception:
                                        pass
                            if card_brand:
                                invoices[idx]['card_brand'] = card_brand
                            if card_last4:
                                invoices[idx]['card_last4'] = card_last4
                        except Exception:
                            # never fail invoice listing because of enrichment
                            continue
            except Exception:
                invoices = []
            try:
                if show_upcoming_invoice:
                    ui = stripe.Invoice.upcoming(customer=org.stripe_customer_id)
                    # Use period_end as the billing date, fallback to created
                    billing_timestamp = ui.get("period_end") or ui.get("created")
                    # Convert Unix timestamp to datetime
                    from datetime import datetime
                    billing_date = None
                    if billing_timestamp:
                        billing_date = timezone.make_aware(datetime.fromtimestamp(billing_timestamp))
                    upcoming_invoice = {
                        "billing_date": billing_date,
                        "amount_due_dollars": (ui.get("amount_due", 0) / 100.0),
                    }
            except Exception:
                upcoming_invoice = None

    return render(request, "billing/manage.html", {
        "org": org,
        "subscription": subscription,
        "plans": plans,
        # Expose a display_plan that falls back to a sensible default when
        # subscription.plan is missing (e.g., trial created without plan).
        "display_plan": (subscription.plan if subscription and subscription.plan else Plan.objects.filter(slug='basic').first()),
        "stripe_publishable_key": publishable_key,
        "payment_methods": payment_methods,
        "default_payment_method_id": default_payment_method_id,
        "has_payment_methods": bool(payment_methods),
        "invoices": invoices,
        "upcoming_invoice": upcoming_invoice,
        "trial_remaining_seconds": trial_remaining_seconds,
        "trial_remaining_days": trial_remaining_days,
        "trial_end_iso": trial_end_iso,
    })


@require_http_methods(["POST"])
def create_setup_intent(request, org_slug):
    org = get_object_or_404(Organization, slug=org_slug)
    err = _require_org_owner_or_admin(request, org)
    if err:
        return err
    if not org.stripe_customer_id:
        # Create customer if still missing
        customer = stripe.Customer.create(email=request.user.email, metadata={"organization_id": str(org.id)})
        org.stripe_customer_id = customer.id
        org.save()
    intent = stripe.SetupIntent.create(customer=org.stripe_customer_id, payment_method_types=["card"])
    return JsonResponse({"client_secret": intent.client_secret})


@require_http_methods(["POST"])
def set_default_payment_method(request, org_slug):
    org = get_object_or_404(Organization, slug=org_slug)
    err = _require_org_owner_or_admin(request, org)
    if err:
        return err
    import json
    body = json.loads(request.body or "{}")
    pm_id = body.get("payment_method_id")
    if not pm_id or not org.stripe_customer_id:
        return HttpResponseBadRequest("Missing payment method or customer.")
    try:
        stripe.Customer.modify(org.stripe_customer_id, invoice_settings={"default_payment_method": pm_id})
    except Exception as e:
        return HttpResponseBadRequest(str(e))
    # Update cache: mark pm as default and attempt to store card metadata
    try:
        # Attempt to retrieve payment method details from Stripe
        pm_obj = None
        try:
            pm_obj = stripe.PaymentMethod.retrieve(pm_id)
        except Exception:
            pm_obj = None

        card = {}
        if pm_obj:
            card = pm_obj.get('card', pm_obj.get('card', {})) or {}

        PaymentMethod.objects.filter(organization=org).update(is_default=False)
        defaults = {
            'brand': card.get('brand'),
            'last4': card.get('last4'),
            'exp_month': card.get('exp_month'),
            'exp_year': card.get('exp_year'),
            'is_default': True,
        }
        PaymentMethod.objects.update_or_create(organization=org, stripe_pm_id=pm_id, defaults=defaults)
    except Exception:
        # Don't block user action if cache update fails
        pass
    return JsonResponse({"status": "ok"})


@require_http_methods(["GET"])
def list_payment_methods(request, org_slug):
    """Return JSON list of card payment methods for the organization (used by client to refresh UI)."""
    org = get_object_or_404(Organization, slug=org_slug)
    err = _require_org_owner_or_admin(request, org)
    if err:
        return err

    if not org.stripe_customer_id:
        return JsonResponse({"data": []})

    try:
        pms = stripe.PaymentMethod.list(customer=org.stripe_customer_id, type="card")
        data = []
        for pm in pms.get('data', []):
            card = pm.card if hasattr(pm, 'card') else pm.get('card', {})
            data.append({
                'id': pm.id,
                'brand': (card.brand if card else pm.get('card', {}).get('brand')),
                'last4': (card.last4 if card else pm.get('card', {}).get('last4')),
                'exp_month': (card.exp_month if card else pm.get('card', {}).get('exp_month')),
                'exp_year': (card.exp_year if card else pm.get('card', {}).get('exp_year')),
            })
        return JsonResponse({"data": data})
    except Exception:
        return JsonResponse({"data": []})


@require_http_methods(["POST"])
def delete_payment_method(request, org_slug):
    org = get_object_or_404(Organization, slug=org_slug)
    err = _require_org_owner_or_admin(request, org)
    if err:
        return err
    import json
    import logging
    logger = logging.getLogger(__name__)
    
    body = json.loads(request.body or "{}")
    pm_id = body.get("payment_method_id")
    if not pm_id:
        return HttpResponseBadRequest("Missing payment method ID.")
    
    try:
        # Prevent deleting the invoice default payment method while an active
        # subscription exists. Require the user to add/set a different default first.
        cust = None
        default_pm = None
        try:
            if org.stripe_customer_id:
                cust = stripe.Customer.retrieve(org.stripe_customer_id)
                default_pm = cust.get('invoice_settings', {}).get('default_payment_method')
        except Exception:
            # If customer retrieval fails, log and continue to attempt detach below
            logger.exception('Failed to retrieve Stripe customer when checking default PM')

        sub = getattr(org, 'subscription', None)
        if default_pm and pm_id == default_pm and sub and getattr(sub, 'active', False):
            return HttpResponseBadRequest('This payment method is currently set as the default for invoices while you have an active subscription. Please add a new card and set it as the default before removing this one.')

        # Detach payment method from customer
        stripe.PaymentMethod.detach(pm_id)
        logger.info(f"Payment method {pm_id} detached from org {org_slug}")
        # Remove from cache if present
        try:
            PaymentMethod.objects.filter(stripe_pm_id=pm_id).delete()
        except Exception:
            logger.exception('Failed to remove cached payment method')
    except Exception as e:
        logger.error(f"Failed to detach payment method: {e}")
        return HttpResponseBadRequest(str(e))
    
    return JsonResponse({"status": "ok"})


@require_http_methods(["POST"])
def cancel_subscription(request, org_slug):
    org = get_object_or_404(Organization, slug=org_slug)
    err = _require_org_owner_or_admin(request, org)
    if err:
        return err
    import json
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        body = json.loads(request.body.decode('utf-8') or "{}")
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error in cancel_subscription: {e}")
        return HttpResponseBadRequest(f"Invalid JSON: {e}")
    
    immediate = body.get("immediate", False)
    sub = getattr(org, "subscription", None)
    if not sub or not sub.stripe_subscription_id:
        return HttpResponseBadRequest("No subscription.")
    
    try:
        if immediate:
            logger.info(f"Immediate cancel for org {org_slug}, subscription {sub.stripe_subscription_id}")
            stripe.Subscription.delete(sub.stripe_subscription_id)
            sub.status = "canceled"
            sub.active = False
        else:
            logger.info(f"Cancel at period end for org {org_slug}, subscription {sub.stripe_subscription_id}")
            stripe.Subscription.modify(sub.stripe_subscription_id, cancel_at_period_end=True)
            sub.cancel_at_period_end = True
        sub.save()
    except Exception as e:
        logger.error(f"Stripe error in cancel_subscription: {e}")
        return HttpResponseBadRequest(str(e))
    return JsonResponse({"status": "ok"})


@require_http_methods(["POST"])
def reactivate_subscription(request, org_slug):
    org = get_object_or_404(Organization, slug=org_slug)
    err = _require_org_owner_or_admin(request, org)
    if err:
        return err
    sub = getattr(org, "subscription", None)
    if not sub or not sub.stripe_subscription_id:
        return HttpResponseBadRequest("No subscription.")
    try:
        stripe.Subscription.modify(sub.stripe_subscription_id, cancel_at_period_end=False)
        sub.cancel_at_period_end = False
        sub.save()
    except Exception as e:
        return HttpResponseBadRequest(str(e))
    return JsonResponse({"status": "ok"})


@require_http_methods(["POST"])
def change_subscription_plan(request, org_slug, plan_id):
    org = get_object_or_404(Organization, slug=org_slug)
    err = _require_org_owner_or_admin(request, org)
    if err:
        return err
    new_plan = get_object_or_404(Plan, id=plan_id)
    if not new_plan.stripe_price_id:
        return HttpResponseBadRequest("Plan missing price id.")
    import json
    sub = getattr(org, "subscription", None)

    # Accept JSON body with optional `start_immediately` (bool).
    try:
        body = json.loads(request.body.decode('utf-8') or "{}")
    except Exception:
        body = {}
    start_immediately = bool(body.get("start_immediately", True))

    # Case 1: Trial without Stripe subscription - either schedule plan change
    # to take effect after trial, or create a Stripe subscription immediately.
    if sub and not sub.stripe_subscription_id:
        if not org.stripe_customer_id:
            return HttpResponseBadRequest("No Stripe customer. Add payment method first.")

        # If user chose to wait until trial ends, only update the local plan
        # pointer and don't create a Stripe subscription yet.
        if not start_immediately:
            sub.plan = new_plan
            sub.save()
            return JsonResponse({"status": "ok", "message": "Plan scheduled to begin after trial."})

        try:
            # Check if customer has payment methods
            payment_methods = stripe.PaymentMethod.list(customer=org.stripe_customer_id, type="card")
            if not payment_methods.data:
                return HttpResponseBadRequest("No payment method found. Please add a card first.")

            # Get the first payment method (most recently added)
            pm_id = payment_methods.data[0].id

            # Set as default payment method
            stripe.Customer.modify(
                org.stripe_customer_id,
                invoice_settings={"default_payment_method": pm_id}
            )

            # Create new Stripe subscription
            stripe_sub = stripe.Subscription.create(
                customer=org.stripe_customer_id,
                items=[{"price": new_plan.stripe_price_id}],
                default_payment_method=pm_id,
                metadata={"organization_id": str(org.id), "plan_id": str(plan_id)},
            )
            # Update local subscription
            sub.stripe_subscription_id = stripe_sub.id
            sub.plan = new_plan
            sub.status = stripe_sub.status
            sub.active = (stripe_sub.status == "active")
            if stripe_sub.get("current_period_end"):
                from datetime import datetime
                sub.current_period_end = timezone.make_aware(datetime.fromtimestamp(stripe_sub["current_period_end"]))
            sub.trial_end = None  # Clear trial when converting to paid
            sub.save()
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to create subscription: {e}")
            return HttpResponseBadRequest(str(e))
        return JsonResponse({"status": "ok"})

    # Case 2: Existing Stripe subscription - modify it
    if not sub or not sub.stripe_subscription_id:
        return HttpResponseBadRequest("No subscription.")

    try:
        # If user requested to wait and the subscription is currently trialing,
        # don't hit Stripe — just record the desired plan locally to take effect
        # after trial ends.
        if not start_immediately and getattr(sub, 'status', '') == 'trialing':
            sub.plan = new_plan
            sub.save()
            return JsonResponse({"status": "ok", "message": "Plan scheduled to begin after trial."})

        stripe.Subscription.modify(sub.stripe_subscription_id, items=[{"price": new_plan.stripe_price_id}])
        sub.plan = new_plan
        sub.save()
    except Exception as e:
        return HttpResponseBadRequest(str(e))
    return JsonResponse({"status": "ok"})
