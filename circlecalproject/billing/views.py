import stripe
# Canonical import for recent stripe versions: import error classes from top-level
from stripe import InvalidRequestError
from django.conf import settings
from django.utils import timezone
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden, JsonResponse, HttpResponseRedirect
from django.shortcuts import redirect, get_object_or_404, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.urls import reverse

from accounts.models import Business as Organization
from billing.models import Plan, Subscription, PaymentMethod
from calendar_app.utils import user_has_role
from billing.models import InvoiceMeta, InvoiceActionLog
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.utils import timezone as dj_timezone
from django.db.models import Q
import logging
import traceback
from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.core import signing
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

stripe.api_key = settings.STRIPE_SECRET_KEY


def _add_query_param(url: str, key: str, value: str, *, overwrite: bool = True) -> str:
    try:
        parts = urlsplit(url)
        qs = parse_qsl(parts.query, keep_blank_values=True)
        if overwrite:
            qs = [(k, v) for (k, v) in qs if k != key]
        qs.append((key, value))
        new_query = urlencode(qs)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, parts.fragment))
    except Exception:
        return url


def _make_app_flow_sig(org_id: int, purpose: str) -> str:
    return signing.dumps({'org': int(org_id), 'p': str(purpose)}, salt='cc_app_flow', compress=True)


def _check_app_flow_sig(sig: str, org_id: int, purpose: str, *, max_age_seconds: int = 24 * 60 * 60) -> bool:
    if not sig:
        return False
    try:
        payload = signing.loads(sig, salt='cc_app_flow', max_age=max_age_seconds)
        return int(payload.get('org')) == int(org_id) and str(payload.get('p')) == str(purpose)
    except Exception:
        return False


def _sync_connect_status(org):
    """Fetch Stripe connect account flags and persist them locally."""
    try:
        acct_id = getattr(org, 'stripe_connect_account_id', None)
        if not acct_id:
            return False
        acct = stripe.Account.retrieve(acct_id)
        org.stripe_connect_details_submitted = bool(acct.get('details_submitted'))
        org.stripe_connect_charges_enabled = bool(acct.get('charges_enabled'))
        org.stripe_connect_payouts_enabled = bool(acct.get('payouts_enabled'))
        org.save(update_fields=[
            'stripe_connect_details_submitted',
            'stripe_connect_charges_enabled',
            'stripe_connect_payouts_enabled',
        ])
        return True
    except Exception:
        return False


def _redirect_to_app(deep_link: str):
    """Redirect to the native app deep link (circlecal://...).

    Django's built-in redirect() / HttpResponseRedirect validates URL schemes and
    can raise DisallowedRedirect for non-http(s) protocols. For Stripe Connect
    completion we *must* redirect to the custom scheme so Expo's auth-session
    browser closes and the user returns to the app.
    """
    if not deep_link:
        return HttpResponseBadRequest('Missing deep link')

    # Prefer a proper redirect response but explicitly allow the circlecal scheme.
    try:
        base_schemes = list(getattr(HttpResponseRedirect, 'allowed_schemes', ['http', 'https']))
        if 'circlecal' not in base_schemes:
            base_schemes.append('circlecal')

        class _AppSchemeRedirect(HttpResponseRedirect):
            allowed_schemes = base_schemes

        return _AppSchemeRedirect(deep_link)
    except Exception:
        # Fallback: set Location header manually to avoid scheme validation.
        resp = HttpResponse('', status=302)
        resp['Location'] = deep_link
        return resp


@require_http_methods(["GET"])
def stripe_connect_start(request, org_slug):
    """Start/continue Stripe Connect Express onboarding for this business."""
    org = get_object_or_404(Organization, slug=org_slug)

    # Stripe Connect onboarding is not a subscription purchase/upgrade flow.
    # Keep subscription billing blocked in-app, but allow Connect onboarding.

    err = _require_org_owner_or_admin(request, org)
    if err:
        return err

    if not getattr(settings, 'STRIPE_SECRET_KEY', None):
        return render(request, 'billing/stripe_connect.html', {
            'org': org,
            'connect_error': 'Stripe is not configured on this server. Set STRIPE_SECRET_KEY and STRIPE_PUBLISHABLE_KEY.',
        }, status=200)

    # If already connected, send them back.
    if getattr(org, 'stripe_connect_account_id', None) and getattr(org, 'stripe_connect_charges_enabled', False):
        messages.success(request, 'Stripe is already connected.')
        return redirect('calendar_app:dashboard', org_slug=org.slug)

    try:
        acct_id = getattr(org, 'stripe_connect_account_id', None)
        if not acct_id:
            acct = stripe.Account.create(
                type='express',
                email=request.user.email,
                metadata={'organization_id': str(org.id), 'org_slug': str(org.slug)},
                capabilities={
                    'card_payments': {'requested': True},
                    'transfers': {'requested': True},
                },
            )
            org.stripe_connect_account_id = acct.id
            try:
                org.save(update_fields=['stripe_connect_account_id'])
            except Exception:
                org.save()
            acct_id = acct.id

        refresh_url = request.build_absolute_uri(
            reverse('billing:stripe_connect_refresh', kwargs={'org_slug': org.slug})
        )
        return_url = request.build_absolute_uri(
            reverse('billing:stripe_connect_return', kwargs={'org_slug': org.slug})
        )

        # Preserve app-mode across the external Stripe flow so the return handler
        # can deep-link back into the app (and close the in-app browser).
        try:
            cc_app_flow = False
            try:
                cc_app_flow = bool(request.session.get('cc_app_flow'))
            except Exception:
                cc_app_flow = False

            cc_app_cookie = str(request.COOKIES.get('cc_app') or '') == '1'
            if _is_app_ua(request) or str(request.GET.get('cc_app') or '') == '1' or cc_app_cookie or cc_app_flow:
                # Remember that this onboarding was launched from the mobile app.
                # This is a fallback for cases where cc_app=1 might not survive.
                try:
                    request.session['cc_app_stripe_connect'] = True
                except Exception:
                    pass

                # Add a signed token so the Stripe return/refresh handlers can
                # identify app-originated flows even when the OS auth-session browser
                # does not share CircleCal cookies (common on iOS/Android).
                try:
                    sig = _make_app_flow_sig(org.id, 'stripe_connect')
                    refresh_url = _add_query_param(refresh_url, 'cc_sig', sig, overwrite=True)
                    return_url = _add_query_param(return_url, 'cc_sig', sig, overwrite=True)
                except Exception:
                    pass

                refresh_url = _add_query_param(refresh_url, 'cc_app', '1', overwrite=False)
                return_url = _add_query_param(return_url, 'cc_app', '1', overwrite=False)
        except Exception:
            pass

        link = stripe.AccountLink.create(
            account=acct_id,
            refresh_url=refresh_url,
            return_url=return_url,
            type='account_onboarding',
        )
        return redirect(link.url)
    except Exception as e:
        # Render a friendly page with next steps instead of a raw 400.
        return render(request, 'billing/stripe_connect.html', {
            'org': org,
            'connect_error': str(e),
        }, status=200)


@require_http_methods(["GET"])
def stripe_connect_refresh(request, org_slug):
    """Stripe sends users here if they need to re-start onboarding."""
    org = get_object_or_404(Organization, slug=org_slug)

    sig = str(request.GET.get('cc_sig') or '')
    sig_ok = _check_app_flow_sig(sig, org.id, 'stripe_connect')
    is_app = str(request.GET.get('cc_app') or '') == '1' or sig_ok

    # If this flow was started from the mobile app (valid signature), always
    # deep-link back into the app to close the OS auth-session browser.
    # This must work whether or not cookies were shared (authenticated or not).
    if is_app and sig_ok:
        return _redirect_to_app('circlecal://stripe-return?status=refresh')

    if not getattr(request.user, 'is_authenticated', False):
        return redirect_to_login(request.get_full_path())

    return redirect('billing:stripe_connect_start', org_slug=org_slug)


@require_http_methods(["GET"])
def stripe_connect_return(request, org_slug):
    """Stripe returns here after onboarding; refresh status and send user back."""
    org = get_object_or_404(Organization, slug=org_slug)

    sig = str(request.GET.get('cc_sig') or '')
    sig_ok = _check_app_flow_sig(sig, org.id, 'stripe_connect')
    is_app = str(request.GET.get('cc_app') or '') == '1' or sig_ok

    # If we have a valid app-flow signature, immediately deep-link back into the app
    # to close the OS auth-session browser.
    # This must work even if the auth-session browser *does* share cookies.
    if is_app and sig_ok:
        ok = _sync_connect_status(org)
        if ok and getattr(org, 'stripe_connect_charges_enabled', False):
            return _redirect_to_app('circlecal://stripe-return?status=connected')
        return _redirect_to_app('circlecal://stripe-return?status=pending')

    if not getattr(request.user, 'is_authenticated', False):
        return redirect_to_login(request.get_full_path())

    # Stripe Connect onboarding return is not subscription billing.

    err = _require_org_owner_or_admin(request, org)
    if err:
        return err

    ok = _sync_connect_status(org)
    if ok and getattr(org, 'stripe_connect_charges_enabled', False):
        messages.success(request, 'Stripe is connected and ready for payments.')
        # Show a one-time informational modal on the dashboard after Connect completes.
        try:
            request.session['cc_stripe_connected_dashboard_modal'] = True
        except Exception:
            pass
        # If Connect onboarding was launched from the native app, return back to the app
        # (so the in-app browser can close and the user is back in the real app UI).
        try:
            launched_from_app = False
            try:
                launched_from_app = bool(request.session.get('cc_app_stripe_connect'))
            except Exception:
                launched_from_app = False

            try:
                launched_from_app = launched_from_app or bool(request.session.get('cc_app_flow'))
            except Exception:
                pass

            if is_app or launched_from_app:
                try:
                    request.session.pop('cc_app_stripe_connect', None)
                except Exception:
                    pass
                return _redirect_to_app('circlecal://stripe-return?status=connected')
        except Exception:
            pass

        return redirect('calendar_app:dashboard', org_slug=org.slug)

    messages.warning(request, 'Stripe connection started, but is not fully enabled yet. Please finish onboarding in Stripe.')
    return redirect('billing:stripe_connect_start', org_slug=org.slug)


@require_http_methods(["GET"])
def stripe_express_return_to_app(request):
    """Return URL for Stripe Express Dashboard.

    Stripe login links can specify a `redirect_url` that Stripe sends users to after they
    exit the Express dashboard. For the mobile app, we immediately deep-link back into
    the native app so the in-app browser closes.
    """

    # Always prefer deep-linking back into the app.
    try:
        return _redirect_to_app('circlecal://stripe-return?status=express_done')
    except Exception:
        # Safe fallback for environments that don't allow custom schemes.
        return redirect('accounts:profile')


@login_required
@require_http_methods(["GET"])
def stripe_express_dashboard(request, org_slug):
    """Redirect owner/admin into Stripe's Express Dashboard for this connected account.

    Uses Stripe's login link API so the user doesn't need to already be logged into Stripe.
    """
    org = get_object_or_404(Organization, slug=org_slug)

    # Stripe Express Dashboard is not subscription billing.

    err = _require_org_owner_or_admin(request, org)
    if err:
        return err

    acct_id = getattr(org, 'stripe_connect_account_id', None)
    if not acct_id:
        messages.warning(request, 'No Stripe connected account found for this business. Please connect Stripe first.')
        return redirect('accounts:profile')

    if not getattr(settings, 'STRIPE_SECRET_KEY', None):
        messages.error(request, 'Stripe is not configured on this server.')
        return redirect('accounts:profile')

    try:
        link = stripe.Account.create_login_link(acct_id)
        url = getattr(link, 'url', None) or link.get('url')
        if not url:
            raise ValueError('Stripe did not return a login link URL.')
        return redirect(url)
    except Exception:
        messages.error(request, 'Could not open Stripe Express Dashboard. Please try again.')
        return redirect('accounts:profile')


def stripe_invoice_upcoming(**kwargs):
    """Compatibility wrapper for Stripe's upcoming invoice preview.

    Older stripe-python versions exposed this as `Invoice.upcoming(...)`;
    newer generated clients (v14+) provide `Invoice.create_preview(...)`.
    This helper tries `upcoming` first, then falls back to `create_preview`.
    """
    inv_fn = getattr(stripe.Invoice, 'upcoming', None)
    if callable(inv_fn):
        return inv_fn(**kwargs)
    # Fallback for newer client naming
    inv_fn = getattr(stripe.Invoice, 'create_preview', None)
    if callable(inv_fn):
        # Newer stripe client expects some different parameter names
        # Translate `subscription_items` -> `subscription_details` which
        # the newer `create_preview` endpoint accepts.
        call_kwargs = dict(kwargs)

        # Newer API expects proration info under `subscription_details`.
        # Keep supporting callers that pass the classic `subscription_proration_date`.
        proration_date = call_kwargs.pop('subscription_proration_date', None)
        proration_behavior = call_kwargs.pop('subscription_proration_behavior', None)

        if 'subscription_items' in call_kwargs and 'subscription_details' not in call_kwargs:
            # Newer API expects a structured `subscription_details` object.
            # If caller passed a list (old `subscription_items`), nest it under
            # `items` to avoid creating `subscription_details[0]` style params.
            items = call_kwargs.pop('subscription_items')
            # If the caller already passed items as a dict-like mapping, keep it
            if isinstance(items, dict):
                call_kwargs['subscription_details'] = items
            else:
                call_kwargs['subscription_details'] = {'items': items}

        # Merge proration_date into subscription_details if provided
        if proration_date is not None or proration_behavior is not None:
            try:
                sd = call_kwargs.get('subscription_details')
                if not isinstance(sd, dict):
                    sd = {} if sd is None else dict(sd)
            except Exception:
                sd = {}
            if proration_date is not None:
                try:
                    sd['proration_date'] = int(proration_date)
                except Exception:
                    # If conversion fails, leave it out rather than breaking preview.
                    pass

            # Mirror Stripe's legacy `subscription_proration_behavior`.
            if proration_behavior is not None:
                try:
                    sd['proration_behavior'] = str(proration_behavior)
                except Exception:
                    pass
            call_kwargs['subscription_details'] = sd

        return inv_fn(**call_kwargs)
    raise AttributeError('stripe.Invoice has neither "upcoming" nor "create_preview"')


def _require_org_owner_or_admin(request, org):
    if not user_has_role(request.user, org, ["owner"]):
        return HttpResponseForbidden("Only owners can manage billing.")
    return None


def _is_app_ua(request) -> bool:
    try:
        ua = (request.META.get('HTTP_USER_AGENT') or '')
        return 'circlecalapp' in ua.lower()
    except Exception:
        return False


