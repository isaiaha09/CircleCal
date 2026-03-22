# calendar_app/middleware.py
import logging
import os
import sys
from django.shortcuts import redirect
from django.http import HttpResponseBadRequest, HttpResponsePermanentRedirect
from django.db import connection, DatabaseError
from django.utils import timezone
from zoneinfo import ZoneInfo
from accounts.models import Business as Organization, Membership
from .utils import user_has_role
from billing.models import Subscription, Plan
from django.conf import settings
from django.contrib import messages
from django.core.cache import cache


logger = logging.getLogger(__name__)


class AppModeMiddleware:
    """Persist a marker that this request chain belongs to the native app.

    The in-app WebView sends a CircleCalApp-* user-agent, but OS-controlled
    auth-session browsers (used for signup/login/Stripe) do not. We therefore
    latch `cc_app_flow` on the Django session as soon as we see `cc_app=1`.

    This allows downstream endpoints (e.g. Stripe Connect start) to reliably
    identify app-originated flows even when query params are later dropped.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        cc_app_param = False
        try:
            cc_app_param = (request.GET.get('cc_app') == '1')
        except Exception:
            cc_app_param = False

        if cc_app_param:
            try:
                request.session['cc_app_flow'] = True
            except Exception:
                pass

        response = self.get_response(request)

        # Best-effort: set a session cookie so app-mode is easier to detect
        # within this browser context even if internal links drop cc_app=1.
        if cc_app_param:
            try:
                if (request.COOKIES.get('cc_app') or '') != '1':
                    response.set_cookie(
                        'cc_app',
                        '1',
                        path='/',
                        secure=bool(request.is_secure()),
                        samesite='Lax',
                    )
            except Exception:
                pass

        return response


class AdminUndoContextMiddleware:
    """Enable admin undo snapshot capture only for admin requests.

    We keep this lightweight: it only toggles a threadlocal flag consumed by
    calendar_app.admin_undo signal handlers.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.conf import settings
        try:
            from .admin_undo import set_request_context
        except Exception:
            set_request_context = None

        admin_prefix = f"/{getattr(settings, 'ADMIN_PATH', 'admin')}/"
        enabled = False
        try:
            enabled = bool(request.path.startswith(admin_prefix)) and bool(getattr(request, 'user', None)) and request.user.is_authenticated and request.user.is_staff
        except Exception:
            enabled = False

        if set_request_context:
            try:
                set_request_context(request, enabled=enabled)
            except Exception:
                pass

        response = self.get_response(request)

        if set_request_context:
            try:
                set_request_context(request, enabled=False)
            except Exception:
                pass

        return response


class CustomDomainMiddleware:
    """Support verified per-business booking subdomains (e.g. booking.example.com).

    This middleware:
    - Detects whether the request host matches a verified Business.custom_domain
    - Redirects '/' on that host to the org's public booking page
    - Optionally validates hosts when ALLOWED_HOSTS is permissive (production)
    """

    def __init__(self, get_response):
        self.get_response = get_response

    @staticmethod
    def _raw_host_without_port(request):
        """Return the HTTP host without consulting Django's ALLOWED_HOSTS.

        We intentionally avoid request.get_host() here because it raises
        DisallowedHost before we can auto-allow verified booking subdomains.
        """
        try:
            raw = (request.META.get('HTTP_HOST') or request.META.get('SERVER_NAME') or '').strip()
        except Exception:
            raw = ''
        if not raw:
            return ''
        return raw.split(':', 1)[0].lower()

    @staticmethod
    def _ensure_allowed_host(host: str):
        """Best-effort: add host to settings.ALLOWED_HOSTS if needed."""
        if not host:
            return
        try:
            allowed = getattr(settings, 'ALLOWED_HOSTS', [])
            if '*' in (allowed or []):
                return
            # Normalize to a mutable list
            if isinstance(allowed, tuple):
                allowed = list(allowed)
            if not isinstance(allowed, list):
                return
            if host not in [h.lower() for h in allowed]:
                allowed.append(host)
                settings.ALLOWED_HOSTS = allowed
        except Exception:
            return

    def __call__(self, request):
        request.custom_domain_organization = None

        host = self._raw_host_without_port(request)
        if not host:
            return self.get_response(request)

        # Django test client uses 'testserver'
        if host in {'testserver'}:
            return self.get_response(request)

        org = None
        try:
            org = Organization.objects.filter(custom_domain__iexact=host, custom_domain_verified=True).first()
        except Exception:
            org = None

        if org:
            request.custom_domain_organization = org

            # Auto-allow this verified host so downstream middleware that calls
            # request.get_host() doesn't raise DisallowedHost.
            self._ensure_allowed_host(host)

            # Branded root: https://booking.example.com/ => /bus/<slug>/
            try:
                if (request.path or '/') == '/':
                    from django.urls import reverse
                    return redirect(reverse('bookings:public_org_page', args=[org.slug]))
            except Exception:
                pass

        return self.get_response(request)


