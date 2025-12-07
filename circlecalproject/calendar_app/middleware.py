# calendar_app/middleware.py
from django.shortcuts import redirect
from django.utils import timezone
from zoneinfo import ZoneInfo
from accounts.models import Business as Organization, Membership
from .utils import user_has_role
from billing.models import Subscription, Plan
from django.conf import settings
class OrganizationMiddleware:
    """
    Resolve the organization for the current request.
    """

    def __init__(self, get_response):
        self.get_response = get_response


    def process_template_response(self, request, response):
        if hasattr(request, "organization"):
            if hasattr(response, "context_data"):
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
                trial_days = 14
                trial_end = timezone.now() + timezone.timedelta(days=trial_days)
                sub = Subscription.objects.create(
                    organization=org,
                    plan=basic_plan,
                    status="trialing",
                    active=False,
                    trial_end=trial_end,
                )

            # If trial expired and not active, redirect to pricing unless already there
            if sub.status == "trialing" and sub.trial_end and timezone.now() >= sub.trial_end:
                # Allow pricing, embedded checkout, and auth pages
                allow_paths = [
                    f"/bus/{org.slug}/pricing/",
                    f"/billing/org/{org.slug}/embedded/",
                    f"/billing/api/bus/{org.slug}/embedded/",
                    "/accounts/login/",
                    "/accounts/signup/",
                ]
                path = request.path
                if not any(path.startswith(ap) for ap in allow_paths):
                    from django.urls import reverse
                    return redirect(reverse("calendar_app:pricing_page", kwargs={"org_slug": org.slug}))

        return response


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
        # Allow access to the PIN entry page itself and any static/media paths
        if path.startswith('/admin/pin') or path.startswith('/static/') or path.startswith(settings.MEDIA_URL):
            return self.get_response(request)

        # Intercept requests to the admin area
        if path.startswith('/admin'):
            if request.session.get('admin_pin_ok'):
                return self.get_response(request)
            # Redirect to the PIN entry page, preserving the intended destination
            from urllib.parse import urlencode
            qs = urlencode({'next': path})
            return redirect(f'/admin/pin/?{qs}')

        return self.get_response(request)