def _deny_in_app_billing(request):
    """CircleCal does not expose pricing/billing inside the native mobile app."""

    if _is_app_ua(request):
        return HttpResponseForbidden('Pricing and billing are not available in the mobile app. Please use CircleCal in a browser to manage your subscription.')
    return None


@require_http_methods(["GET"])
def create_checkout_session(request, org_slug, plan_id):
    """
    Redirects to Stripe Checkout for a subscription.
    URL: /billing/org/<org_slug>/checkout/<plan_id>/
    """
    org = get_object_or_404(Organization, slug=org_slug)

    deny = _deny_in_app_billing(request)
    if deny:
        return deny

    err = _require_org_owner_or_admin(request, org)
    if err:
        return err

    plan = get_object_or_404(Plan, id=plan_id)
    if not plan.stripe_price_id:
        return HttpResponseBadRequest("Plan has no Stripe price id.")

    # Ensure Stripe customer exists
    if not org.stripe_customer_id:
        customer = stripe.Customer.create(
            name=getattr(org, "name", None) or str(getattr(org, "slug", "")) or None,
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

    discounts = None
    try:
        from billing.models import DiscountCode

        dc = (
            DiscountCode.objects.filter(users=request.user, active=True)
            .order_by('-created_at')
            .first()
        )
        if dc and dc.is_valid():
            # Stripe Checkout supports applying either a promotion code or a coupon.
            promo_id = getattr(dc, 'stripe_promotion_code_id', None)
            coupon_id = getattr(dc, 'stripe_coupon_id', None)
            if promo_id:
                discounts = [{"promotion_code": str(promo_id)}]
            elif coupon_id:
                discounts = [{"coupon": str(coupon_id)}]
    except Exception:
        discounts = None

    session_kwargs = {
        'customer': org.stripe_customer_id,
        'mode': 'subscription',
        'line_items': [{"price": plan.stripe_price_id, "quantity": 1}],
        'success_url': success_url,
        'cancel_url': cancel_url,
        'metadata': {"organization_id": str(org.id), "plan_id": str(plan.id)},
    }
    if discounts:
        session_kwargs['discounts'] = discounts

    session = stripe.checkout.Session.create(**session_kwargs)

    return redirect(session.url)


@login_required
@require_POST
def invoice_hide(request, org_slug, invoice_id):
    """Hide (archive) an invoice in the UI. Safe, reversible."""
    org = get_object_or_404(Organization, slug=org_slug)
    err = _require_org_owner_or_admin(request, org)
    if err:
        return err

    # invoice_id here is expected to be a Stripe invoice id or subscription change id
    stripe_id = invoice_id
    # Try to find or create InvoiceMeta for this stripe invoice id
    meta, created = InvoiceMeta.objects.get_or_create(organization=org, stripe_invoice_id=stripe_id)
    meta.hidden = True
    meta.save()
    # Log action
    InvoiceActionLog.objects.create(invoice_meta=meta, user=request.user, action='hide')
    return JsonResponse({'success': True, 'hidden': True})


@login_required
@require_POST
def invoice_void(request, org_slug, invoice_id):
    """Void a Stripe invoice (when allowed) and mark it voided locally.

    This calls Stripe's `void_invoice` for open/draft invoices. For paid
    invoices, a credit note is generally required instead (we return an error).
    """
    org = get_object_or_404(Organization, slug=org_slug)
    err = _require_org_owner_or_admin(request, org)
    if err:
        return err

    stripe_id = invoice_id
    # Fetch invoice from Stripe to ensure it belongs to this customer and can be voided
    try:
        si = stripe.Invoice.retrieve(stripe_id)
    except Exception as e:
        return JsonResponse({'error': 'Stripe invoice not found or inaccessible', 'details': str(e)}, status=400)

    # Verify invoice belongs to this org's customer when possible
    cust = si.get('customer')
    if org.stripe_customer_id and cust and cust != org.stripe_customer_id:
        return JsonResponse({'error': 'Invoice does not belong to this organization.'}, status=403)

    status = si.get('status')
    # Only allow voiding for draft/open invoices
    if status not in ('draft', 'open'):
        return JsonResponse({'error': f'Invoice status "{status}" cannot be voided via this endpoint. Consider creating a credit note.'}, status=400)

    try:
        stripe.Invoice.void_invoice(stripe_id)
    except Exception as e:
        return JsonResponse({'error': 'Stripe void failed', 'details': str(e)}, status=400)

    # Mark local metadata
    meta, created = InvoiceMeta.objects.get_or_create(organization=org, stripe_invoice_id=stripe_id)
    meta.voided = True
    meta.voided_at = dj_timezone.now()
    meta.voided_by = request.user
    meta.save()
    InvoiceActionLog.objects.create(invoice_meta=meta, user=request.user, action='void')
    return JsonResponse({'success': True, 'voided': True})


@login_required
@require_POST
def invoice_unhide(request, org_slug, invoice_id):
    """Unhide (un-archive) an invoice in the UI."""
    org = get_object_or_404(Organization, slug=org_slug)
    err = _require_org_owner_or_admin(request, org)
    if err:
        return err

    stripe_id = invoice_id
    try:
        # Match either an InvoiceMeta with the stripe_invoice_id, or a SubscriptionChange
        # whose stripe_invoice_id matches (avoid treating the stripe id as a FK id).
        meta = InvoiceMeta.objects.filter(organization=org).filter(
            Q(stripe_invoice_id=stripe_id) | Q(subscription_change__stripe_invoice_id=stripe_id)
        ).first()
        if not meta:
            # If no meta exists yet for this invoice, create one (idempotent unhide)
            try:
                meta = InvoiceMeta.objects.create(organization=org, stripe_invoice_id=stripe_id, hidden=False)
            except Exception:
                return JsonResponse({'error': 'Invoice meta not found and could not be created'}, status=404)
        else:
            meta.hidden = False
            meta.save()
        try:
            InvoiceActionLog.objects.create(invoice_meta=meta, user=request.user, action='unhide')
        except Exception:
            # Log action failure is non-fatal for the client
            pass
        return JsonResponse({'success': True, 'hidden': False})
    except Exception as e:
        # Log full traceback to server console and include trace in JSON when DEBUG
        logging.exception('invoice_unhide failed')
        tb = traceback.format_exc()
        # If in DEBUG mode it's helpful to return the trace to the client for quick iteration
        try:
            if settings.DEBUG:
                return JsonResponse({'error': str(e), 'trace': tb}, status=500)
        except Exception:
            pass
        return JsonResponse({'error': str(e)}, status=500)


@require_http_methods(["GET"])
def billing_portal(request, org_slug):
    """
    Sends user to Stripe Billing Portal to manage plan/cancel/update card.
    URL: /billing/org/<org_slug>/portal/
    """
    org = get_object_or_404(Organization, slug=org_slug)

    deny = _deny_in_app_billing(request)
    if deny:
        return deny

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

    # Stripe Connect: keep connect flags in sync even if the user doesn't return
    # cleanly through our `stripe_connect_return` endpoint.
    if event_type == "account.updated":
        try:
            acct_id = data.get("id")
            if acct_id:
                org = Organization.objects.filter(stripe_connect_account_id=acct_id).first()
                if org:
                    org.stripe_connect_details_submitted = bool(data.get("details_submitted"))
                    org.stripe_connect_charges_enabled = bool(data.get("charges_enabled"))
                    org.stripe_connect_payouts_enabled = bool(data.get("payouts_enabled"))
                    org.save(update_fields=[
                        "stripe_connect_details_submitted",
                        "stripe_connect_charges_enabled",
                        "stripe_connect_payouts_enabled",
                    ])
        except Exception:
            # Webhooks should never crash the endpoint.
            pass

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

    # 1b) Embedded Payment Element flow -> subscription created
    # The Payment Element path creates subscriptions directly and does NOT emit
    # `checkout.session.completed`, so we must listen for subscription.created.
    if event_type == "customer.subscription.created":
        subscription_id = data.get("id")
        meta = data.get("metadata", {}) or {}
        org_id = meta.get("organization_id")
        plan_id = meta.get("plan_id")
        customer_id = data.get("customer")

        org = None
        plan = None
        try:
            if org_id:
                org = Organization.objects.filter(id=org_id).first()
            if not org and customer_id:
                org = Organization.objects.filter(stripe_customer_id=customer_id).first()
        except Exception:
            org = None

        try:
            if plan_id:
                plan = Plan.objects.filter(id=plan_id).first()
        except Exception:
            plan = None

        # Best-effort price -> plan mapping if metadata isn't present.
        if org and not plan:
            try:
                items = (data.get("items") or {}).get("data") or []
                price_id = None
                if items and isinstance(items[0], dict):
                    price = items[0].get("price") or {}
                    if isinstance(price, dict):
                        price_id = price.get("id")
                if price_id:
                    plan = Plan.objects.filter(stripe_price_id=price_id).first()
            except Exception:
                plan = None

        if org and subscription_id:
            status = data.get("status") or "active"
            cancel_at_period_end = bool(data.get("cancel_at_period_end", False))

            current_period_end = None
            try:
                cpe = data.get("current_period_end")
                if cpe:
                    from datetime import datetime
                    from django.utils import timezone as django_tz
                    current_period_end = django_tz.make_aware(datetime.fromtimestamp(int(cpe)))
            except Exception:
                current_period_end = None

            trial_end = None
            try:
                te = data.get("trial_end")
                if te:
                    from datetime import datetime
                    from django.utils import timezone as django_tz
                    trial_end = django_tz.make_aware(datetime.fromtimestamp(int(te)))
            except Exception:
                trial_end = None

            defaults = {
                "stripe_subscription_id": subscription_id,
                "active": (status in ("active", "trialing")),
                "status": status,
                "cancel_at_period_end": cancel_at_period_end,
                "current_period_end": current_period_end,
                "trial_end": trial_end,
            }
            if plan:
                defaults["plan"] = plan

            Subscription.objects.update_or_create(organization=org, defaults=defaults)

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

            # If subscription is active/trialing and not canceling, clear any scheduled deletion.
            try:
                if sub.active and not sub.cancel_at_period_end:
                    from accounts.models import Profile
                    org = getattr(sub, 'organization', None)
                    owner = getattr(org, 'owner', None) if org else None
                    if owner:
                        Profile.objects.filter(user=owner, scheduled_account_deletion_reason='trial_cancel_at_period_end').update(
                            scheduled_account_deletion_at=None,
                            scheduled_account_deletion_reason=None,
                        )
            except Exception:
                pass
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

    deny = _deny_in_app_billing(request)
    if deny:
        return deny

    err = _require_org_owner_or_admin(request, org)
    if err:
        return err

    plan = get_object_or_404(Plan, id=plan_id)
    if not plan.stripe_price_id:
        return HttpResponseBadRequest("Plan has no Stripe price id.")

    publishable_key = settings.STRIPE_PUBLISHABLE_KEY
    subscription = getattr(org, "subscription", None)
    is_change_flow = bool(subscription and getattr(subscription, "stripe_subscription_id", None))

    # Surface any admin-assigned DiscountCode to the user so it's clear a discount
    # is expected before they confirm payment.
    discount_display = None
    discount_code = None
    try:
        from billing.models import DiscountCode
        dc = (
            DiscountCode.objects.filter(users=request.user, active=True)
            .order_by('-created_at')
            .first()
        )
        if dc and dc.is_valid() and (getattr(dc, 'stripe_coupon_id', None) or getattr(dc, 'stripe_promotion_code_id', None)):
            discount_code = dc.code
            if dc.percent_off is not None:
                try:
                    discount_display = f"{float(dc.percent_off):g}% off"
                except Exception:
                    discount_display = "Discount applied"
            elif dc.amount_off_cents is not None:
                try:
                    amt = float(dc.amount_off_cents) / 100.0
                    cur = (dc.currency or 'USD').upper()
                    symbol = '$' if cur == 'USD' else ''
                    discount_display = f"{symbol}{amt:,.2f} off"
                except Exception:
                    discount_display = "Discount applied"
            else:
                discount_display = "Discount applied"
    except Exception:
        discount_display = None
        discount_code = None

    return render(request, "calendar_app/embedded_checkout.html", {
        "organization": org,
        "plan": plan,
        "subscription": subscription,
        "is_change_flow": is_change_flow,
        "stripe_publishable_key": publishable_key,
        "discount_display": discount_display,
        "discount_code": discount_code,
    })


from django.views.decorators.csrf import csrf_exempt

@csrf_exempt
@require_http_methods(["POST"])
def create_embedded_subscription(request, org_slug, plan_id):
    """Create an incomplete subscription and return client_secret for Payment Element."""
    org = get_object_or_404(Organization, slug=org_slug)

    deny = _deny_in_app_billing(request)
    if deny:
        return deny

    err = _require_org_owner_or_admin(request, org)
    if err:
        return err

    plan = get_object_or_404(Plan, id=plan_id)
    if not plan.stripe_price_id:
        return JsonResponse({"error": "Plan has no Stripe price id."}, status=400)

    # Ensure customer exists
    if not org.stripe_customer_id:
        customer = stripe.Customer.create(
            name=getattr(org, "name", None) or str(getattr(org, "slug", "")) or None,
            email=request.user.email,
            metadata={"organization_id": str(org.id)}
        )
        org.stripe_customer_id = customer.id
        org.save()

    # If the user has an active admin-assigned DiscountCode (stored in DB),
    # apply it to the Stripe subscription at creation time.
    discounts = None
    _coupon_id = None
    try:
        from billing.models import DiscountCode

        dc = (
            DiscountCode.objects.filter(users=request.user, active=True)
            .order_by('-created_at')
            .first()
        )
        if dc and dc.is_valid():
            promo_id = getattr(dc, 'stripe_promotion_code_id', None)
            _coupon_id = getattr(dc, 'stripe_coupon_id', None)
            if promo_id:
                discounts = [{"promotion_code": str(promo_id)}]
            elif _coupon_id:
                discounts = [{"coupon": str(_coupon_id)}]
    except Exception:
        discounts = None
        _coupon_id = None

    # Create subscription in incomplete state; Stripe will require payment confirmation client-side.
    # Stripe API change (2025-03): Invoice.payment_intent removed; use Invoice.payments[*].payment.payment_intent.
    try:
        sub_create_kwargs = {
            'customer': org.stripe_customer_id,
            'items': [{"price": plan.stripe_price_id}],
            'payment_behavior': "default_incomplete",
            # Ensure the card used for the first payment is saved and becomes the
            # subscription's default payment method. We'll also sync it into our
            # local PaymentMethod cache after confirmation.
            'payment_settings': {"save_default_payment_method": "on_subscription"},
            'expand': ["latest_invoice.payments"],
            'metadata': {"organization_id": str(org.id), "plan_id": str(plan.id)},
        }
        if discounts:
            sub_create_kwargs['discounts'] = discounts

        try:
            sub = stripe.Subscription.create(**sub_create_kwargs)
        except Exception as e:
            # Back-compat: some Stripe API versions/libs historically used `coupon`
            # instead of `discounts=[{coupon: ...}]`.
            msg = str(e).lower()
            if discounts and ('unknown parameter' in msg or 'received unknown parameter' in msg) and 'discounts' in msg:
                sub_create_kwargs.pop('discounts', None)
                if _coupon_id:
                    sub_create_kwargs['coupon'] = str(_coupon_id)
                sub = stripe.Subscription.create(**sub_create_kwargs)
            else:
                raise

        latest_invoice = sub.get("latest_invoice")
        if not latest_invoice:
            return JsonResponse({"error": "Stripe did not return latest_invoice for the subscription."}, status=400)

        payments = latest_invoice.get("payments") if isinstance(latest_invoice, dict) else getattr(latest_invoice, "payments", None)
        if not payments:
            # In case payments weren't expanded for some reason, try retrieving invoice with payments expanded.
            invoice_id = latest_invoice.get("id") if isinstance(latest_invoice, dict) else getattr(latest_invoice, "id", None)
            if not invoice_id:
                return JsonResponse({"error": "Unable to locate invoice id to retrieve payments."}, status=400)
            latest_invoice = stripe.Invoice.retrieve(invoice_id, expand=["payments"])
            payments = latest_invoice.get("payments")

        payment_rows = payments.get("data", []) if isinstance(payments, dict) else []
        if not payment_rows:
            return JsonResponse({"error": "Stripe invoice has no pending payments to confirm."}, status=400)

        payment = payment_rows[0].get("payment", {}) if isinstance(payment_rows[0], dict) else {}
        payment_intent = payment.get("payment_intent") if isinstance(payment, dict) else None

        # payment_intent may be an ID string or an expanded object
        if isinstance(payment_intent, dict):
            client_secret = payment_intent.get("client_secret")
            pi_id = payment_intent.get("id")
        else:
            pi_id = payment_intent
            client_secret = None

        if not client_secret:
            if not pi_id:
                return JsonResponse({"error": "Unable to locate Stripe PaymentIntent for invoice payment."}, status=400)
            pi = stripe.PaymentIntent.retrieve(pi_id)
            client_secret = pi.get("client_secret")

        if not client_secret:
            return JsonResponse({"error": "Stripe PaymentIntent is missing client_secret."}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)

    return JsonResponse({
        "subscription_id": sub["id"],
        "client_secret": client_secret,
    })


@require_http_methods(["GET"])
def preview_embedded_initial_invoice(request, org_slug, subscription_id):
    """Preview the first invoice for a newly created embedded subscription.

    The embedded Payment Element flow creates a Stripe subscription in
    `default_incomplete` and returns a PaymentIntent client secret.
    This endpoint fetches the subscription's latest invoice (expanded with
    line items) so the UI can show a confirmation modal with the actual
    amount due (taxes/discounts included) before confirming payment.
    """
    org = get_object_or_404(Organization, slug=org_slug)
    err = _require_org_owner_or_admin(request, org)
    if err:
        return err

    if not org.stripe_customer_id:
        return JsonResponse({"error": "Organization has no Stripe customer."}, status=400)

    try:
        sub = stripe.Subscription.retrieve(subscription_id)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)

    # Ensure this subscription belongs to the org's Stripe customer
    if str(sub.get('customer')) != str(org.stripe_customer_id):
        return JsonResponse({"error": "Subscription does not belong to this organization."}, status=403)

    invoice_id = sub.get('latest_invoice')
    if not invoice_id:
        return JsonResponse({"error": "Subscription has no latest invoice."}, status=400)

    try:
        inv = stripe.Invoice.retrieve(str(invoice_id), expand=['lines'])
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)

    amount_due_cents = int(inv.get('amount_due') or 0)
    currency = (inv.get('currency') or 'usd')
    period_end_ts = inv.get('period_end') or inv.get('created')
    billing_date = None
    try:
        if period_end_ts:
            from datetime import datetime
            billing_date = timezone.make_aware(datetime.fromtimestamp(int(period_end_ts)))
    except Exception:
        billing_date = None

    lines_out = []
    def _normalize_day_first_date_in_text(s: str) -> str:
        try:
            import re
            if not s:
                return s
            # Stripe sometimes includes day-first dates in descriptions like "after 29 Dec 2025".
            # Normalize to "Dec 29, 2025".
            month_map = {
                'jan': 'Jan', 'feb': 'Feb', 'mar': 'Mar', 'apr': 'Apr', 'may': 'May', 'jun': 'Jun',
                'jul': 'Jul', 'aug': 'Aug', 'sep': 'Sep', 'sept': 'Sep', 'oct': 'Oct', 'nov': 'Nov', 'dec': 'Dec',
            }
            pat = re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3,4})\s+(\d{4})\b")

            def repl(m):
                day = str(int(m.group(1)))
                mon_raw = (m.group(2) or '').strip().lower()
                mon = month_map.get(mon_raw)
                if not mon:
                    return m.group(0)
                year = m.group(3)
                return f"{mon} {day}, {year}"

            return pat.sub(repl, s)
        except Exception:
            return s
    try:
        for ln in (inv.get('lines') or {}).get('data', []):
            try:
                desc = ln.get('description') or ln.get('price', {}).get('nickname') or ln.get('id')
            except Exception:
                desc = ln.get('id')
            desc = _normalize_day_first_date_in_text(desc)
            amt = int(ln.get('amount') or 0)
            lines_out.append({
                'description': desc,
                'amount': amt / 100.0,
                'quantity': ln.get('quantity'),
                # Not a proration preview; keep shape consistent with other modals.
                'proration': False,
                'billing_reason': ln.get('billing_reason'),
            })
    except Exception:
        lines_out = []

    return JsonResponse({
        'amount_due_dollars': amount_due_cents / 100.0,
        'proration_amount_dollars': amount_due_cents / 100.0,
        'currency': currency,
        'billing_date_iso': billing_date.isoformat() if billing_date else None,
        'billing_date': (billing_date.strftime('%b %d, %Y').replace(' 0', ' ') if billing_date else None),
        # Use `proration_lines` key because embedded_checkout's modal expects it.
        'proration_lines': lines_out,
        'lines': lines_out,
        'raw': {
            'id': inv.get('id'),
            'amount_due': inv.get('amount_due'),
            'currency': inv.get('currency'),
            'status': inv.get('status'),
        },
    }, safe=False)