class HostedSubdomainMiddleware:
    """Support CircleCal-hosted subdomains (e.g. <orgslug>.<base_domain>).

    This is intended to scale via wildcard DNS/cert and is distinct from
    customer-owned booking subdomains.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    @staticmethod
    def _raw_host_without_port(request):
        try:
            raw = (request.META.get('HTTP_HOST') or request.META.get('SERVER_NAME') or '').strip()
        except Exception:
            raw = ''
        if not raw:
            return ''
        return raw.split(':', 1)[0].lower()

    @staticmethod
    def _ensure_allowed_host_suffix(base_domain: str):
        """Best-effort: add .<base_domain> to settings.ALLOWED_HOSTS if needed."""
        base_domain = (base_domain or '').strip().lower().lstrip('.')
        if not base_domain:
            return
        suffix = f'.{base_domain}'
        try:
            allowed = getattr(settings, 'ALLOWED_HOSTS', [])
            if '*' in (allowed or []):
                return
            if isinstance(allowed, tuple):
                allowed = list(allowed)
            if not isinstance(allowed, list):
                return
            allowed_lc = [str(h).lower() for h in allowed]
            if suffix not in allowed_lc:
                allowed.append(suffix)
                settings.ALLOWED_HOSTS = allowed
        except Exception:
            return

    def __call__(self, request):
        request.hosted_subdomain_organization = None

        base = (getattr(settings, 'HOSTED_SUBDOMAIN_BASE', '') or os.getenv('HOSTED_SUBDOMAIN_BASE') or '').strip().lower().lstrip('.')
        if not base:
            return self.get_response(request)

        host = self._raw_host_without_port(request)
        if not host:
            return self.get_response(request)

        if host in {'testserver', 'localhost', '127.0.0.1', '[::1]'}:
            return self.get_response(request)

        suffix = f'.{base}'
        if not host.endswith(suffix):
            return self.get_response(request)

        sub_label = host[: -len(suffix)].strip('.').lower()
        # Only single-label subdomains are supported for wildcard certs.
        if not sub_label or '.' in sub_label:
            return self.get_response(request)

        # Avoid collisions with common reserved hostnames.
        if sub_label in {'www', 'api', 'static', 'media', 'admin'}:
            return self.get_response(request)

        org = None
        try:
            org = Organization.objects.filter(slug__iexact=sub_label).first()
        except Exception:
            org = None

        if not org:
            return self.get_response(request)

        # Only orgs with Booking Flow Bundle should get hosted-subdomain experience.
        try:
            from billing.utils import can_use_hosted_subdomain
            eligible = bool(can_use_hosted_subdomain(org))
        except Exception:
            eligible = False

        if not eligible:
            # If a non-eligible org is hit by subdomain, send users to canonical.
            try:
                if (request.path or '/') == '/' and request.method in ('GET', 'HEAD'):
                    canonical = (getattr(settings, 'CANONICAL_HOST', '') or '').strip().lower()
                    if canonical:
                        from django.urls import reverse
                        return HttpResponsePermanentRedirect(f'https://{canonical}{reverse("bookings:public_org_page", args=[org.slug])}')
            except Exception:
                pass
            return self.get_response(request)

        request.hosted_subdomain_organization = org
        self._ensure_allowed_host_suffix(base)

        try:
            if (request.path or '/') == '/':
                from django.urls import reverse
                return redirect(reverse('bookings:public_org_page', args=[org.slug]))
        except Exception:
            pass

        return self.get_response(request)


class CanonicalHostRedirectMiddleware:
    """Redirect requests to the canonical host (e.g. force circlecal.app).

    This is optional and controlled via env vars (in production settings):
      - CANONICAL_HOST (e.g. circlecal.app or www.circlecal.app)
      - CANONICAL_HOST_REDIRECT=1

    It intentionally runs AFTER CustomDomainMiddleware so verified booking subdomains
    are not redirected away.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    @staticmethod
    def _raw_host_without_port(request):
        try:
            raw = (request.META.get('HTTP_HOST') or request.META.get('SERVER_NAME') or '').strip()
        except Exception:
            raw = ''
        if not raw:
            return ''
        return raw.split(':', 1)[0].lower()

    def __call__(self, request):
        canonical_host = (getattr(settings, 'CANONICAL_HOST', '') or '').strip().lower()
        enabled = bool(getattr(settings, 'CANONICAL_HOST_REDIRECT', False))

        if not enabled or not canonical_host:
            return self.get_response(request)

        # Do not interfere with verified booking subdomains.
        if getattr(request, 'custom_domain_organization', None) is not None:
            return self.get_response(request)

        # Do not interfere with hosted subdomains.
        if getattr(request, 'hosted_subdomain_organization', None) is not None:
            return self.get_response(request)

        host = self._raw_host_without_port(request)
        if not host or host in {'testserver', 'localhost', '127.0.0.1', '[::1]'}:
            return self.get_response(request)

        # Avoid redirecting unsafe methods (prevents losing POST bodies).
        if request.method not in ('GET', 'HEAD'):
            return self.get_response(request)

        if host == canonical_host:
            return self.get_response(request)

        # Only redirect when the current host is one of our canonical/allowed hosts;
        # this avoids unexpected behavior on arbitrary Host headers.
        try:
            allowed = getattr(settings, 'ALLOWED_HOSTS', []) or []
            allowed_lc = {str(h).lower() for h in allowed}
        except Exception:
            allowed_lc = set()

        if allowed_lc and host not in allowed_lc:
            return self.get_response(request)

        scheme = 'https'
        path = request.get_full_path()
        return HttpResponsePermanentRedirect(f'{scheme}://{canonical_host}{path}')