@csrf_exempt
@require_http_methods(["POST"])
def sync_subscription_from_stripe(request, org_slug, subscription_id):
    """Sync Stripe subscription data into the local Subscription model.

    This is used after embedded Payment Element confirmation where webhooks may
    not be running (especially in local dev). It is safe-guarded by requiring
    the current user to be an org owner/admin and the Stripe subscription to
    belong to the org's Stripe customer.
    """
    org = get_object_or_404(Organization, slug=org_slug)
    err = _require_org_owner_or_admin(request, org)
    if err:
        return err

    if not org.stripe_customer_id:
        return JsonResponse({"error": "Organization has no Stripe customer."}, status=400)

    try:
        sub = stripe.Subscription.retrieve(subscription_id)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)

    # Ensure this subscription belongs to the org's customer
    if str(sub.get("customer")) != str(org.stripe_customer_id):
        return JsonResponse({"error": "Subscription does not belong to this organization."}, status=403)

    status = sub.get("status") or "active"
    cancel_at_period_end = bool(sub.get("cancel_at_period_end", False))

    current_period_end = None
    try:
        cpe = sub.get("current_period_end")
        if cpe:
            from datetime import datetime
            from django.utils import timezone as django_tz
            current_period_end = django_tz.make_aware(datetime.fromtimestamp(int(cpe)))
    except Exception:
        current_period_end = None

    trial_end = None
    try:
        te = sub.get("trial_end")
        if te:
            from datetime import datetime
            from django.utils import timezone as django_tz
            trial_end = django_tz.make_aware(datetime.fromtimestamp(int(te)))
    except Exception:
        trial_end = None

    # Determine plan from metadata first, else from price id
    plan = None
    try:
        meta = sub.get("metadata", {}) or {}
        pid = meta.get("plan_id")
        if pid:
            plan = Plan.objects.filter(id=pid).first()
    except Exception:
        plan = None

    if not plan:
        try:
            items = ((sub.get("items") or {}).get("data") or [])
            price_id = None
            if items and isinstance(items[0], dict):
                price = items[0].get("price") or {}
                if isinstance(price, dict):
                    price_id = price.get("id")
            if price_id:
                plan = Plan.objects.filter(stripe_price_id=price_id).first()
        except Exception:
            plan = None

    defaults = {
        "stripe_subscription_id": subscription_id,
        "active": (status in ("active", "trialing")),
        "status": status,
        "cancel_at_period_end": cancel_at_period_end,
        "current_period_end": current_period_end,
        "trial_end": trial_end,
    }
    if plan:
        defaults["plan"] = plan

    Subscription.objects.update_or_create(organization=org, defaults=defaults)

    # If the org owner previously scheduled account deletion due to trial cancel-at-period-end,
    # clear that schedule once we have an active subscription that is not canceling.
    try:
        if (status in ("active", "trialing")) and (not cancel_at_period_end):
            from accounts.models import Profile
            owner = getattr(org, 'owner', None)
            if owner:
                Profile.objects.filter(user=owner, scheduled_account_deletion_reason='trial_cancel_at_period_end').update(
                    scheduled_account_deletion_at=None,
                    scheduled_account_deletion_reason=None,
                )
    except Exception:
        pass

    # Best-effort: sync payment methods and default card into our local cache so
    # manage.html reflects the card used in embedded checkout even when webhooks
    # are not running (common in local dev).
    pm_sync = {"ok": False}
    try:
        def _to_id(v):
            if not v:
                return None
            if isinstance(v, dict):
                return v.get("id")
            return str(v)

        cust = stripe.Customer.retrieve(org.stripe_customer_id)
        default_pm_id = _to_id((cust.get("invoice_settings") or {}).get("default_payment_method"))
        sub_default_pm_id = _to_id(sub.get("default_payment_method"))

        # Some Stripe flows can succeed without immediately attaching the newly
        # used PaymentMethod to the Customer. Try to infer it from the latest
        # invoice payment intent and attach/default it so it appears in the UI.
        invoice_pm_id = None
        try:
            latest_invoice_id = _to_id(sub.get('latest_invoice'))
            if latest_invoice_id:
                inv = stripe.Invoice.retrieve(latest_invoice_id, expand=['payments'])
                pays = (inv.get('payments') or {}).get('data') or []
                if pays and isinstance(pays[0], dict):
                    payment = pays[0].get('payment') or {}
                    pi = payment.get('payment_intent') if isinstance(payment, dict) else None
                    pi_obj = None
                    if isinstance(pi, dict):
                        pi_obj = pi
                    elif pi:
                        try:
                            pi_obj = stripe.PaymentIntent.retrieve(str(pi))
                        except Exception:
                            pi_obj = None
                    if pi_obj:
                        invoice_pm_id = _to_id(pi_obj.get('payment_method'))
        except Exception:
            invoice_pm_id = None

        pm_hint = sub_default_pm_id or invoice_pm_id
        if pm_hint:
            try:
                try:
                    stripe.PaymentMethod.attach(pm_hint, customer=org.stripe_customer_id)
                except Exception:
                    pass
                if not default_pm_id:
                    try:
                        stripe.Customer.modify(org.stripe_customer_id, invoice_settings={"default_payment_method": pm_hint})
                        default_pm_id = pm_hint
                    except Exception:
                        pass
            except Exception:
                pass

        # If Stripe hasn't set a customer default yet but the subscription has
        # one (common after first Payment Element confirmation), set it so the
        # billing manager shows a clear default card.
        if (not default_pm_id) and sub_default_pm_id:
            try:
                stripe.Customer.modify(org.stripe_customer_id, invoice_settings={"default_payment_method": sub_default_pm_id})
                default_pm_id = sub_default_pm_id
            except Exception:
                # Don't fail the overall sync if setting default fails.
                pass

        pms = stripe.PaymentMethod.list(customer=org.stripe_customer_id, type="card")
        pm_data = pms.get("data", []) if isinstance(pms, dict) else getattr(pms, "data", [])
        pm_ids = []
        for pm in (pm_data or []):
            pm_id = _to_id(pm.get("id") if isinstance(pm, dict) else getattr(pm, "id", None))
            if not pm_id:
                continue
            pm_ids.append(pm_id)

        # Remove stale cached methods that no longer exist on Stripe
        if pm_ids:
            PaymentMethod.objects.filter(organization=org).exclude(stripe_pm_id__in=pm_ids).delete()

        # Clear defaults then set based on Stripe customer invoice_settings
        PaymentMethod.objects.filter(organization=org).update(is_default=False)

        for pm in (pm_data or []):
            pm_id = _to_id(pm.get("id") if isinstance(pm, dict) else getattr(pm, "id", None))
            if not pm_id:
                continue
            card = pm.get("card") if isinstance(pm, dict) else getattr(pm, "card", None)
            card = card or {}
            defaults = {
                "brand": (card.get("brand") if isinstance(card, dict) else getattr(card, "brand", None)),
                "last4": (card.get("last4") if isinstance(card, dict) else getattr(card, "last4", None)),
                "exp_month": (card.get("exp_month") if isinstance(card, dict) else getattr(card, "exp_month", None)),
                "exp_year": (card.get("exp_year") if isinstance(card, dict) else getattr(card, "exp_year", None)),
                "is_default": bool(default_pm_id and pm_id == default_pm_id),
            }
            PaymentMethod.objects.update_or_create(organization=org, stripe_pm_id=pm_id, defaults=defaults)

        pm_sync = {"ok": True, "count": len(pm_ids), "default_payment_method": default_pm_id}
    except Exception as e:
        pm_sync = {"ok": False, "error": str(e)}

    return JsonResponse({
        "ok": True,
        "status": status,
        "plan": (plan.slug if plan else None),
        "plan_name": (plan.name if plan else None),
        "payment_methods": pm_sync,
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
    trial_total_days = None
    trial_end_iso = None
    next_billing_iso = None
    show_invoices = True
    show_upcoming_invoice = True
    # Enable AJAX invoice filtering by default; templates expect this flag.
    use_ajax_filters = True

    now = timezone.now()

    # Self-heal: older versions of the trial plan-change flow could overwrite
    # subscription.plan even though the org is still on a free trial (no paid
    # Stripe subscription). This makes the UI appear upgraded without payment.
    # For active local trials, force the current plan back to Basic and clear
    # any scheduled change.
    try:
        if subscription and subscription.status == 'trialing' and subscription.trial_end and subscription.trial_end > now and not getattr(subscription, 'stripe_subscription_id', None):
            basic_plan = Plan.objects.filter(slug='basic').first()
            needs_fix = False
            if basic_plan is not None and subscription.plan and getattr(subscription.plan, 'slug', None) != 'basic':
                subscription.plan = basic_plan
                needs_fix = True
            if getattr(subscription, 'scheduled_plan', None) is not None or getattr(subscription, 'scheduled_change_at', None) is not None:
                subscription.scheduled_plan = None
                subscription.scheduled_change_at = None
                needs_fix = True
            if needs_fix:
                try:
                    subscription.save(update_fields=['plan', 'scheduled_plan', 'scheduled_change_at'])
                except Exception:
                    subscription.save()
    except Exception:
        pass

    # Best-effort: reconcile local subscription state from Stripe.
    # This prevents stale UI (e.g., still showing "cancels at period end") after
    # a user resumes billing/changes plan and a webhook or client-side sync was missed.
    if subscription and getattr(subscription, 'stripe_subscription_id', None) and org.stripe_customer_id:
        try:
            stripe_sub = stripe.Subscription.retrieve(subscription.stripe_subscription_id)
            stripe_status = stripe_sub.get('status') or getattr(subscription, 'status', 'active')
            stripe_cancel = bool(stripe_sub.get('cancel_at_period_end', False))

            update_fields = []
            if getattr(subscription, 'status', None) != stripe_status:
                subscription.status = stripe_status
                update_fields.append('status')

            stripe_active = (stripe_status in ('active', 'trialing'))
            if getattr(subscription, 'active', None) != stripe_active:
                subscription.active = stripe_active
                update_fields.append('active')

            if getattr(subscription, 'cancel_at_period_end', None) != stripe_cancel:
                subscription.cancel_at_period_end = stripe_cancel
                update_fields.append('cancel_at_period_end')

            # Keep key timestamps in sync for display.
            try:
                from datetime import datetime
                from django.utils import timezone as django_tz
                cpe = stripe_sub.get('current_period_end')
                if cpe:
                    cpe_dt = django_tz.make_aware(datetime.fromtimestamp(int(cpe)))
                    if getattr(subscription, 'current_period_end', None) != cpe_dt:
                        subscription.current_period_end = cpe_dt
                        update_fields.append('current_period_end')
                te = stripe_sub.get('trial_end')
                if te:
                    te_dt = django_tz.make_aware(datetime.fromtimestamp(int(te)))
                    if getattr(subscription, 'trial_end', None) != te_dt:
                        subscription.trial_end = te_dt
                        update_fields.append('trial_end')
            except Exception:
                pass

            if update_fields:
                try:
                    subscription.save(update_fields=list(dict.fromkeys(update_fields)))
                except Exception:
                    subscription.save()
        except Exception:
            pass

    # Determine trial countdown regardless of Stripe subscription presence
    if subscription and subscription.status == "trialing" and subscription.trial_end and subscription.trial_end > now:
        delta = subscription.trial_end - now
        trial_remaining_seconds = int(delta.total_seconds())
        trial_remaining_days = delta.days
        trial_end_iso = subscription.trial_end.isoformat()
        try:
            if subscription.start_date:
                import math
                total_seconds = (subscription.trial_end - subscription.start_date).total_seconds()
                trial_total_days = max(1, int(math.ceil(total_seconds / 86400.0)))
        except Exception:
            trial_total_days = None
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
            # Capture the default card last4 for UI convenience when available
            default_card_last4 = default.last4 if default else None
        else:
            # Fallback to Stripe live API
            if org.stripe_customer_id:
                try:
                    pms = stripe.PaymentMethod.list(customer=org.stripe_customer_id, type="card")
                    payment_methods = pms.get("data", [])
                    cust = stripe.Customer.retrieve(org.stripe_customer_id)
                    default_payment_method_id = cust.get("invoice_settings", {}).get("default_payment_method")
                    # try to resolve default last4 from the retrieved payment methods
                    default_card_last4 = None
                    if default_payment_method_id and isinstance(payment_methods, list):
                        for pm in payment_methods:
                            pm_id = pm.get('id') if isinstance(pm, dict) else getattr(pm, 'id', None)
                            if pm_id == default_payment_method_id:
                                # pm may be a dict-like stripe object
                                card = pm.get('card') if isinstance(pm, dict) else getattr(pm, 'card', None) or {}
                                default_card_last4 = (card.get('last4') if isinstance(card, dict) else getattr(card, 'last4', None))
                                break
                except Exception:
                    payment_methods = []
                    default_card_last4 = None
    except Exception:
        # If cache lookup fails for any reason, fall back to Stripe
        try:
            if org.stripe_customer_id:
                pms = stripe.PaymentMethod.list(customer=org.stripe_customer_id, type="card")
                payment_methods = pms.get("data", [])
                cust = stripe.Customer.retrieve(org.stripe_customer_id)
                default_payment_method_id = cust.get("invoice_settings", {}).get("default_payment_method")
                # try to resolve default last4
                default_card_last4 = None
                if default_payment_method_id and isinstance(payment_methods, list):
                    for pm in payment_methods:
                        pm_id = pm.get('id') if isinstance(pm, dict) else getattr(pm, 'id', None)
                        if pm_id == default_payment_method_id:
                            card = pm.get('card') if isinstance(pm, dict) else getattr(pm, 'card', None) or {}
                            default_card_last4 = (card.get('last4') if isinstance(card, dict) else getattr(card, 'last4', None))
                            break
        except Exception:
            payment_methods = []
            default_card_last4 = None

    # Respect optional query param to show archived (hidden) invoices
    show_archived = str(request.GET.get('show_archived', '')).lower() in ('1', 'true', 'yes')

    # Only pull invoices if a real Stripe subscription exists
    if subscription and subscription.stripe_subscription_id and org.stripe_customer_id:
        try:
            if show_invoices:
                # Scope invoice listing to the specific subscription so the UI
                # shows invoices relevant to this subscription (avoids mixing
                # invoices from other subscriptions on the same customer).
                try:
                    invs = stripe.Invoice.list(subscription=subscription.stripe_subscription_id, limit=10)
                except Exception:
                    # Fallback to customer-wide listing if subscription-scoped list fails
                    invs = stripe.Invoice.list(customer=org.stripe_customer_id, limit=10)

                raw_invoices = invs.get("data", [])
                invoices = []
                from datetime import datetime
                for i in raw_invoices:
                    # Prefer showing the amount actually paid for paid invoices,
                    # otherwise fall back to amount_due for open/draft invoices.
                    amount_display = i.get('amount_paid') if i.get('amount_paid', 0) else i.get('amount_due', 0)
                    # Convert Stripe unix timestamp to aware datetime for template rendering
                    created_ts = i.get('created')
                    created_dt = None
                    try:
                        if created_ts:
                            # Stripe timestamps are UTC seconds. Create a UTC-aware
                            # datetime then convert to the current Django timezone
                            from datetime import datetime, timezone as dt_tz
                            created_utc = datetime.fromtimestamp(int(created_ts), tz=dt_tz.utc)
                            created_dt = timezone.localtime(created_utc)
                    except Exception:
                        created_dt = None

                    invoices.append({
                        'created': created_dt,
                        'amount_display_dollars': (amount_display / 100.0),
                        'status': i.get('status'),
                        'hosted_invoice_url': i.get('hosted_invoice_url'),
                        'card_brand': None,
                        'card_last4': None,
                        'raw': i,
                    })

                # Try to enrich invoices with card details where available
                for idx, entry in enumerate(invoices):
                    raw = entry.get('raw')
                    try:
                        card_brand = None
                        card_last4 = None
                        # Prefer payment_intent -> charges
                        pi = raw.get('payment_intent')
                        if pi:
                            try:
                                pi_obj = stripe.PaymentIntent.retrieve(pi) if isinstance(pi, str) else pi
                                charges = pi_obj.get('charges', {}).get('data', [])
                                if charges:
                                    ch = charges[0]
                                    pm_card = ch.get('payment_method_details', {}).get('card', {})
                                    card_brand = pm_card.get('brand')
                                    card_last4 = pm_card.get('last4')
                            except Exception:
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

    # --- Include local scheduled subscription changes as pseudo-invoices ---
    # These are local DB records (SubscriptionChange) that represent
    # scheduled downgrades/upgrades. We surface them in the invoice list as
    # zero-dollar entries with no card so the user can see upcoming changes.
    try:
        from billing.models import SubscriptionChange
        # Show most recent 10 changes for this org
        sch_changes = SubscriptionChange.objects.filter(organization=org).order_by('-created_at')[:10]
        for sc in sch_changes:
            try:
                # Ensure subscription change timestamps are localized to Django timezone
                created_dt = sc.created_at
                try:
                    # If naive, make aware then localize; if aware, just localize
                    from django.utils import timezone as dj_tz
                    if timezone.is_naive(created_dt):
                        created_dt = dj_tz.make_aware(created_dt, dj_tz.get_current_timezone())
                    created_dt = dj_tz.localtime(created_dt)
                except Exception:
                    # fallback to original value if localization fails
                    created_dt = sc.created_at
                invoices.append({
                    'created': created_dt,
                    'amount_display_dollars': float((sc.amount_cents or 0) / 100.0),
                    # Per UX request: display as paid/settled with no card
                    'status': 'paid',
                    'hosted_invoice_url': None,
                    'card_brand': sc.card_brand,
                    'card_last4': sc.card_last4,
                    'raw': {
                        'pseudo': True,
                        'type': 'subscription_change',
                        'id': sc.id,
                        'new_plan': sc.new_plan.id if sc.new_plan else None,
                        'new_plan_name': sc.new_plan.name if sc.new_plan else None,
                        'change_type': sc.change_type,
                        'effective_at': sc.effective_at if sc.effective_at else None,
                        'status': sc.status,
                        'amount_cents': sc.amount_cents,
                        'card_brand': sc.card_brand,
                        'card_last4': sc.card_last4,
                        'stripe_invoice_id': sc.stripe_invoice_id,
                    },
                })
            except Exception:
                # don't let one bad change break invoice listing
                continue
    except Exception:
        # If the model/table is not present yet (before migrations), skip quietly
        pass

    # If a local applied discount exists and we have an upcoming invoice,
    # add a pseudo-invoice entry so the Invoices section clearly shows
    # that a discount will be applied on the next billing date.
    try:
        # `applied_discount` is computed later; safe-guard by reading it from locals
        _ad = locals().get('applied_discount')
        _ui = locals().get('upcoming_invoice')
        if _ad and _ui:
            orig = float(_ui.get('amount_due_dollars') or 0.0)
            if _ad.get('percent_off') is not None:
                discount_amt = orig * (float(_ad.get('percent_off')) / 100.0)
            else:
                discount_amt = float(( _ad.get('amount_off_cents') or 0 ) / 100.0)
            adjusted = max(0.0, orig - discount_amt)

            created_dt = _ui.get('billing_date') if isinstance(_ui.get('billing_date'), type(timezone.now())) else timezone.now()

            invoices.append({
                'created': created_dt,
                'amount_display_dollars': float(adjusted),
                'status': 'paid',
                'hosted_invoice_url': None,
                'card_brand': None,
                'card_last4': None,
                'raw': {
                    'pseudo': True,
                    'type': 'applied_discount',
                    'original_amount': orig,
                    'discount_amount': discount_amt,
                    'adjusted_amount': adjusted,
                    'discount': _ad,
                    'billing_date': _ui.get('billing_date'),
                },
            })
    except Exception:
        pass

    # --- Annotate invoices with hidden metadata so client can toggle archived rows ---
    try:
        from billing.models import InvoiceMeta
        hidden_qs = InvoiceMeta.objects.filter(organization=org, hidden=True)
        hidden_stripe_ids = set([s for s in hidden_qs.values_list('stripe_invoice_id', flat=True) if s])
        hidden_change_ids = set([c for c in hidden_qs.values_list('subscription_change_id', flat=True) if c])

        # Always annotate invoices with a boolean 'hidden' flag for UI use.
        for inv in invoices:
            raw = inv.get('raw') or {}
            if raw.get('pseudo'):
                inv['hidden'] = (raw.get('id') in hidden_change_ids)
            else:
                inv['hidden'] = ((raw.get('id') or '') in hidden_stripe_ids)
    except Exception:
        hidden_stripe_ids = set()
        hidden_change_ids = set()

    # --- Apply invoice filtering & sorting based on query params ---
    # Supported filters:
    # - sort: latest|earliest|lowest|highest
    # - card: last4 string to filter by card used
    # - status: invoice status (paid|open|draft|scheduled|processed|all)
    try:
        sort_by = (request.GET.get('inv_sort') or 'latest').lower()
        filter_card = request.GET.get('inv_card')
        filter_status = request.GET.get('inv_status')

        # Build a normalized status value for pseudo-invoices
        def _status_of(inv):
            raw = inv.get('raw') or {}
            if raw.get('pseudo'):
                return (raw.get('status') or 'scheduled')
            return (inv.get('status') or '').lower()

        # Filter by card last4 — consider the org's default card as a fallback
        if filter_card:
            try:
                fallback_last4 = default_card_last4
            except NameError:
                fallback_last4 = None
            def _matches_card(i):
                v = i.get('card_last4') or (i.get('raw', {}) or {}).get('card_last4') or fallback_last4
                return (v == filter_card)
            invoices = [i for i in invoices if _matches_card(i)]

        # Filter by status
        if filter_status and filter_status.lower() not in ('', 'all'):
            fs = filter_status.lower()
            invoices = [i for i in invoices if _status_of(i) == fs]

        # Sorting
        if sort_by == 'latest':
            invoices.sort(key=lambda x: x.get('created') or timezone.make_aware(timezone.datetime(1970,1,1)), reverse=True)
        elif sort_by == 'earliest':
            invoices.sort(key=lambda x: x.get('created') or timezone.make_aware(timezone.datetime(1970,1,1)))
        elif sort_by == 'lowest':
            invoices.sort(key=lambda x: float(x.get('amount_display_dollars') or 0.0))
        elif sort_by == 'highest':
            invoices.sort(key=lambda x: float(x.get('amount_display_dollars') or 0.0), reverse=True)
    except Exception:
        # If any filter fails, continue without filtering
        pass

    # Collect available card last4 values and status options for UI selects
    try:
        invoice_card_options = []
        invoice_status_options = set()
        for i in invoices:
            card = i.get('card_last4') or (i.get('raw') or {}).get('card_last4')
            if card and card not in invoice_card_options:
                invoice_card_options.append(card)
            st = (i.get('raw') or {}).get('status') if (i.get('raw') or {}).get('pseudo') else (i.get('status') or '')
            st_norm = (st or '').lower()
            if st_norm:
                invoice_status_options.add(st_norm)
        invoice_status_options = sorted(list(invoice_status_options))
    except Exception:
        invoice_card_options = []
        invoice_status_options = []

    # Ensure the default card last4 appears in the card options so users can
    # filter by their default even if no invoice was paid with that card.
    try:
        if 'default_card_last4' in locals() and default_card_last4:
            if default_card_last4 not in invoice_card_options:
                invoice_card_options.insert(0, default_card_last4)
    except Exception:
        pass

    # --- Group invoices by month and support month-based paging ---
    # Build a list of months available from the invoices (YYYY-MM keys)
    months = []
    invoices_by_month = {}
    try:
        for inv in invoices:
            created = inv.get('created')
            if not created:
                key = 'unknown'
                label = 'Unknown'
            else:
                key = created.strftime('%Y-%m')
                label = created.strftime('%B %Y')
            if key not in invoices_by_month:
                invoices_by_month[key] = {'label': label, 'items': []}
            invoices_by_month[key]['items'].append(inv)

        # Sort months descending (newest first)
        months = [{'key': k, 'label': invoices_by_month[k]['label']} for k in sorted(invoices_by_month.keys(), reverse=True)]
    except Exception:
        months = []
        invoices_by_month = {}

    # Choose selected month from query param `invoice_month`; default to newest
    selected_month = request.GET.get('invoice_month')
    if not selected_month:
        selected_month = months[0]['key'] if months else None

    # Provide paged invoices (only the selected month's items) to the template
    paged_invoices = []
    selected_month_label = None
    if selected_month and selected_month in invoices_by_month:
        paged_invoices = invoices_by_month[selected_month]['items']
        selected_month_label = invoices_by_month[selected_month]['label']
    else:
        # If no month selected or not found, show all invoices (fallback)
        paged_invoices = invoices
        selected_month_label = None

    # Determine the upcoming invoice / next recurring charge. Prefer a
    # subscription-scoped upcoming invoice from Stripe, but fall back to
    # showing the subscription's recurring plan price if Stripe doesn't
    # return a usable upcoming invoice.
    try:
        if subscription and subscription.stripe_subscription_id and org.stripe_customer_id and show_upcoming_invoice:
            try:
                ui = stripe_invoice_upcoming(customer=org.stripe_customer_id, subscription=subscription.stripe_subscription_id)
            except Exception:
                try:
                    ui = stripe_invoice_upcoming(customer=org.stripe_customer_id)
                    if ui and ui.get('subscription') and ui.get('subscription') != subscription.stripe_subscription_id:
                        ui = None
                except Exception:
                    ui = None

            billing_date = None
            amount_due = None
            if ui:
                billing_timestamp = ui.get('period_end') or ui.get('created')
                if billing_timestamp:
                    try:
                        from datetime import datetime, timezone as dt_tz
                        billing_utc = datetime.fromtimestamp(int(billing_timestamp), tz=dt_tz.utc)
                        billing_date = timezone.localtime(billing_utc)
                    except Exception:
                        billing_date = None
                amount_due = (ui.get('amount_due', 0) / 100.0) if ui.get('amount_due') is not None else None

            # Prefer showing the price that will actually be billed next.
            # If a scheduled change exists and is set to take effect at the
            # end of the current period, show the scheduled_plan price. Fall
            # back to the subscription's current plan price, then to Stripe's
            # upcoming invoice amount if available.
            upcoming_invoice = None
            try:
                # 1) If there's a scheduled_plan and it's scheduled to apply
                # at (or very near) the subscription.current_period_end, show
                # the scheduled plan price as the next charge.
                if subscription and getattr(subscription, 'scheduled_plan', None) and subscription.scheduled_change_at and getattr(subscription.scheduled_plan, 'price', None) is not None and subscription.current_period_end:
                    try:
                        sched = subscription.scheduled_change_at
                        cpe = subscription.current_period_end
                        # Accept small time differences (<= 1 day) as equivalent
                        if sched and cpe and abs((sched - cpe).total_seconds()) <= 86400:
                            upcoming_invoice = {
                                'billing_date': subscription.current_period_end,
                                'amount_due_dollars': float(subscription.scheduled_plan.price),
                            }
                    except Exception:
                        # ignore and fall through to other fallbacks
                        upcoming_invoice = None

                # 2) If no scheduled change applies, prefer the subscription's
                # current plan price (this represents the recurring charge).
                if upcoming_invoice is None and subscription and getattr(subscription, 'plan', None) and subscription.plan.price is not None:
                    upcoming_invoice = {
                        'billing_date': subscription.current_period_end,
                        'amount_due_dollars': float(subscription.plan.price),
                    }

                # 3) As a last resort, use the Stripe upcoming invoice amount
                # (if we were able to retrieve one above).
                if upcoming_invoice is None and amount_due is not None:
                    upcoming_invoice = {
                        'billing_date': billing_date,
                        'amount_due_dollars': amount_due,
                    }

                # If we still don't have a billing date, prefer the local
                # subscription.current_period_end (if available) so the UI can
                # show a next-charge date even when Stripe preview omits it.
                if upcoming_invoice and not upcoming_invoice.get('billing_date'):
                    if subscription and getattr(subscription, 'current_period_end', None):
                        upcoming_invoice['billing_date'] = subscription.current_period_end
                    elif billing_date:
                        upcoming_invoice['billing_date'] = billing_date
            except Exception:
                upcoming_invoice = None
        else:
            upcoming_invoice = None
    except Exception:
        upcoming_invoice = None

    # For non-trial subscriptions, provide a countdown target for the next charge / period end.
    # NOTE: This must run after upcoming_invoice is populated, because the
    # Stripe-derived billing_date is often the only reliable timestamp.
    if not trial_remaining_seconds:
        try:
            target = None
            if isinstance(upcoming_invoice, dict) and upcoming_invoice.get('billing_date'):
                target = upcoming_invoice.get('billing_date')
            if not target and subscription and getattr(subscription, 'current_period_end', None):
                target = subscription.current_period_end
            if target and target > now:
                next_billing_iso = target.isoformat()
            else:
                next_billing_iso = None
        except Exception:
            next_billing_iso = None

    # --- Detect locally applied discount (AppliedDiscount) for display ---
    applied_discount = None
    try:
        from billing.models import AppliedDiscount
        if subscription:
            ad = AppliedDiscount.objects.filter(subscription=subscription, active=True).order_by('-applied_at').first()
            if ad:
                applied_discount = {
                    'code': ad.discount_code.code,
                    'percent_off': float(ad.discount_code.percent_off) if ad.discount_code.percent_off is not None else None,
                    'amount_off_cents': ad.discount_code.amount_off_cents,
                    'currency': ad.discount_code.currency,
                    'applied_at': ad.applied_at.isoformat() if ad.applied_at else None,
                    'proration_behavior': ad.proration_behavior,
                    'source': 'local',
                    'stripe_coupon_id': ad.stripe_coupon_id,
                }
    except Exception:
        applied_discount = None
    

    # Determine whether a scheduled change is a downgrade (new plan price < current plan price)
    scheduled_change_is_downgrade = None
    if subscription and getattr(subscription, 'scheduled_plan', None) and getattr(subscription, 'plan', None):
        try:
            cur_p = float(subscription.plan.price) if subscription.plan.price is not None else None
            new_p = float(subscription.scheduled_plan.price) if subscription.scheduled_plan.price is not None else None
            if cur_p is not None and new_p is not None:
                scheduled_change_is_downgrade = (new_p < cur_p)
        except Exception:
            scheduled_change_is_downgrade = None

    # AJAX filtering is always enabled client-side; the template will request JSON pages.
    use_ajax_filters = True

    # If requested as JSON (AJAX fetch), return server-side filtered + paged invoices as JSON payload
    if str(request.GET.get('as_json', '')).lower() in ('1', 'true', 'yes'):
        # Respect same filters used for the page: inv_sort, inv_card, inv_status, invoice_month, show_archived
        page = 1
        try:
            page = max(1, int(request.GET.get('page', '1')))
        except Exception:
            page = 1
        page_size = 25
        try:
            page_size = max(5, min(200, int(request.GET.get('page_size', page_size))))
        except Exception:
            page_size = 25

        # Apply month filter server-side for paging
        invoice_month = request.GET.get('invoice_month')
        def _in_month(inv, month_key):
            if not month_key:
                return True
            created = inv.get('created')
            if not created:
                return False
            try:
                key = created.strftime('%Y-%m')
                return key == month_key or key.startswith(month_key[:7])
            except Exception:
                return False

        filtered = []
        for inv in invoices:
            # honor show_archived param: when show_archived not set, exclude hidden
            if not show_archived and inv.get('hidden'):
                continue
            if invoice_month and not _in_month(inv, invoice_month):
                continue
            filtered.append(inv)

        total = len(filtered)
        start = (page - 1) * page_size
        end = start + page_size
        page_items = filtered[start:end]

        # Ensure we have a stable fallback for card last4 when invoices lack it
        try:
            fallback_last4 = default_card_last4
        except NameError:
            fallback_last4 = None

        def _serialize(inv):
            raw = inv.get('raw') or {}
            created_dt = inv.get('created')
            created_iso = created_dt.isoformat() if created_dt else None
            created_display = created_dt.strftime('%B %d, %Y %I:%M %p') if created_dt else None
            payload = {
                'id': raw.get('id') if isinstance(raw, dict) else None,
                'pseudo': bool(raw.get('pseudo')) if isinstance(raw, dict) else False,
                'created_iso': created_iso,
                'created_display': created_display,
                'amount_display_dollars': inv.get('amount_display_dollars'),
                'status': ((raw.get('status') or inv.get('status')) if isinstance(raw, dict) and raw.get('pseudo') else inv.get('status')),
                'hosted_invoice_url': inv.get('hosted_invoice_url'),
                'card_brand': inv.get('card_brand'),
                'card_last4': (inv.get('card_last4') or fallback_last4),
                'hidden': bool(inv.get('hidden')),
                'discount': (raw.get('discount') if isinstance(raw, dict) and raw.get('discount') else applied_discount),
            }

            # Include the pseudo-invoice type for UI rendering.
            try:
                if isinstance(raw, dict) and raw.get('pseudo'):
                    payload['type'] = raw.get('type')
            except Exception:
                pass

            # Include subscription-change details for pseudo-invoices so the
            # billing UI can render the "View change" modal consistently even
            # when invoice rows are built client-side.
            try:
                if isinstance(raw, dict) and raw.get('pseudo') and raw.get('type') == 'subscription_change':
                    payload.update({
                        'type': raw.get('type'),
                        'change_type': raw.get('change_type'),
                        'new_plan_name': raw.get('new_plan_name'),
                        'amount_cents': raw.get('amount_cents'),
                        'stripe_invoice_id': raw.get('stripe_invoice_id'),
                    })
                    eff = raw.get('effective_at')
                    if eff is not None:
                        try:
                            payload['effective_at'] = eff.isoformat()
                        except Exception:
                            payload['effective_at'] = str(eff)
                    else:
                        payload['effective_at'] = None
            except Exception:
                # Never fail invoice listing because of extra serialization
                pass

            return payload

        data = [_serialize(i) for i in page_items]
        has_more = end < total
        return JsonResponse({'data': data, 'meta': {'total': total, 'page': page, 'page_size': page_size, 'has_more': has_more}})

    return render(request, "billing/manage.html", {
        "org": org,
        "subscription": subscription,
        "plans": plans,
        "scheduled_plan": (subscription.scheduled_plan if subscription and getattr(subscription, 'scheduled_plan', None) else None),
        "scheduled_change_is_downgrade": scheduled_change_is_downgrade,
        # Expose a display_plan that falls back to a sensible default when
        # subscription.plan is missing (e.g., trial created without plan).
        "display_plan": (subscription.plan if subscription and subscription.plan else Plan.objects.filter(slug='basic').first()),
        "stripe_publishable_key": publishable_key,
        "payment_methods": payment_methods,
        "default_payment_method_id": default_payment_method_id,
        "use_ajax_filters": use_ajax_filters,
        "has_payment_methods": bool(payment_methods),
        "invoices": invoices,
        "months": months,
        "selected_month": selected_month,
        "paged_invoices": paged_invoices,
        "upcoming_invoice": upcoming_invoice,
        "trial_remaining_seconds": trial_remaining_seconds,
        "trial_remaining_days": trial_remaining_days,
        "trial_total_days": trial_total_days,
        "trial_end_iso": trial_end_iso,
        "next_billing_iso": next_billing_iso,
        "checkout_status": request.GET.get('checkout', ''),
        "checkout_sub_id": request.GET.get('sub', ''),
        "checkout_plan_id": request.GET.get('plan', ''),
        "invoice_card_options": invoice_card_options,
        "invoice_status_options": invoice_status_options,
        "current_inv_sort": request.GET.get('inv_sort', 'latest'),
        "current_inv_card": request.GET.get('inv_card', ''),
        "current_inv_status": request.GET.get('inv_status', ''),
        "show_archived": show_archived,
        # Provide JSON-serializable strings for use in client-side JS
        "hidden_stripe_ids": list(hidden_stripe_ids) if 'hidden_stripe_ids' in locals() else [],
        "hidden_change_ids": list(hidden_change_ids) if 'hidden_change_ids' in locals() else [],
        "applied_discount": applied_discount,
        "default_card_last4": (locals().get('default_card_last4') if 'default_card_last4' in locals() else None),
        # Upcoming invoice adjusted total when a local applied discount exists
        "upcoming_invoice_adjusted": (None if not (upcoming_invoice and applied_discount) else (
            (lambda ai, ad: {
                'original': ai.get('amount_due_dollars'),
                'discount_amount': ( (ai.get('amount_due_dollars') * (float(ad.get('percent_off')) / 100.0)) if ad.get('percent_off') is not None else ( (ad.get('amount_off_cents') or 0) / 100.0) ),
                'adjusted': (ai.get('amount_due_dollars') - ( (ai.get('amount_due_dollars') * (float(ad.get('percent_off')) / 100.0)) if ad.get('percent_off') is not None else ( (ad.get('amount_off_cents') or 0) / 100.0) ))
            })(upcoming_invoice, applied_discount)
        )),
    })


@require_http_methods(["POST"])
def create_setup_intent(request, org_slug):
    org = get_object_or_404(Organization, slug=org_slug)
    err = _require_org_owner_or_admin(request, org)
    if err:
        return err

    if not org.stripe_customer_id:
        # Create customer if still missing
        customer = stripe.Customer.create(
            name=getattr(org, "name", None) or str(getattr(org, "slug", "")) or None,
            email=request.user.email,
            metadata={"organization_id": str(org.id)},
        )
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


@require_http_methods(["POST"])
def resubscribe_subscription(request, org_slug):
    """Create a new Stripe subscription for an org when previous subscription was canceled.

    Expects JSON body with optional `plan_id` to select which plan to subscribe to.
    If no `plan_id` provided, uses the local `subscription.plan` if present.
    """
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
        logger.error(f"JSON decode error in resubscribe_subscription: {e}")
        return HttpResponseBadRequest(f"Invalid JSON: {e}")

    plan_id = body.get('plan_id')
    if plan_id:
        try:
            plan = Plan.objects.get(id=plan_id)
        except Plan.DoesNotExist:
            return HttpResponseBadRequest('Plan not found.')
    else:
        sub = getattr(org, 'subscription', None)
        if not sub or not sub.plan:
            return HttpResponseBadRequest('No plan specified and no local subscription plan available.')
        plan = sub.plan

    if not plan.stripe_price_id:
        return HttpResponseBadRequest('Plan missing Stripe price id.')

    # Ensure stripe customer exists
    if not org.stripe_customer_id:
        return HttpResponseBadRequest('Organization has no Stripe customer. Add a payment method first.')

    # Find default payment method if available
    default_pm = None
    try:
        cust = stripe.Customer.retrieve(org.stripe_customer_id)
        default_pm = cust.get('invoice_settings', {}).get('default_payment_method')
    except Exception:
        default_pm = None

    if not default_pm:
        # Try cached default
        cached = PaymentMethod.objects.filter(organization=org, is_default=True).first()
        if cached:
            default_pm = cached.stripe_pm_id

    # If still no payment method, try to list attached methods and pick first
    if not default_pm:
        try:
            pms = stripe.PaymentMethod.list(customer=org.stripe_customer_id, type='card')
            if pms and pms.get('data'):
                default_pm = pms['data'][0]['id']
        except Exception:
            default_pm = None

    if not default_pm:
        return HttpResponseBadRequest('No payment method available. Add a card first.')

    try:
        create_kwargs = {
            'customer': org.stripe_customer_id,
            'items': [{'price': plan.stripe_price_id}],
            'default_payment_method': default_pm,
            'expand': ['latest_invoice.payment_intent'],
            'metadata': {'organization_id': str(org.id), 'plan_id': str(plan.id)}
        }
        new_sub = stripe.Subscription.create(**create_kwargs)
        # Update local subscription row
        subs, created = Subscription.objects.get_or_create(organization=org)
        old_stripe_id = subs.stripe_subscription_id
        subs.stripe_subscription_id = new_sub.id
        subs.plan = plan
        subs.status = new_sub.get('status') or 'active'
        subs.active = (subs.status == 'active' or subs.status == 'trialing')
        try:
            if getattr(new_sub, 'current_period_end', None):
                from datetime import datetime
                subs.current_period_end = timezone.make_aware(datetime.fromtimestamp(new_sub.current_period_end))
        except Exception:
            pass
        subs.scheduled_plan = None
        subs.scheduled_change_at = None
        subs.save()

        logger.info(f"Created new Stripe subscription {new_sub.id} for org {org.slug} (replaced {old_stripe_id})")
        return JsonResponse({'status': 'ok', 'subscription_id': new_sub.id})
    except Exception as e:
        logger.exception('Failed to create new subscription in resubscribe_subscription')
        return HttpResponseBadRequest(str(e))


@require_http_methods(["GET"])
def list_payment_methods(request, org_slug):
    """Return JSON list of card payment methods for the organization (used by client to refresh UI)."""
    org = get_object_or_404(Organization, slug=org_slug)
    err = _require_org_owner_or_admin(request, org)
    if err:
        return err

    if not org.stripe_customer_id:
        return JsonResponse({"data": [], "default_payment_method_id": None})

    try:
        default_pm_id = None
        try:
            cust = stripe.Customer.retrieve(org.stripe_customer_id)
            default_pm_id = (cust.get('invoice_settings') or {}).get('default_payment_method')
        except Exception:
            default_pm_id = None

        pms = stripe.PaymentMethod.list(customer=org.stripe_customer_id, type="card")
        data = []
        for pm in pms.get('data', []):
            card = pm.card if hasattr(pm, 'card') else pm.get('card', {})
            pm_id = pm.id
            data.append({
                'id': pm_id,
                'brand': (card.brand if card else pm.get('card', {}).get('brand')),
                'last4': (card.last4 if card else pm.get('card', {}).get('last4')),
                'exp_month': (card.exp_month if card else pm.get('card', {}).get('exp_month')),
                'exp_year': (card.exp_year if card else pm.get('card', {}).get('exp_year')),
                'is_default': bool(default_pm_id and str(pm_id) == str(default_pm_id)),
            })
        return JsonResponse({"data": data, "default_payment_method_id": default_pm_id})
    except Exception:
        return JsonResponse({"data": [], "default_payment_method_id": None})


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
    schedule_delete_at_trial_end = bool(body.get("schedule_delete_at_trial_end", False))
    sub = getattr(org, "subscription", None)
    if not sub:
        return HttpResponseBadRequest("No subscription.")

    # If there's no Stripe subscription id (local-only trial/subscription),
    # handle cancellation scheduling locally.
    if not sub.stripe_subscription_id:
        # Immediate cancel: end access now.
        if immediate:
            sub.status = "canceled"
            sub.active = False
            sub.end_date = timezone.now()
            sub.cancel_at_period_end = False
            sub.save()
            return JsonResponse({"status": "ok", "message": "Local subscription canceled immediately (no Stripe id)."})

        # Cancel at period end: keep trial/period running; schedule end locally.
        is_trial = (getattr(sub, 'status', '') == 'trialing') or bool(getattr(sub, 'trial_end', None))
        period_end = getattr(sub, 'trial_end', None) if is_trial else getattr(sub, 'current_period_end', None)

        sub.cancel_at_period_end = True
        # If we have an end timestamp, store it in current_period_end for UI consistency.
        if period_end and not getattr(sub, 'current_period_end', None):
            sub.current_period_end = period_end
        sub.save()

        resp = {"status": "ok", "is_trial": bool(is_trial)}

        # Non-trial: send cancellation scheduled email immediately.
        if not is_trial:
            try:
                from accounts.emails import send_subscription_cancellation_scheduled_email
                target_user = getattr(org, 'owner', None) or request.user
                resp["email_to"] = getattr(target_user, 'email', None)
                resp["email_sent"] = bool(send_subscription_cancellation_scheduled_email(
                    target_user,
                    business_name=getattr(org, 'name', '') or 'your business',
                    scheduled_for=period_end,
                ))
            except Exception:
                resp["email_sent"] = False
            resp["message"] = "Cancellation scheduled for period end."
            return JsonResponse(resp)

        # Trial: always schedule deactivation + send scheduled-deactivation email immediately.
        try:
            from accounts.models import Profile
            from accounts.emails import send_trial_deletion_scheduled_email
            target_user = getattr(org, 'owner', None) or request.user
            resp["email_to"] = getattr(target_user, 'email', None)

            if period_end:
                profile, _ = Profile.objects.get_or_create(user=target_user)
                profile.scheduled_account_deletion_at = period_end
                profile.scheduled_account_deletion_reason = 'trial_cancel_at_period_end'
                profile.save(update_fields=['scheduled_account_deletion_at', 'scheduled_account_deletion_reason'])

            business_names = [getattr(org, 'name', '')] if getattr(org, 'name', None) else []
            resp["trial_deletion_email_sent"] = bool(send_trial_deletion_scheduled_email(
                target_user,
                scheduled_for=period_end,
                business_names=business_names,
            ))
        except Exception:
            resp["trial_deletion_email_sent"] = False

        resp["message"] = "Cancellation scheduled for trial end. Your account will be deactivated at trial end unless you subscribe before then."
        return JsonResponse(resp)

    try:
        if immediate:
            logger.info(f"Immediate cancel for org {org_slug}, subscription {sub.stripe_subscription_id}")
            try:
                stripe.Subscription.delete(sub.stripe_subscription_id)
            except InvalidRequestError as e:
                # Treat "No such subscription" as already canceled
                if 'No such subscription' in str(e):
                    logger.warning(f"Stripe subscription already missing for {sub.stripe_subscription_id}")
                else:
                    raise
            sub.status = "canceled"
            sub.active = False
            sub.end_date = timezone.now()
        else:
            logger.info(f"Cancel at period end for org {org_slug}, subscription {sub.stripe_subscription_id}")
            try:
                stripe_sub = stripe.Subscription.modify(sub.stripe_subscription_id, cancel_at_period_end=True)
                sub.cancel_at_period_end = True

                # Best-effort sync of timestamps for messaging/emails.
                try:
                    from datetime import datetime
                    from django.utils import timezone as django_tz
                    cpe = None
                    te = None
                    if isinstance(stripe_sub, dict):
                        cpe = stripe_sub.get('current_period_end')
                        te = stripe_sub.get('trial_end')
                    if cpe:
                        sub.current_period_end = django_tz.make_aware(datetime.fromtimestamp(int(cpe)))
                    if te:
                        sub.trial_end = django_tz.make_aware(datetime.fromtimestamp(int(te)))
                except Exception:
                    pass
            except InvalidRequestError as e:
                # If subscription missing, mark canceled immediately
                if 'No such subscription' in str(e):
                    logger.warning(f"Stripe subscription missing while scheduling cancel for {sub.stripe_subscription_id}; marking canceled locally")
                    sub.status = "canceled"
                    sub.active = False
                    sub.end_date = timezone.now()
                else:
                    raise
        sub.save()
    except InvalidRequestError as e:
        logger.error(f"Stripe InvalidRequestError in cancel_subscription: {e}")
        return HttpResponseBadRequest(str(e))
    except Exception as e:
        logger.exception(f"Stripe error in cancel_subscription: {e}")
        return HttpResponseBadRequest(str(e))
    # Schedule emails / trial deletion scheduling.
    message = None
    email_sent = None
    deletion_email_sent = None
    email_to = None
    is_trial_flag = None

    if not immediate:
        is_trial = (getattr(sub, 'status', '') == 'trialing') or bool(getattr(sub, 'trial_end', None))
        is_trial_flag = bool(is_trial)
        period_end = getattr(sub, 'trial_end', None) if is_trial else getattr(sub, 'current_period_end', None)

        # Non-trial: always send a cancellation scheduled email immediately.
        if not is_trial:
            try:
                from accounts.emails import send_subscription_cancellation_scheduled_email
                target_user = getattr(org, 'owner', None) or request.user
                email_to = getattr(target_user, 'email', None)
                email_sent = bool(send_subscription_cancellation_scheduled_email(
                    target_user,
                    business_name=getattr(org, 'name', '') or 'your business',
                    scheduled_for=period_end,
                ))
            except Exception:
                email_sent = False
            message = "Cancellation scheduled for period end."

        # Trial: schedule deactivation + send scheduled-deactivation email immediately.
        # Do this whenever a trial is set to cancel-at-period-end (no reliance on client flags).
        if is_trial:
            delete_at = getattr(sub, 'trial_end', None) or getattr(sub, 'current_period_end', None)
            try:
                from accounts.models import Profile
                from accounts.emails import send_trial_deletion_scheduled_email

                target_user = getattr(org, 'owner', None) or request.user
                email_to = getattr(target_user, 'email', None)
                if delete_at:
                    profile, _ = Profile.objects.get_or_create(user=target_user)
                    profile.scheduled_account_deletion_at = delete_at
                    profile.scheduled_account_deletion_reason = 'trial_cancel_at_period_end'
                    profile.save(update_fields=['scheduled_account_deletion_at', 'scheduled_account_deletion_reason'])

                business_names = [getattr(org, 'name', '')] if getattr(org, 'name', None) else []
                deletion_email_sent = bool(send_trial_deletion_scheduled_email(
                    target_user,
                    scheduled_for=(delete_at or period_end),
                    business_names=business_names,
                ))

                message = "Cancellation scheduled for trial end. Your account will be deactivated at trial end unless you subscribe before then."
            except Exception:
                logger.exception('Failed to schedule account deletion at trial end')
                deletion_email_sent = False
                message = "Cancellation scheduled for trial end."

    resp = {"status": "ok"}
    if message:
        resp["message"] = message
    if email_sent is not None:
        resp["email_sent"] = email_sent
    if deletion_email_sent is not None:
        resp["trial_deletion_email_sent"] = deletion_email_sent
    if email_to is not None:
        resp["email_to"] = email_to
    if is_trial_flag is not None:
        resp["is_trial"] = is_trial_flag
    return JsonResponse(resp)


@require_http_methods(["POST"])
def reactivate_subscription(request, org_slug):
    org = get_object_or_404(Organization, slug=org_slug)
    err = _require_org_owner_or_admin(request, org)
    if err:
        return err
    sub = getattr(org, "subscription", None)
    if not sub:
        return HttpResponseBadRequest("No subscription.")

    # If there is a Stripe subscription, clear cancel_at_period_end in Stripe.
    if getattr(sub, 'stripe_subscription_id', None):
        try:
            stripe.Subscription.modify(sub.stripe_subscription_id, cancel_at_period_end=False)
        except Exception as e:
            return HttpResponseBadRequest(str(e))

    # Local state: always clear cancel_at_period_end.
    sub.cancel_at_period_end = False

    # If local status got marked as canceled for UI purposes, restore it.
    now = timezone.now()
    if getattr(sub, 'trial_end', None) and now < sub.trial_end:
        sub.status = 'trialing'
    else:
        sub.status = 'active'
    sub.active = True

    try:
        sub.save(update_fields=['cancel_at_period_end', 'status', 'active'])
    except Exception:
        sub.save()

    # If this org was on a trial-cancel path, clear any scheduled account deletion.
    try:
        from accounts.models import Profile
        owner = getattr(org, 'owner', None)
        if owner:
            Profile.objects.filter(user=owner, scheduled_account_deletion_reason='trial_cancel_at_period_end').update(
                scheduled_account_deletion_at=None,
                scheduled_account_deletion_reason=None,
            )
    except Exception:
        pass

    is_trial = bool(getattr(sub, 'trial_end', None) and now < sub.trial_end)
    return JsonResponse({"status": "ok", "message": "Subscription reactivated.", "is_trial": is_trial})


@require_http_methods(["POST"])
def cancel_scheduled_change(request, org_slug):
    """Cancel a previously scheduled plan change (downgrade) for the org."""
    org = get_object_or_404(Organization, slug=org_slug)
    err = _require_org_owner_or_admin(request, org)
    if err:
        return err
    sub = getattr(org, 'subscription', None)
    if not sub:
        return HttpResponseBadRequest('No subscription.')
    # Clear scheduled change
    sub.scheduled_plan = None
    sub.scheduled_change_at = None
    sub.save()
    return JsonResponse({'status': 'ok'})


@require_http_methods(["GET", "POST"])
def preview_plan_change(request, org_slug, plan_id):
    """Return a proration/preview for changing to `plan_id`.

    Uses Stripe's `Invoice.upcoming` to simulate what the next invoice would
    look like if the subscription were changed to the provided plan. This is
    useful to show users the prorated immediate charge before committing.
    """
    org = get_object_or_404(Organization, slug=org_slug)
    err = _require_org_owner_or_admin(request, org)
    if err:
        return err

    plan = get_object_or_404(Plan, id=plan_id)
    sub = getattr(org, 'subscription', None)

    # If caller requested to start immediately (useful when converting a trial
    # to paid) then handle that scenario explicitly: return a preview that
    # charges the full plan price now and sets the next billing date one
    # billing period after the current date.
    start_immediately_flag = False
    try:
        start_immediately_flag = str(request.GET.get('start_immediately', '')).lower() in ('1', 'true', 'yes')
    except Exception:
        start_immediately_flag = False

    if start_immediately_flag and sub and getattr(sub, 'status', '').lower() == 'trialing':
        # Simulate immediate first-charge for the selected plan
        try:
            import calendar as _calendar
            from datetime import datetime, timedelta

            now = timezone.now()
            # Plan price is Decimal; convert to float for JSON serializable output
            plan_price = float(plan.price) if plan.price is not None else 0.0

            # Next billing date: add one billing period (monthly/yearly)
            if getattr(plan, 'billing_period', '') == 'yearly':
                month_offset = 12
            else:
                month_offset = 1

            # Add months safely
            month = now.month - 1 + month_offset
            year = now.year + month // 12
            month = month % 12 + 1
            day = min(now.day, _calendar.monthrange(year, month)[1])
            billing_dt = datetime(year, month, day, now.hour, now.minute, now.second, tzinfo=now.tzinfo)

            proration_lines = [{
                'description': f'{plan.name} (first period)',
                'amount': plan_price,
                'quantity': 1,
                'proration': False,
                'billing_reason': 'first_period'
            }]

            return JsonResponse({
                'amount_due_dollars': plan_price,
                'proration_amount_dollars': plan_price,
                'recurring_amount_dollars': 0.0,
                'billing_date_iso': billing_dt.isoformat(),
                'billing_date': billing_dt.strftime('%b %d, %Y').replace(' 0', ' '),
                'lines': proration_lines,
                'proration_lines': proration_lines,
                'recurring_lines': [],
                'raw': None,
            }, safe=False)
        except Exception as e:
            # If our local simulation fails, continue to fallback to Stripe preview
            pass

    # If no Stripe customer or price id, can't preview
    if not plan.stripe_price_id:
        return HttpResponseBadRequest('Plan missing Stripe price id.')

    if not org.stripe_customer_id and not (sub and not sub.stripe_subscription_id):
        # If customer missing and not in the special trial-without-stripe-sub case
        return HttpResponseBadRequest('No Stripe customer available. Add a card first.')

    try:
        proration_ts = None
        try:
            proration_ts = int(timezone.now().timestamp())
        except Exception:
            proration_ts = None

        # If there is an existing Stripe subscription, include it to let Stripe
        # compute prorations for a mid-period change. Otherwise (trial without
        # a Stripe subscription), request an upcoming invoice for the first
        # billing cycle by passing subscription_items only.
        if sub and sub.stripe_subscription_id and org.stripe_customer_id:
            # Retrieve the Stripe subscription to get the subscription item id
            try:
                stripe_sub = stripe.Subscription.retrieve(sub.stripe_subscription_id)
                # Choose the first subscription item to replace
                items = stripe_sub.get('items', {}).get('data', [])
                if items:
                    sub_item_id = items[0].get('id')
                    inv = stripe_invoice_upcoming(
                        customer=org.stripe_customer_id,
                        subscription=sub.stripe_subscription_id,
                        subscription_items=[{"id": sub_item_id, "price": plan.stripe_price_id}],
                        subscription_proration_date=proration_ts,
                        subscription_proration_behavior='create_prorations',
                    )
                else:
                    # Fallback: no items found, ask Stripe to compute based on new item
                    inv = stripe_invoice_upcoming(
                        customer=org.stripe_customer_id,
                        subscription=sub.stripe_subscription_id,
                        subscription_items=[{"price": plan.stripe_price_id}],
                        subscription_proration_date=proration_ts,
                        subscription_proration_behavior='create_prorations',
                    )
            except Exception:
                # If retrieval fails, fall back to the simpler upcoming call
                inv = stripe_invoice_upcoming(
                    customer=org.stripe_customer_id,
                    subscription=sub.stripe_subscription_id,
                    subscription_items=[{"price": plan.stripe_price_id}],
                    subscription_proration_date=proration_ts,
                    subscription_proration_behavior='create_prorations',
                )
        else:
            # No existing Stripe subscription: simulate first invoice
            inv = stripe_invoice_upcoming(
                customer=org.stripe_customer_id,
                subscription_items=[{"price": plan.stripe_price_id}],
            )


        amount_due = inv.get('amount_due', 0) / 100.0 if inv else 0.0
        period_end_ts = None
        if inv:
            period_end_ts = inv.get('period_end') or inv.get('created')

        def _is_proration_line(line):
            try:
                if bool(line.get('proration')):
                    return True
            except Exception:
                pass
            # Stripe Invoice.create_preview recommends using:
            #   line.parent.subscription_item_details.proration == true
            try:
                parent = line.get('parent') if isinstance(line, dict) else None
                sid = parent.get('subscription_item_details') if isinstance(parent, dict) else None
                if isinstance(sid, dict) and sid.get('proration') is True:
                    return True
            except Exception:
                pass
            return False

        # Build simplified lines and separate proration vs recurring lines so the
        # UI can clearly show immediate charges vs upcoming recurring charges.
        def _normalize_day_first_date_in_text(s: str) -> str:
            try:
                import re
                if not s:
                    return s
                month_map = {
                    'jan': 'Jan', 'feb': 'Feb', 'mar': 'Mar', 'apr': 'Apr', 'may': 'May', 'jun': 'Jun',
                    'jul': 'Jul', 'aug': 'Aug', 'sep': 'Sep', 'sept': 'Sep', 'oct': 'Oct', 'nov': 'Nov', 'dec': 'Dec',
                }
                pat = re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3,4})\s+(\d{4})\b")

                def repl(m):
                    day = str(int(m.group(1)))
                    mon_raw = (m.group(2) or '').strip().lower()
                    mon = month_map.get(mon_raw)
                    if not mon:
                        return m.group(0)
                    year = m.group(3)
                    return f"{mon} {day}, {year}"

                return pat.sub(repl, s)
            except Exception:
                return s
        lines = []
        proration_amount_cents = 0
        recurring_amount_cents = 0
        proration_lines = []
        recurring_lines = []
        if inv and inv.get('lines'):
            for line in inv['lines'].get('data', []):
                desc = line.get('description') or line.get('plan', {}).get('nickname') or line.get('id')
                desc = _normalize_day_first_date_in_text(desc)
                amt = int(line.get('amount', 0))
                entry = {
                    'description': desc,
                    'amount': (amt / 100.0),
                    'quantity': line.get('quantity'),
                    'proration': _is_proration_line(line),
                    'billing_reason': line.get('billing_reason'),
                }
                lines.append(entry)
                if entry['proration']:
                    proration_amount_cents += amt
                    proration_lines.append(entry)
                else:
                    recurring_amount_cents += amt
                    recurring_lines.append(entry)

        # Pending invoice items (separate field) are amounts Stripe will invoice
        # now but they may not be marked as 'proration' lines. Include them in
        # immediate charges.
        pending_cents = int(inv.get('pending_invoice_items_amount', 0)) if inv else 0

        proration_amount = (proration_amount_cents + pending_cents) / 100.0
        recurring_amount = recurring_amount_cents / 100.0

        from datetime import datetime
        billing_date = None
        if period_end_ts:
            try:
                billing_date = timezone.make_aware(datetime.fromtimestamp(period_end_ts))
            except Exception:
                billing_date = None

        return JsonResponse({
            'amount_due_dollars': amount_due,
            'proration_amount_dollars': proration_amount,
            'recurring_amount_dollars': recurring_amount,
            'billing_date_iso': billing_date.isoformat() if billing_date else None,
            'billing_date': (billing_date.strftime('%b %d, %Y').replace(' 0', ' ') if billing_date else None),
            'lines': lines,
            'proration_lines': proration_lines,
            'recurring_lines': recurring_lines,
            'raw': inv,
        }, safe=False)
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.exception('Failed to preview plan change')
        return HttpResponseBadRequest(str(e))


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
        # User is choosing a plan (scheduled or immediate). This implies they no longer
        # want to cancel at trial/period end.
        try:
            if getattr(sub, 'cancel_at_period_end', False):
                sub.cancel_at_period_end = False
        except Exception:
            pass

        # Clear any scheduled trial-end account deletion when user picks a plan.
        try:
            from accounts.models import Profile
            owner = getattr(org, 'owner', None)
            if owner:
                Profile.objects.filter(user=owner, scheduled_account_deletion_reason='trial_cancel_at_period_end').update(
                    scheduled_account_deletion_at=None,
                    scheduled_account_deletion_reason=None,
                )
        except Exception:
            pass

        if not org.stripe_customer_id:
            return HttpResponseBadRequest("No Stripe customer. Add payment method first.")

        # If user chose to wait until trial ends, only update the local plan
        # pointer and don't create a Stripe subscription yet.
        if not start_immediately:
            # Do not change or schedule a plan while the user is still on a free
            # trial. A plan should only change after a paid Stripe subscription
            # exists.
            sub.scheduled_plan = None
            sub.scheduled_change_at = None
            try:
                sub.save(update_fields=['scheduled_plan', 'scheduled_change_at', 'cancel_at_period_end'])
            except Exception:
                sub.save()
            return JsonResponse({"status": "ok", "message": "No changes made."})

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

            # Apply any Stripe-linked DiscountCode for this user (promo code or coupon).
            discounts = None
            try:
                from billing.models import DiscountCode

                dc = (
                    DiscountCode.objects.filter(users=request.user, active=True)
                    .order_by('-created_at')
                    .first()
                )
                if dc and dc.is_valid():
                    promo_id = getattr(dc, 'stripe_promotion_code_id', None)
                    coupon_id = getattr(dc, 'stripe_coupon_id', None)
                    if promo_id:
                        discounts = [{"promotion_code": str(promo_id)}]
                    elif coupon_id:
                        discounts = [{"coupon": str(coupon_id)}]
            except Exception:
                discounts = None

            # Create new Stripe subscription
            sub_create_kwargs = {
                'customer': org.stripe_customer_id,
                'items': [{"price": new_plan.stripe_price_id}],
                'default_payment_method': pm_id,
                'metadata': {"organization_id": str(org.id), "plan_id": str(plan_id)},
            }
            if discounts:
                sub_create_kwargs['discounts'] = discounts
            try:
                stripe_sub = stripe.Subscription.create(**sub_create_kwargs)
            except Exception as e:
                # Fallback for older Stripe API behavior where `coupon=` is accepted
                # but `discounts=[{coupon: ...}]` may be rejected.
                if discounts and isinstance(discounts, list) and discounts and isinstance(discounts[0], dict) and discounts[0].get('coupon'):
                    sub_create_kwargs.pop('discounts', None)
                    sub_create_kwargs['coupon'] = discounts[0].get('coupon')
                    stripe_sub = stripe.Subscription.create(**sub_create_kwargs)
                else:
                    raise
            # Update local subscription
            sub.stripe_subscription_id = stripe_sub.id
            sub.plan = new_plan
            sub.status = stripe_sub.status
            sub.active = (stripe_sub.status == "active")
            sub.cancel_at_period_end = False
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

    # User is changing plans; ensure the subscription is not set to cancel-at-period-end.
    # Important: don't trust local state here. If Stripe still has cancel_at_period_end=true
    # (e.g., local DB missed a webhook update), subsequent Stripe webhooks can reapply the
    # canceling flag after the plan change, causing the UI to stay in "reactivate" mode.
    try:
        try:
            stripe.Subscription.modify(sub.stripe_subscription_id, cancel_at_period_end=False)
        except Exception:
            # Best-effort; still clear locally so UI reflects intent.
            pass
        if getattr(sub, 'cancel_at_period_end', False):
            sub.cancel_at_period_end = False
            try:
                sub.save(update_fields=['cancel_at_period_end'])
            except Exception:
                sub.save()
    except Exception:
        pass

    # Clear any scheduled trial-end account deletion when user resumes billing.
    try:
        from accounts.models import Profile
        owner = getattr(org, 'owner', None)
        if owner:
            Profile.objects.filter(user=owner, scheduled_account_deletion_reason='trial_cancel_at_period_end').update(
                scheduled_account_deletion_at=None,
                scheduled_account_deletion_reason=None,
            )
    except Exception:
        pass
    # Prefer using Stripe's live subscription price to determine upgrade vs downgrade
    current_price = None
    stripe_sub_obj = None
    try:
        if sub and sub.stripe_subscription_id:
            stripe_sub_obj = stripe.Subscription.retrieve(sub.stripe_subscription_id, expand=['items.data.price'])
            items = stripe_sub_obj.get('items', {}).get('data', [])
            if items and items[0].get('price'):
                price_obj = items[0].get('price')
                # unit_amount is in cents
                if price_obj.get('unit_amount') is not None:
                    current_price = float(price_obj.get('unit_amount')) / 100.0
    except Exception:
        # Fall back to local DB price when Stripe call fails
        try:
            current_price = float(sub.plan.price) if sub.plan and sub.plan.price is not None else None
        except Exception:
            current_price = None

    # If user requested to wait and the subscription is currently trialing,
    # do nothing. The UI's Cancel should not schedule anything.
    if not start_immediately and getattr(sub, 'status', '') == 'trialing':
        sub.scheduled_plan = None
        sub.scheduled_change_at = None
        try:
            sub.save(update_fields=['scheduled_plan', 'scheduled_change_at', 'cancel_at_period_end'])
        except Exception:
            sub.save()
        return JsonResponse({"status": "ok", "message": "No changes made."})

    # Decide upgrade vs downgrade based on Stripe price (preferred) or local DB price
    is_upgrade = None
    try:
        if current_price is not None:
            is_upgrade = float(new_plan.price) > current_price
    except Exception:
        is_upgrade = None

    # If we can determine it's a downgrade, schedule it at period end (no refunds).
    if is_upgrade is False:
        sub.scheduled_plan = new_plan
        sub.scheduled_change_at = sub.current_period_end or None
        sub.save()
        # Record a local SubscriptionChange so the user can see the scheduled change
        try:
            from billing.models import SubscriptionChange
            SubscriptionChange.objects.create(
                organization=org,
                subscription=sub,
                change_type='downgrade',
                new_plan=new_plan,
                effective_at=sub.scheduled_change_at,
                amount_cents=0,
                status='scheduled',
            )
        except Exception:
            pass
        return JsonResponse({"status": "ok", "message": "Plan downgrade scheduled to take effect at period end."})

    # Upgrades (or unknown): apply immediately and create prorations
    try:
        import logging
        logger = logging.getLogger(__name__)

        # Ensure customer and default payment method exist for immediate upgrades
        if not org.stripe_customer_id:
            return HttpResponseBadRequest("No Stripe customer. Add payment method first.")

        # If Stripe subscription exists but is canceled, handle by creating
        # a new Stripe subscription for the customer (so upgrade can be immediate).
        if stripe_sub_obj and stripe_sub_obj.get('status') == 'canceled':
            # For upgrades on a canceled subscription, create a fresh subscription
            # using the customer's default payment method (or the first card).
            cust = stripe.Customer.retrieve(org.stripe_customer_id)
            default_pm = cust.get('invoice_settings', {}).get('default_payment_method')
            if not default_pm:
                # Try to find any attached payment method
                pms = stripe.PaymentMethod.list(customer=org.stripe_customer_id, type='card')
                if not pms or not pms.get('data'):
                    return HttpResponseBadRequest('No payment method found. Please add a card before upgrading.')
                default_pm = pms.get('data')[0].get('id')

            # Create new subscription for the Team (new_plan)
            new_sub = stripe.Subscription.create(
                customer=org.stripe_customer_id,
                items=[{"price": new_plan.stripe_price_id}],
                default_payment_method=default_pm,
                expand=["latest_invoice.payment_intent"],
                metadata={"organization_id": str(org.id), "plan_id": str(new_plan.id), "recreated_from": sub.stripe_subscription_id},
            )
            logger.info(f"Recreated subscription {new_sub.id} for org {org.slug} due to canceled subscription {sub.stripe_subscription_id}")

            # Update local subscription
            sub.stripe_subscription_id = new_sub.id
            sub.plan = new_plan
            sub.status = new_sub.get('status')
            sub.active = (sub.status == 'active' or sub.status == 'trialing')
            sub.cancel_at_period_end = False
            if new_sub.get('current_period_end'):
                from datetime import datetime
                sub.current_period_end = timezone.make_aware(datetime.fromtimestamp(new_sub['current_period_end']))
            sub.scheduled_plan = None
            sub.scheduled_change_at = None
            sub.save()

            # If the org is now on Team, auto-assign any legacy unassigned services
            # to the owner membership. This makes Pro->Team upgrades behave as expected.
            try:
                from billing.utils import TEAM_SLUG
                if getattr(new_plan, 'slug', None) == TEAM_SLUG:
                    from accounts.models import Membership
                    from bookings.models import ServiceAssignment
                    from bookings.models import Service
                    owner_mem = Membership.objects.filter(organization=org, role='owner', is_active=True).first()
                    if owner_mem is None:
                        owner_mem = Membership.objects.filter(organization=org, user=getattr(org, 'owner', None), is_active=True).first()
                    if owner_mem is not None:
                        for svc in Service.objects.filter(organization=org):
                            if not ServiceAssignment.objects.filter(service=svc).exists():
                                ServiceAssignment.objects.get_or_create(service=svc, membership=owner_mem)
            except Exception:
                pass

            # Record the immediate change so users see it in the UI
            try:
                from billing.models import SubscriptionChange
                SubscriptionChange.objects.create(
                    organization=org,
                    subscription=sub,
                    change_type='upgrade',
                    new_plan=new_plan,
                    effective_at=sub.current_period_end,
                    amount_cents=0,
                    status='processed',
                )
            except Exception:
                pass

            # Log the preview/new subscription for auditing
            try:
                logger.info('Preview JSON (not available for canceled->new flow)')
                logger.info(f'Created subscription {new_sub.id} status={new_sub.get("status")}')
            except Exception:
                pass

            return JsonResponse({"status": "ok", "message": "Subscription recreated and upgraded immediately."})

        # Otherwise, proceed with the usual immediate-upgrade flow for active subs
        # Preview the upcoming invoice to obtain proration lines and amounts
        try:
            proration_ts = None
            try:
                proration_ts = int(timezone.now().timestamp())
            except Exception:
                proration_ts = None

            sub_item_id = stripe_sub_obj.get('items', {}).get('data', [])[0].get('id') if stripe_sub_obj and stripe_sub_obj.get('items', {}).get('data') else None
            preview = stripe_invoice_upcoming(
                customer=org.stripe_customer_id,
                subscription=sub.stripe_subscription_id,
                subscription_items=[{"id": sub_item_id, "price": new_plan.stripe_price_id}] if sub_item_id else [{"price": new_plan.stripe_price_id}],
                subscription_proration_date=proration_ts,
                subscription_proration_behavior='create_prorations',
            )
            logger.info(f"Preview for change: org={org.slug} new_plan={new_plan.id} preview_lines={len(preview.get('lines', {}).get('data', []))}")
        except Exception as e:
            preview = None
            logger.exception('Failed to preview upcoming invoice')

        proration_lines = []
        if preview and preview.get('lines'):
            for line in preview['lines'].get('data', []):
                is_proration = False
                try:
                    if line.get('proration'):
                        is_proration = True
                except Exception:
                    is_proration = False
                if not is_proration:
                    try:
                        parent = line.get('parent') if isinstance(line, dict) else None
                        sid = parent.get('subscription_item_details') if isinstance(parent, dict) else None
                        if isinstance(sid, dict) and sid.get('proration') is True:
                            is_proration = True
                    except Exception:
                        is_proration = False

                if is_proration or line.get('billing_reason') == 'pending_invoice_item':
                    proration_lines.append(line)

        # If Stripe preview returned no proration lines (or zero amount),
        # fall back to a server-side proration calculation to ensure the
        # customer is charged immediately for the prorated portion of the
        # upgrade. This avoids situations where Stripe's upcoming invoice
        # contains only the future recurring amount and nothing to invoice now.
        total_proration_cents = 0
        for l in proration_lines:
            total_proration_cents += int(l.get('amount', 0) or 0)

        if total_proration_cents == 0:
            try:
                # Attempt to compute prorated difference between new and current price
                now_ts = int(timezone.now().timestamp())
                # Prefer Stripe subscription timestamps when available
                period_start = None
                period_end = None
                if stripe_sub_obj:
                    period_start = int(stripe_sub_obj.get('current_period_start')) if stripe_sub_obj.get('current_period_start') else None
                    period_end = int(stripe_sub_obj.get('current_period_end')) if stripe_sub_obj.get('current_period_end') else None
                # Fallback to local subscription current_period_end if Stripe values missing
                if not period_end and sub and getattr(sub, 'current_period_end', None):
                    try:
                        period_end = int(sub.current_period_end.timestamp())
                    except Exception:
                        period_end = None

                if period_start and period_end and period_end > now_ts and period_end > period_start:
                    remaining = period_end - now_ts
                    total_period = period_end - period_start
                    ratio = float(remaining) / float(total_period)
                    # Determine price difference in cents
                    new_price_cents = int(round(float(new_plan.price) * 100))
                    cur_price_cents = None
                    # Use Stripe current_price when available
                    if current_price is not None:
                        cur_price_cents = int(round(float(current_price) * 100))
                    elif sub and sub.plan and sub.plan.price is not None:
                        cur_price_cents = int(round(float(sub.plan.price) * 100))
                    if cur_price_cents is None:
                        raise ValueError('Cannot determine current price')
                    diff_cents = new_price_cents - cur_price_cents
                    if diff_cents > 0:
                        prorate_cents = int(round(diff_cents * ratio))
                        if prorate_cents > 0:
                            # Build a minimal proration line structure compatible with downstream code
                            proration_lines = [{
                                'description': f'Prorated upgrade to {new_plan.name}',
                                'amount': prorate_cents,
                                'quantity': 1,
                                'proration': True,
                                'billing_reason': 'proration_fallback'
                            }]
                            total_proration_cents = prorate_cents
                # else: leave proration_lines empty and handle below
            except Exception:
                # If anything goes wrong, fall back to no proration (will result in $0 now)
                total_proration_cents = 0

        # Apply the subscription change but disable automatic prorations so we
        # can invoice only the prorations we want now.
        # IMPORTANT: Replace the existing subscription item when possible.
        # Passing only {price: ...} can add a second item instead of replacing.
        update_items = [{"price": new_plan.stripe_price_id}]
        try:
            existing_item_id = stripe_sub_obj.get('items', {}).get('data', [])[0].get('id') if stripe_sub_obj and stripe_sub_obj.get('items', {}).get('data') else None
            if existing_item_id:
                update_items = [{"id": existing_item_id, "price": new_plan.stripe_price_id}]
        except Exception:
            pass

        stripe_sub = stripe.Subscription.modify(
            sub.stripe_subscription_id,
            items=update_items,
            proration_behavior="none",
        )

        # Create invoice items for each proration line (Stripe preview gives
        # amounts in cents). This mirrors the prorations but ensures we only
        # charge those amounts now, not the full recurring amount.
        created_invoice = None
        try:
            currency = (preview.get('currency') if preview else 'usd') or 'usd'
            for line in proration_lines:
                amt = int(line.get('amount', 0))
                desc = line.get('description') or (line.get('plan', {}) or {}).get('nickname') or 'Proration'
                # Attach invoice items to the subscription so the resulting
                # invoice is associated with the correct subscription id.
                stripe.InvoiceItem.create(
                    customer=org.stripe_customer_id,
                    subscription=sub.stripe_subscription_id,
                    amount=amt,
                    currency=currency,
                    description=desc,
                )

            # Create and finalize invoice to bill the created invoice items now
            created_invoice = stripe.Invoice.create(customer=org.stripe_customer_id, subscription=sub.stripe_subscription_id)
            stripe.Invoice.finalize_invoice(created_invoice.id)
            try:
                stripe.Invoice.pay(created_invoice.id)
            except Exception:
                pass

            invoice_info = None
            if created_invoice:
                # Retrieve the finalized invoice with expanded payment details so
                # we can surface the charged amount and payment method/card used.
                try:
                    inv = stripe.Invoice.retrieve(created_invoice.id, expand=['payment_intent', 'charge', 'latest_charge'])
                except Exception:
                    inv = stripe.Invoice.retrieve(created_invoice.id)

                invoice_info = {
                    'id': inv.id,
                    'amount_due': inv.get('amount_due', 0),
                    'amount_paid': inv.get('amount_paid', 0),
                    'currency': inv.get('currency'),
                    'status': inv.get('status'),
                    'hosted_invoice_url': inv.get('hosted_invoice_url'),
                }

                # Attempt to extract card/PM details from payment_intent or charge
                pm_details = None
                try:
                    pi = inv.get('payment_intent') or (inv.get('latest_charge') and {}).get('payment_intent')
                    if pi:
                        # If expanded above, pi is an object; otherwise retrieve
                        if isinstance(pi, dict) and pi.get('id') and pi.get('charges'):
                            ch = (pi.get('charges', {}).get('data') or [None])[0]
                        else:
                            pi_obj = stripe.PaymentIntent.retrieve(pi) if isinstance(pi, str) else pi
                            ch = (pi_obj.get('charges', {}).get('data') or [None])[0]
                        if ch:
                            pm = ch.get('payment_method_details', {}).get('card')
                            if pm:
                                pm_details = {'brand': pm.get('brand'), 'last4': pm.get('last4')}
                except Exception:
                    pm_details = None

                if pm_details:
                    invoice_info['card_brand'] = pm_details.get('brand')
                    invoice_info['card_last4'] = pm_details.get('last4')

                logger.info(f"Created invoice {created_invoice.id} for proration for org={org.slug}")
        except Exception:
            created_invoice = None
            logger.exception('Failed while creating/charging proration invoice items')

        # Update local subscription record from Stripe response
        sub.plan = new_plan
        sub.status = stripe_sub.get('status', sub.status)
        sub.active = (sub.status == 'active' or sub.status == 'trialing')
        if stripe_sub.get('current_period_end'):
            from datetime import datetime
            sub.current_period_end = timezone.make_aware(datetime.fromtimestamp(stripe_sub['current_period_end']))
        sub.scheduled_plan = None
        sub.scheduled_change_at = None
        sub.save()

        # If the org is now on Team, auto-assign any legacy unassigned services
        # to the owner membership. This runs once at upgrade time.
        try:
            from billing.utils import TEAM_SLUG
            if getattr(new_plan, 'slug', None) == TEAM_SLUG:
                from accounts.models import Membership
                from bookings.models import ServiceAssignment
                from bookings.models import Service
                owner_mem = Membership.objects.filter(organization=org, role='owner', is_active=True).first()
                if owner_mem is None:
                    owner_mem = Membership.objects.filter(organization=org, user=getattr(org, 'owner', None), is_active=True).first()
                if owner_mem is not None:
                    for svc in Service.objects.filter(organization=org):
                        if not ServiceAssignment.objects.filter(service=svc).exists():
                            ServiceAssignment.objects.get_or_create(service=svc, membership=owner_mem)
        except Exception:
            pass
        # Create a SubscriptionChange record reflecting this processed upgrade
        try:
            from billing.models import SubscriptionChange
            amt = None
            try:
                if invoice_info and invoice_info.get('amount_paid') is not None:
                    amt = int(invoice_info.get('amount_paid') or 0)
            except Exception:
                amt = None
            SubscriptionChange.objects.create(
                organization=org,
                subscription=sub,
                change_type='upgrade',
                new_plan=new_plan,
                effective_at=sub.current_period_end,
                amount_cents=(amt if amt is not None else int(total_proration_cents or 0)),
                status='processed',
                card_brand=(invoice_info.get('card_brand') if invoice_info else None),
                card_last4=(invoice_info.get('card_last4') if invoice_info else None),
                stripe_invoice_id=(invoice_info.get('id') if invoice_info else None),
            )
        except Exception:
            pass
        # If we created an invoice, include its info in the response so the
        # client can display the charged amount and card used.
        if created_invoice and invoice_info:
            return JsonResponse({"status": "ok", "message": "Plan changed and prorated charges applied.", "invoice": invoice_info})
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.exception('Failed to change subscription plan')
        return HttpResponseBadRequest(str(e))

    return JsonResponse({"status": "ok", "message": "Plan changed and prorated charges applied."})