class OrganizationMiddleware:
    """
    Resolve the organization for the current request.
    """

    def __init__(self, get_response):
        self.get_response = get_response


    def process_template_response(self, request, response):
        if hasattr(request, "organization"):
            if hasattr(response, "context_data"):
                # DRF Response objects can have context_data=None; don't crash API endpoints.
                if response.context_data is None:
                    response.context_data = {}
                if isinstance(response.context_data, dict):
                    response.context_data["organization"] = request.organization
        return response


    def __call__(self, request):

        # 1. Default: no org yet
        request.organization = None

        # 2. Detect /bus/<slug>/ in the URL
        path_parts = request.path.strip('/').split('/')
        if len(path_parts) >= 2 and path_parts[0] == 'bus':
            slug = path_parts[1]
            try:
                org = Organization.objects.get(slug=slug)
                request.organization = org
            except Organization.DoesNotExist:
                request.organization = None

        # 3. Fallback: user's first organization
        else:
            if request.user.is_authenticated:
                membership = request.user.memberships.select_related('organization').first()
                if membership:
                    request.organization = membership.organization

        # 4. ✅ ATTACH user_has_role TO THE REQUEST OBJECT HERE
        request.user_has_role = lambda roles, org=request.organization: user_has_role(
            request.user,
            org,
            roles if isinstance(roles, (list, tuple)) else [roles]
        )

        # 5. Continue processing
        # During pytest runs we skip UX-gating redirects (Stripe connect / profile completion)
        # so integration tests can validate endpoint behavior (200/400/etc) without being
        # converted into 302 redirects.
        is_test_run = bool(os.environ.get('PYTEST_CURRENT_TEST')) or ('test' in sys.argv)

        # 5a. Enforce profile completion (First/Last) before allowing navigation away
        # from Profile. Client-side JS should block clicks, but this makes it non-bypassable.
        try:
            if (not is_test_run) and request.user.is_authenticated:
                path = request.path or ''
                admin_prefix = '/' + (getattr(settings, 'ADMIN_PATH', 'admin') or 'admin').strip('/')
                is_admin_path = path.startswith(admin_prefix)
                is_admin_user = bool(getattr(request.user, 'is_staff', False)) or bool(getattr(request.user, 'is_superuser', False))

                # Do not block Django admin users/pages.
                if not (is_admin_path or is_admin_user):
                    first = (getattr(request.user, 'first_name', '') or '').strip()
                    last = (getattr(request.user, 'last_name', '') or '').strip()
                    if not (first and last):
                        # Only enforce this completion gate for staff/manager accounts.
                        # Owners/admins should be allowed to proceed to the dashboard.
                        is_staff_or_manager = False
                        try:
                            if getattr(request, 'user_has_role', None):
                                is_staff_or_manager = bool(request.user_has_role(['staff', 'manager']))
                        except Exception:
                            is_staff_or_manager = False

                        if is_staff_or_manager:
                            # If the user has not created/joined any business yet, keep them
                            # in the business-setup flow instead of forcing Profile.
                            try:
                                from accounts.models import Membership
                                has_any_org = Membership.objects.filter(user=request.user, is_active=True).exists()
                            except Exception:
                                has_any_org = True

                            allow_paths = [
                                '/accounts/profile/',
                                '/accounts/profile',
                                '/accounts/two_factor/',
                                '/accounts/two_factor',
                                '/accounts/password/change/',
                                '/accounts/password/change',
                                '/accounts/deactivate/',
                                '/accounts/deactivate',
                                '/accounts/delete/',
                                '/accounts/delete',
                                '/accounts/logout/',
                                '/accounts/logout',
                                '/post-login/',
                                '/post-login',
                                '/choose-business/',
                                '/choose-business',
                                '/create-business/',
                                '/create-business',
                                '/static/',
                                settings.MEDIA_URL,
                            ]
                            if not any(path.startswith(ap) for ap in allow_paths):
                                from django.urls import reverse
                                if not has_any_org:
                                    return redirect(reverse('calendar_app:choose_business'))
                                return redirect(reverse('accounts:profile'))
        except Exception:
            pass

        response = self.get_response(request)

        # 6. Trial/Subscription enforcement: allow trial without card, require payment after trial
        org = getattr(request, "organization", None)
        if request.user.is_authenticated and org:
            try:
                sub = org.subscription
            except Subscription.DoesNotExist:
                sub = None

            # Auto-provision trial subscription if missing
            if not sub:
                basic_plan = Plan.objects.filter(slug="basic").first()
                trial_days = 31
                start_dt = timezone.now()
                trial_end = start_dt + timezone.timedelta(days=trial_days)
                try:
                    sub = Subscription.objects.create(
                        organization=org,
                        plan=basic_plan,
                        status="trialing",
                        active=False,
                        start_date=start_dt,
                        trial_end=trial_end,
                    )
                except DatabaseError:
                    # Best-effort only: this middleware runs after downstream
                    # middlewares have already handled the request, so a failed
                    # backfill must not turn a normal page load into a 500.
                    logger.warning(
                        "Skipping subscription auto-provision for org_id=%s on path=%s because the database rejected the insert.",
                        getattr(org, 'id', None),
                        getattr(request, 'path', ''),
                        exc_info=True,
                    )
                    sub = None

            # If trial expired and not active, redirect to pricing unless already there
            # Also show a one-time post-login message when applicable.
            try:
                post_login_check = bool(request.session.pop('cc_post_login_check_trial', False))
            except Exception:
                post_login_check = False
            if post_login_check and sub and sub.status == 'trialing' and sub.trial_end:
                try:
                    remaining = sub.trial_end - timezone.now()
                    remaining_days = int(remaining.total_seconds() // 86400)
                    if remaining.total_seconds() <= 0:
                        messages.error(request, 'Your trial has ended. Please choose a plan to continue using CircleCal.')
                    elif remaining_days <= 3:
                        messages.warning(request, f'Your trial ends soon ({max(remaining_days, 0)} day(s) remaining). Choose a plan to keep your business active.')
                except Exception:
                    pass

            if sub and sub.status == "trialing" and sub.trial_end and timezone.now() >= sub.trial_end:
                # Allow pricing, embedded checkout, and auth pages
                allow_paths = [
                    f"/bus/{org.slug}/pricing/",
                    f"/bus/{org.slug}/billing-unavailable/",
                    f"/billing/org/{org.slug}/embedded/",
                    f"/billing/api/bus/{org.slug}/embedded/",
                    "/accounts/login/",
                    "/accounts/login",
                    "/accounts/signup/",
                    "/accounts/signup",
                    "/accounts/profile/",
                    "/accounts/profile",
                    "/accounts/two_factor/",
                    "/accounts/two_factor",
                    "/accounts/password/change/",
                    "/accounts/password/change",
                    "/accounts/password/change/done/",
                    "/accounts/password/change/done",
                    "/accounts/logout/",
                    "/accounts/logout",
                ]
                path = request.path
                if not any(path.startswith(ap) for ap in allow_paths):
                    from django.urls import reverse
                    try:
                        ua = (request.META.get('HTTP_USER_AGENT') or '')
                        is_app_ua = 'circlecalapp' in ua.lower()
                    except Exception:
                        is_app_ua = False

                    if is_app_ua:
                        messages.error(request, 'Your trial has ended. Pricing and billing are not available in the mobile app. Please manage billing on the web.')
                        return redirect(reverse("calendar_app:app_billing_unavailable", kwargs={"org_slug": org.slug}))

                    return redirect(reverse("calendar_app:pricing_page", kwargs={"org_slug": org.slug}))

            # 7. Stripe Connect enforcement (for client card payments): owners/admins must connect.
            try:
                # Only enforce for privileged roles.
                needs_connect = user_has_role(request.user, org, ["owner", "admin"])
            except Exception:
                needs_connect = False

            if (not is_test_run) and needs_connect:
                if not getattr(settings, 'STRIPE_SECRET_KEY', None):
                    # Opportunistic background cleanup: deactivate trial accounts whose scheduled
                    # deactivation time has passed. This avoids relying on manual CLI commands.
                    # Skipped during tests.
                    try:
                        if not is_test_run:
                            lock_key = 'cc_due_trial_deletion_check_lock'
                            if not cache.get(lock_key):
                                cache.set(lock_key, True, timeout=300)  # run at most every 5 min per process
                                from accounts.deletion import delete_due_trial_accounts
                                delete_due_trial_accounts(limit=20, dry_run=False)
                    except Exception:
                        pass

                    return response
                connected = bool(getattr(org, 'stripe_connect_charges_enabled', False)) and bool(getattr(org, 'stripe_connect_account_id', None))
                if not connected:
                    path = request.path or ''
                    # Normalize trailing slash so /accounts/profile and /accounts/profile/ behave the same.
                    path_norm = path if path.endswith('/') else (path + '/')
                    allow_paths = [
                        f"/billing/bus/{org.slug}/stripe/connect/",
                        "/billing/",  # allow billing routes needed for onboarding
                        f"/bus/{org.slug}/pricing/",
                        "/accounts/login/",
                        "/accounts/login",
                        "/accounts/logout/",
                        "/accounts/logout",
                        "/accounts/signup/",
                        "/accounts/signup",
                        "/accounts/profile/",
                        "/accounts/profile",
                        "/accounts/two_factor/",
                        "/accounts/two_factor",
                        "/accounts/password/change/",
                        "/accounts/password/change",
                        "/accounts/deactivate/",
                        "/accounts/deactivate",
                        "/accounts/delete/",
                        "/accounts/delete",
                        "/static/",
                        settings.MEDIA_URL,
                    ]

                    # Allow public /bus/<slug>/... routes only for GET requests; still enforce on internal POSTs.
                    is_public_bus_root = (path == f"/bus/{org.slug}/")
                    is_public_bus_service = path.startswith(f"/bus/{org.slug}/service/")
                    if (is_public_bus_root or is_public_bus_service) and request.method == 'GET':
                        pass
                    elif any(path.startswith(ap) for ap in allow_paths) or any(path_norm.startswith(ap) for ap in allow_paths):
                        pass
                    else:
                        # Instead of sending users straight to Stripe (server-side redirect),
                        # route them to Profile and auto-open a modal explaining why.
                        try:
                            request.session['cc_auto_open_stripe_connect_modal'] = True
                        except Exception:
                            pass
                        from django.urls import reverse
                        return redirect(reverse('accounts:profile'))

        return response


class PostgresRLSContextMiddleware:
    """Set PostgreSQL session variables used by tenant RLS policies.

    This is a no-op on non-PostgreSQL backends. Values are always reset after
    the request so persistent DB connections do not leak tenant context across
    requests.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def _set_config(self, name: str, value: str):
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config(%s, %s, false)", [name, value])

    def __call__(self, request):
        if getattr(connection, 'vendor', '') != 'postgresql':
            return self.get_response(request)

        user_id = ''
        org_id = ''
        bypass = '0'

        try:
            user = getattr(request, 'user', None)
            if user is not None and getattr(user, 'is_authenticated', False):
                user_id = str(int(getattr(user, 'id', 0) or 0))
                if bool(getattr(user, 'is_superuser', False)) or bool(getattr(user, 'is_staff', False)):
                    bypass = '1'
        except Exception:
            user_id = ''
            bypass = '0'

        try:
            org = (
                getattr(request, 'organization', None)
                or getattr(request, 'custom_domain_organization', None)
                or getattr(request, 'hosted_subdomain_organization', None)
            )
            if org is not None:
                org_id = str(int(getattr(org, 'id', 0) or 0))
        except Exception:
            org_id = ''

        try:
            self._set_config('circlecal.current_user_id', user_id)
            self._set_config('circlecal.current_org_id', org_id)
            self._set_config('circlecal.rls_bypass', bypass)
            return self.get_response(request)
        finally:
            try:
                self._set_config('circlecal.current_user_id', '')
                self._set_config('circlecal.current_org_id', '')
                self._set_config('circlecal.rls_bypass', '0')
            except Exception:
                pass


class UserTimezoneMiddleware:
    """Activate the user's preferred timezone for each request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Default to settings.TIME_ZONE; override using Business tz on org pages,
        # otherwise fall back to user's profile timezone when available.
        deactivate_after = False
        tzname = None
        try:
            # Prefer business timezone when an organization is in context
            org = getattr(request, 'organization', None)
            if org and getattr(org, 'timezone', None):
                tzname = org.timezone
            elif request.user.is_authenticated:
                prof = getattr(request.user, 'profile', None)
                tzname = getattr(prof, 'timezone', None) or None

            if tzname:
                try:
                    timezone.activate(ZoneInfo(tzname))
                    deactivate_after = True
                except Exception:
                    pass
        except Exception:
            pass

        response = self.get_response(request)

        if deactivate_after:
            try:
                timezone.deactivate()
            except Exception:
                pass
        return response


class AdminPinMiddleware:
    """
    Simple middleware to require a PIN before exposing the Django admin pages.
    Configure the PIN via `ADMIN_PIN` in environment/settings. If unset, the
    middleware is a no-op.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Only active if ADMIN_PIN is set
        pin = getattr(settings, 'ADMIN_PIN', None)
        # If no env PIN, check for DB-stored PIN
        if not pin:
            try:
                from .models import AdminPin
                if AdminPin.get_latest_hash():
                    pin = True
            except Exception:
                pin = None

        if not pin:
            return self.get_response(request)

        path = request.path or ''
        admin_prefix = '/' + (getattr(settings, 'ADMIN_PATH', 'admin') or 'admin').strip('/')
        pin_prefix = admin_prefix + '/pin'

        # Allow access to the PIN entry page itself and any static/media paths
        if path.startswith(pin_prefix) or path.startswith('/static/') or path.startswith(settings.MEDIA_URL):
            return self.get_response(request)

        # Intercept requests to the admin area
        if path.startswith(admin_prefix):
            if request.session.get('admin_pin_ok'):
                return self.get_response(request)
            # Redirect to the PIN entry page, preserving the intended destination
            from urllib.parse import urlencode
            qs = urlencode({'next': path})
            return redirect(f'{pin_prefix}/?{qs}')

        return self.get_response(request)


class BusinessSlugRedirectMiddleware:
    """Redirect old /bus/<slug>/ links after a business changes its public slug."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path or ''
        if not path.startswith('/bus/'):
            return self.get_response(request)

        parts = path.strip('/').split('/')
        if len(parts) < 2:
            return self.get_response(request)

        slug = (parts[1] or '').strip()
        if not slug:
            return self.get_response(request)

        # Fast path: slug exists
        try:
            if Organization.objects.filter(slug=slug).only('id').exists():
                return self.get_response(request)
        except Exception:
            return self.get_response(request)

        # Redirect path: slug used to exist
        try:
            from accounts.models import BusinessSlugRedirect
            row = BusinessSlugRedirect.objects.select_related('business').filter(old_slug=slug).first()
            if not row or not getattr(row, 'business', None):
                return self.get_response(request)
            new_slug = getattr(row.business, 'slug', None)
            if not new_slug or new_slug == slug:
                return self.get_response(request)

            parts[1] = new_slug
            trailing = '/' if path.endswith('/') else ''
            new_path = '/' + '/'.join(parts) + trailing
            qs = request.META.get('QUERY_STRING') or ''
            if qs:
                new_path = new_path + '?' + qs
            return HttpResponsePermanentRedirect(new_path)
        except Exception:
            return self.get_response(request)