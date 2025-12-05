from django.shortcuts import render, redirect
from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.http import require_http_methods
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from calendar_app.forms import OrganizationCreateForm
import json
from django.utils.text import slugify
from django.middleware.csrf import get_token
from accounts.models import Business as Organization, Membership, Invite
from bookings.models import Booking, Service
from bookings.models import WeeklyAvailability, ServiceWeeklyAvailability
from django.db import transaction
from django.http import HttpResponseForbidden
from calendar_app.permissions import require_roles
from calendar_app.utils import user_has_role
from django.shortcuts import get_object_or_404
from django.utils.crypto import get_random_string
from calendar_app.forms import SignupForm
from django.contrib.auth import login
from django.contrib.auth import logout
from accounts.models import Profile
from django.utils import timezone
from datetime import date
from bookings.models import OrgSettings


def _build_org_weekly_map(org):
    weekly_rows = WeeklyAvailability.objects.filter(organization=org, is_active=True).order_by('weekday', 'start_time')
    availability_map = [[] for _ in range(7)]
    for row in weekly_rows:
        ui_idx = (row.weekday + 1) % 7
        availability_map[ui_idx].append(f"{row.start_time.strftime('%H:%M')}-{row.end_time.strftime('%H:%M')}")
    return availability_map


def _build_service_weekly_map(service):
    rows = service.weekly_availability.filter(is_active=True).order_by('weekday', 'start_time')
    svc_map = [[] for _ in range(7)]
    for row in rows:
        ui_idx = (row.weekday + 1) % 7
        svc_map[ui_idx].append(f"{row.start_time.strftime('%H:%M')}-{row.end_time.strftime('%H:%M')}")
    return svc_map

def home(request):
    return render(request, "calendar_app/index.html")

@login_required
def post_login_redirect(request):
    """
    Proper SaaS workspace redirect logic:
    - No orgs → Create one
    - One org → Dashboard
    - Many orgs → Choose organization
    """
    memberships = Membership.objects.filter(user=request.user, is_active=True).select_related("organization")
    count = memberships.count()

    # If user's profile is incomplete, require profile completion before
    # granting access to the dashboard. Treat missing Profile as incomplete.
    try:
        prof = request.user.profile
        profile_complete = bool(prof.avatar or (prof.timezone and prof.timezone != 'UTC'))
    except Exception:
        profile_complete = False

    if count == 0:
        return redirect("calendar_app:create_business")

    if count == 1:
        # If profile incomplete, send user to profile editing page first
        if not profile_complete:
            return redirect('accounts:profile')
        org = memberships.first().organization
        return redirect("calendar_app:dashboard", org_slug=org.slug)

    return redirect("calendar_app:choose_business")


def calendar_view(request, org_slug):
    org = request.organization
    if not org:
        # handle no organization (redirect to signup or choose business)
        return redirect('calendar_app:choose_business')
    # Serialize weekly availability so the front-end can pre-populate defaultAvailability.
    # Structure: array of objects: [{"day_of_week": 0, "ranges": ["09:00-12:00","13:00-17:00"], "unavailable": false}, ...]
    weekly_rows = WeeklyAvailability.objects.filter(organization=org, is_active=True).order_by('weekday', 'start_time')
    # Backend stores weekday as 0=Monday..6=Sunday; UI expects 0=Sunday..6=Saturday
    # Map model index to UI index: ui = (model + 1) % 7
    availability_map = {i: [] for i in range(7)}
    for row in weekly_rows:
        ui_idx = (row.weekday + 1) % 7
        availability_map[ui_idx].append(f"{row.start_time.strftime('%H:%M')}-{row.end_time.strftime('%H:%M')}")

    availability_serialized = []
    for i in range(7):
        ranges = availability_map[i]
        availability_serialized.append({
            'day_of_week': i,
            'ranges': ranges,
            'unavailable': len(ranges) == 0
        })

    coach_availability_json = json.dumps(availability_serialized)
    get_token(request)
    return render(request, "calendar_app/calendar.html", {
        'organization': org,
        'coach_availability_json': coach_availability_json,
        'org_timezone': org.timezone,  # Pass organization's timezone to template
    })

def demo_calendar_view(request):
    return render(request, "calendar_app/demo_calendar.html")


@require_http_methods(['POST'])
@require_roles(['owner', 'admin'])
def save_availability(request, slug):
    """Simple endpoint to accept weekly availability payload from the calendar UI for a given slug.

    This implementation is intentionally lightweight: it validates JSON and
    returns success. You can extend it to persist availability per-resource later.
    """
    org = request.organization
    # Enforce plan restriction: Basic cannot modify weekly availability
    try:
        from billing.utils import enforce_weekly_availability
        ok, msg = enforce_weekly_availability(org)
        if not ok:
            return HttpResponseForbidden(msg or "Upgrade required for weekly availability edits.")
    except Exception:
        # Fail open if billing module unavailable
        pass
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except Exception:
        return HttpResponseBadRequest('Invalid JSON')

    # Accept two payload formats:
    # 1) { windows: [{ weekday: 0..6, start: 'HH:MM', end: 'HH:MM' }, ...] }
    # 2) { availability: [{ day: 'Monday'|'0'..'6', ranges: ['HH:MM-HH:MM', ...], unavailable: bool }, ...] }

    weekday_map = {
        'sunday': 0, 'monday': 1, 'tuesday': 2, 'wednesday': 3,
        'thursday': 4, 'friday': 5, 'saturday': 6
    }

    cleaned = []  # list of (weekday, start, end)

    windows = payload.get("windows")
    availability = payload.get("availability")

    if isinstance(windows, list):
        for w in windows:
            try:
                wd = int(w.get("weekday"))
                start = str(w.get("start"))
                end = str(w.get("end"))
            except Exception:
                return HttpResponseBadRequest("Invalid window entry")

            if wd < 0 or wd > 6:
                return HttpResponseBadRequest("weekday out of range (0-6)")
            if not (isinstance(start, str) and isinstance(end, str) and len(start) == 5 and len(end) == 5 and start[2] == ':' and end[2] == ':'):
                return HttpResponseBadRequest("Time must be HH:MM")
            # Convert UI index (0=Sun..6=Sat) to model index (0=Mon..6=Sun)
            model_wd = ((wd - 1) % 7)
            cleaned.append((model_wd, start, end))
    elif isinstance(availability, list):
        # Convert availability rows (may contain multiple ranges per weekday)
        for row in availability:
            day = row.get('day')
            ranges = row.get('ranges') or []
            unavailable = bool(row.get('unavailable'))

            # Resolve weekday index from name or numeric string
            try:
                if isinstance(day, int):
                    wd = day
                else:
                    s = str(day).strip()
                    wd = weekday_map.get(s.lower()) if s.lower() in weekday_map else int(s)
            except Exception:
                return HttpResponseBadRequest("Invalid day value")

            if wd < 0 or wd > 6:
                return HttpResponseBadRequest("weekday out of range (0-6)")

            if unavailable:
                # Skip creating windows for unavailable days (no rows stored means no availability)
                continue

            if not isinstance(ranges, list):
                return HttpResponseBadRequest("ranges must be a list")
            for r in ranges:
                try:
                    parts = str(r).split('-')
                    start = parts[0].strip()
                    end = parts[1].strip()
                except Exception:
                    return HttpResponseBadRequest("Invalid range entry")
                if not (len(start) == 5 and len(end) == 5 and start[2] == ':' and end[2] == ':'):
                    return HttpResponseBadRequest("Time must be HH:MM")
                if start >= end:
                    return HttpResponseBadRequest("Start must be before end")
                # Convert UI index (0=Sun..6=Sat) to model index (0=Mon..6=Sun)
                model_wd = ((wd - 1) % 7)
                cleaned.append((model_wd, start, end))
    else:
        return HttpResponseBadRequest("Missing windows or availability array")

    # Replace existing rows atomically
    with transaction.atomic():
        WeeklyAvailability.objects.filter(organization=org).delete()
        WeeklyAvailability.objects.bulk_create([
            WeeklyAvailability(
                organization=org,
                weekday=wd,
                start_time=start,
                end_time=end,
                is_active=True,
            )
            for (wd, start, end) in cleaned
        ])

    return JsonResponse({'success': True, 'count': len(cleaned)})


@require_http_methods(['POST'])
@require_roles(['owner', 'admin'])
def save_availability_general(request):
    """General availability save endpoint (no slug).

    Accepts the same payload as `save_availability` but does not require a slug.
    """
    org = request.organization
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except Exception:
        return HttpResponseBadRequest('Invalid JSON')

    weekday_map = {
        'sunday': 0, 'monday': 1, 'tuesday': 2, 'wednesday': 3,
        'thursday': 4, 'friday': 5, 'saturday': 6
    }

    cleaned = []
    windows = payload.get("windows")
    availability = payload.get("availability")

    if isinstance(windows, list):
        for w in windows:
            try:
                wd = int(w.get("weekday"))
                start = str(w.get("start"))
                end = str(w.get("end"))
            except Exception:
                return HttpResponseBadRequest("Invalid window entry")
            if wd < 0 or wd > 6:
                return HttpResponseBadRequest("weekday out of range (0-6)")
            if not (isinstance(start, str) and isinstance(end, str) and len(start) == 5 and len(end) == 5 and start[2] == ':' and end[2] == ':'):
                return HttpResponseBadRequest("Time must be HH:MM")
            # Convert UI index (0=Sun..6=Sat) to model index (0=Mon..6=Sun)
            model_wd = ((wd - 1) % 7)
            cleaned.append((model_wd, start, end))
    elif isinstance(availability, list):
        for row in availability:
            day = row.get('day')
            ranges = row.get('ranges') or []
            unavailable = bool(row.get('unavailable'))

            try:
                if isinstance(day, int):
                    wd = day
                else:
                    s = str(day).strip()
                    wd = weekday_map.get(s.lower()) if s.lower() in weekday_map else int(s)
            except Exception:
                return HttpResponseBadRequest("Invalid day value")

            if wd < 0 or wd > 6:
                return HttpResponseBadRequest("weekday out of range (0-6)")

            if unavailable:
                continue

            if not isinstance(ranges, list):
                return HttpResponseBadRequest("ranges must be a list")
            for r in ranges:
                try:
                    parts = str(r).split('-')
                    start = parts[0].strip()
                    end = parts[1].strip()
                except Exception:
                    return HttpResponseBadRequest("Invalid range entry")
                if not (len(start) == 5 and len(end) == 5 and start[2] == ':' and end[2] == ':'):
                    return HttpResponseBadRequest("Time must be HH:MM")
                if start >= end:
                    return HttpResponseBadRequest("Start must be before end")
                # Convert UI index (0=Sun..6=Sat) to model index (0=Mon..6=Sun)
                model_wd = ((wd - 1) % 7)
                cleaned.append((model_wd, start, end))
    else:
        return HttpResponseBadRequest("Missing windows or availability array")

    with transaction.atomic():
        WeeklyAvailability.objects.filter(organization=org).delete()
        WeeklyAvailability.objects.bulk_create([
            WeeklyAvailability(
                organization=org,
                weekday=wd,
                start_time=start,
                end_time=end,
                is_active=True,
            )
            for (wd, start, end) in cleaned
        ])

    return JsonResponse({'success': True, 'count': len(cleaned)})


def invite_member(request, org_slug):
    org = request.organization
    if not request.user_has_role('owner', org): # implement role helper
        return HttpResponseForbidden()
    email = request.POST['email']
    role = request.POST.get('role','staff')
    # Create inactive user or send invite link to sign up + join org
    # Save pending invitation and send email with token




@login_required
def create_business(request):
    """
    Create a brand new organization during onboarding or later.
    """
    if request.method == "POST":
        form = OrganizationCreateForm(request.POST)
        if form.is_valid():
            org = form.save(commit=False)
            org.owner = request.user
            org.slug = form.cleaned_data["slug"]
            # Save timezone chosen during business creation
            org.timezone = form.cleaned_data.get('timezone', org.timezone)
            org.save()

            # Create membership for the creator
            Membership.objects.create(
                user=request.user,
                organization=org,
                role="owner",
                is_active=True,
            )

            messages.success(request, f"Organization '{org.name}' created.")
            # Require the user to customize their profile before accessing the dashboard
            return redirect('accounts:profile')
    else:
        form = OrganizationCreateForm()

    resp = render(request, "calendar_app/create_business.html", {
        "form": form,
    })
    # Mark this path so that if the user logs out before completing
    # creation, we can return them here after they log back in.
    try:
        resp.set_cookie('post_login_redirect', request.path, max_age=60*60*24)
    except Exception:
        pass
    return resp


@login_required
def choose_business(request):
    """
    Show all organizations the user is a member of.
    This powers the workspace picker in choose_organization.html.
    """

    memberships = Membership.objects.filter(
        user=request.user,
        is_active=True
    ).select_related("organization")

    organizations = [m.organization for m in memberships]

    resp = render(request, "calendar_app/choose_business.html", {
        "organizations": organizations
    })
    # Preserve returning users to this page if they logout mid-onboarding
    try:
        resp.set_cookie('post_login_redirect', request.path, max_age=60*60*24)
    except Exception:
        pass
    return resp


@login_required
def edit_business(request, org_slug):
    """
    Edit business details - only owners can edit.
    """
    org = get_object_or_404(Organization, slug=org_slug)
    
    # Check if user is effectively owner (by field or role)
    membership = Membership.objects.filter(user=request.user, organization=org, is_active=True).first()
    is_owner_user = (org.owner_id == request.user.id)
    is_owner_role = (membership and membership.role == 'owner')
    if not (is_owner_user or is_owner_role):
        messages.error(request, "Only owners can edit business details.")
        return redirect('calendar_app:choose_business')
    
    if request.method == "POST":
        org.name = request.POST.get('name', org.name).strip()
        org.timezone = request.POST.get('timezone', org.timezone)
        org.save()
        messages.success(request, f"Business '{org.name}' updated successfully.")
        return redirect('calendar_app:choose_business')
    
    # Get list of common timezones
    import zoneinfo
    common_timezones = [
        'America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles',
        'America/Phoenix', 'America/Anchorage', 'Pacific/Honolulu',
        'Europe/London', 'Europe/Paris', 'Europe/Berlin',
        'Asia/Tokyo', 'Asia/Shanghai', 'Asia/Dubai',
        'Australia/Sydney', 'UTC'
    ]
    
    return render(request, "calendar_app/edit_business.html", {
        "org": org,
        "timezones": common_timezones,
    })


@login_required
def delete_business(request, org_slug):
    """
    Delete business - only owners can delete.
    """
    org = get_object_or_404(Organization, slug=org_slug)
    
    # Check if user is effectively owner (by field or role)
    membership = Membership.objects.filter(user=request.user, organization=org, is_active=True).first()
    is_owner_user = (org.owner_id == request.user.id)
    is_owner_role = (membership and membership.role == 'owner')
    if not (is_owner_user or is_owner_role):
        messages.error(request, "Only owners can delete businesses.")
        return redirect('calendar_app:choose_business')
    
    if request.method == "POST":
        org_name = org.name
        org.delete()
        messages.success(request, f"Business '{org_name}' has been deleted.")
        return redirect('calendar_app:choose_business')
    
    return render(request, "calendar_app/delete_business.html", {
        "org": org,
    })

@login_required
@login_required
def dashboard(request, org_slug):
    org = get_object_or_404(Organization, slug=org_slug)
    
    # Check if user has access to this organization
    membership = Membership.objects.filter(user=request.user, organization=org, is_active=True).first()
    if not membership:
        messages.error(request, "You don't have access to this organization.")
        return redirect('calendar_app:choose_business')
    
    memberships = request.user.memberships.select_related("organization")

    # Provide subscription/trial info for conditional portal link
    from billing.utils import get_subscription
    subscription = get_subscription(org)
    trialing_active = False
    if subscription and subscription.status == "trialing" and subscription.trial_end and subscription.trial_end > timezone.now():
        trialing_active = True

    return render(request, "calendar_app/dashboard.html", {
        "memberships": memberships,
        "org": org,
        "subscription": subscription,
        "trialing_active": trialing_active,
    })


@require_http_methods(["GET", "POST"])
@require_roles(["owner", "admin"])
def org_refund_settings(request, org_slug):
    """User-facing org-wide refund policy settings."""
    org = get_object_or_404(Organization, slug=org_slug)
    settings_obj, _ = OrgSettings.objects.get_or_create(organization=org)

    if request.method == "POST":
        settings_obj.org_refunds_allowed = bool(request.POST.get("org_refunds_allowed"))
        try:
            settings_obj.org_refund_cutoff_hours = int(request.POST.get("org_refund_cutoff_hours", settings_obj.org_refund_cutoff_hours))
        except Exception:
            settings_obj.org_refund_cutoff_hours = settings_obj.org_refund_cutoff_hours
        settings_obj.org_refund_policy_text = request.POST.get("org_refund_policy_text", "").strip()
        settings_obj.save()
        messages.success(request, "Refund policy updated.")
        return redirect("calendar_app:dashboard", org_slug=org.slug)

    return render(request, "calendar_app/org_refund_settings.html", {
        "org": org,
        "settings": settings_obj,
    })


@require_http_methods(["GET", "POST"])
@require_roles(["owner", "admin"])
def create_service(request, org_slug):
    org = get_object_or_404(Organization, slug=org_slug)
    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        # Auto-generate slug from name if not provided
        from django.utils.text import slugify
        base_slug = slugify(name)
        slug = (request.POST.get("slug") or base_slug)
        if not base_slug:
            messages.error(request, "Service name is required.")
            return render(request, "calendar_app/create_service.html", { "org": org })

        svc = Service.objects.create(
            organization=org,
            name=name,
            slug=slug,
            description=request.POST.get("description", ""),
            duration=int(request.POST.get("duration", 30)),
            price=float(request.POST.get("price", 0)),
            buffer_before=int(request.POST.get("buffer_before", 0)),
            buffer_after=int(request.POST.get("buffer_after", 0)),
            min_notice_hours=int(request.POST.get("min_notice_hours", 1)),
            max_booking_days=int(request.POST.get("max_booking_days", 30)),
        )
        # Refund fields
        svc.refunds_allowed = request.POST.get("refunds_allowed") is not None
        try:
            svc.refund_cutoff_hours = int(request.POST.get("refund_cutoff_hours", svc.refund_cutoff_hours))
        except Exception:
            pass
        svc.refund_policy_text = request.POST.get("refund_policy_text", "").strip()
        svc.save()

        messages.success(request, "Service created.")
        return redirect("calendar_app:dashboard", org_slug=org.slug)

    return render(request, "calendar_app/create_service.html", { "org": org })


@require_http_methods(["GET", "POST"])
@require_roles(["owner", "admin"])
def edit_service(request, org_slug, service_id):
    org = get_object_or_404(Organization, slug=org_slug)
    service = get_object_or_404(Service, id=service_id, organization=org)
    if request.method == "POST":
        # Basic fields
        service.name = (request.POST.get("name") or service.name).strip()
        service.description = (request.POST.get("description") or service.description)

        # Numeric fields with validation
        def _set_int(field_name, default):
            val = request.POST.get(field_name, None)
            if val is not None and val != "":
                try:
                    return int(val)
                except Exception:
                    messages.error(request, f"Invalid value for {field_name}.")
            return default
        def _set_float(field_name, default):
            val = request.POST.get(field_name, None)
            if val is not None and val != "":
                try:
                    return float(val)
                except Exception:
                    messages.error(request, f"Invalid value for {field_name}.")
            return default

        service.duration = _set_int("duration", service.duration)
        service.price = _set_float("price", float(service.price))
        service.buffer_before = _set_int("buffer_before", service.buffer_before)
        service.buffer_after = _set_int("buffer_after", service.buffer_after)
        service.min_notice_hours = _set_int("min_notice_hours", service.min_notice_hours)
        service.max_booking_days = _set_int("max_booking_days", service.max_booking_days)

        service.is_active = request.POST.get("is_active") is not None

        # Refund fields
        service.refunds_allowed = request.POST.get("refunds_allowed") is not None
        service.refund_cutoff_hours = _set_int("refund_cutoff_hours", service.refund_cutoff_hours)
        service.refund_policy_text = (request.POST.get("refund_policy_text") or "").strip()

        service.save()
        service.refresh_from_db()
        messages.success(request, "Service updated.")
        return render(request, "calendar_app/edit_service.html", { "org": org, "service": service })

    return render(request, "calendar_app/edit_service.html", { "org": org, "service": service })


def team_dashboard(request, org_slug):
    org = request.organization

    # Only owners and admins can view/manage team
    if not user_has_role(request.user, org, ["owner", "admin"]):
        return HttpResponseForbidden("Not allowed.")

    members = Membership.objects.filter(organization=org).select_related("user")

    return render(request, "calendar_app/team_dashboard.html", {
        "org": org,
        "members": members,
    })


def invite_member(request, org_slug):
    org = request.organization

    if not user_has_role(request.user, org, ["owner", "admin"]):
        return HttpResponseForbidden("No permission.")

    if request.method == "POST":
        email = request.POST["email"]
        role = request.POST.get("role", "staff")

        token = get_random_string(48)

        # Enforce plan limits: only Team plan allows multiple staff
        try:
            from billing.utils import can_add_staff
            active_members = Membership.objects.filter(organization=org, is_active=True).count()
            if active_members >= 1 and not can_add_staff(org):
                messages.error(request, "Team plan required to invite additional staff members. Upgrade to add more team members.")
                return redirect("team_dashboard", org_slug=org.slug)
        except Exception:
            pass

        Invite.objects.create(
            organization=org,
            email=email,
            role=role,
            token=token
        )

        # TODO: send actual email later
        print("Invite link:", f"http://127.0.0.1:8000/invite/{token}/")

        return redirect("team_dashboard", org_slug=org.slug)

    return HttpResponseForbidden("Invalid request")


def remove_member(request, org_slug, member_id):
    org = request.organization

    if not user_has_role(request.user, org, ["owner", "admin"]):
        return HttpResponseForbidden("No permission.")

    member = get_object_or_404(Membership, id=member_id, organization=org)

    # Owner cannot remove themselves
    if member.user == org.owner:
        return HttpResponseForbidden("Cannot remove organization owner.")

    member.delete()
    return redirect("team_dashboard", org_slug=org.slug)

def update_member_role(request, org_slug, member_id):
    org = request.organization

    if not user_has_role(request.user, org, ["owner", "admin"]):
        return HttpResponseForbidden("Not allowed.")

    member = get_object_or_404(Membership, id=member_id, organization=org)

    new_role = request.GET.get("role")
    if new_role not in ["owner", "admin", "manager", "staff"]:
        return HttpResponseForbidden("Invalid role.")

    member.role = new_role
    member.save()

    return redirect("team_dashboard", org_slug=org.slug)


def accept_invite(request, token):
    invite = get_object_or_404(Invite, token=token)

    # If not logged in, redirect to login
    if not request.user.is_authenticated:
        request.session["pending_invite"] = token
        return redirect("/login/")

    user = request.user
    org = invite.organization

    # Create membership
    Membership.objects.get_or_create(
        user=user,
        organization=org,
        defaults={"role": invite.role}
    )

    invite.accepted = True
    invite.save()

    return redirect(f"/bus/{org.slug}/calendar/")





def pricing_page(request, org_slug):
    from billing.models import Plan
    from billing.utils import get_subscription
    
    org = request.organization
    if not org:
        return redirect("calendar_app:choose_business")
    
    # Only show active plans and order by price (low -> high)
    plans = Plan.objects.filter(is_active=True).order_by('price')

    # Provide subscription context to template so it can show trial status
    subscription = get_subscription(org)
    # Determine current_plan object for template: prefer explicit subscription.plan,
    # but if the org has a subscription without a linked plan (e.g., trial created
    # without setting plan), fall back to the plan slug derived from billing.utils
    current_plan = None
    if subscription:
        if subscription.plan:
            current_plan = subscription.plan
        else:
            from billing.utils import get_plan_slug
            from billing.models import Plan
            current_plan = Plan.objects.filter(slug=get_plan_slug(org)).first()

    # Also expose a display_plan variable (same as current_plan) for templates
    display_plan = current_plan

    # Provide 'now' for template comparisons (trial end etc.)
    now = timezone.now()

    return render(request, "calendar_app/pricing.html", {
        "org": org,
        "plans": plans,
        "current_plan": current_plan,
        "display_plan": display_plan,
        "subscription": subscription,
        "now": now,
    })



def signup(request):
    if request.method == "POST":
        form = SignupForm(request.POST)
        if form.is_valid():
            user = form.save()
            # When multiple authentication backends are configured Django
            # requires the backend to be supplied when calling `login()`
            # (or the user object must have a `backend` attribute). The
            # project uses Axes plus the default ModelBackend, so pick a
            # sensible backend to attach here.
            try:
                from django.conf import settings
                backend = None
                for b in getattr(settings, 'AUTHENTICATION_BACKENDS', []):
                    if 'ModelBackend' in b:
                        backend = b
                        break
                if not backend:
                    # fallback to the first configured backend
                    backend = settings.AUTHENTICATION_BACKENDS[0]
            except Exception:
                backend = None

            if backend:
                login(request, user, backend=backend)
            else:
                # As a last resort, try to login without specifying backend
                # (this will raise the same ValueError if Django requires it).
                login(request, user)
            return redirect("calendar_app:choose_business")
    else:
        form = SignupForm()

    return render(request, "registration/signup.html", {"form": form})

def logout(request):
    logout(request)
    return redirect('/')




@login_required
@require_roles(['owner', 'admin', 'manager'])
def services_page(request, org_slug):
    """
    List all services for this organization (coach dashboard view).
    Only owner/admin/manager can access.
    """
    org = request.organization  # middleware already sets this for org routes

    services = Service.objects.filter(organization=org).order_by("name")
    selected_slug = request.GET.get("service")
    service = None
    if selected_slug:
        service = services.filter(slug=selected_slug).first()
    if not service:
        service = services.first()

    print(f"[DEBUG] services_page: org={org.slug}, num_services={services.count()}, selected_service={getattr(service, 'slug', None)}")
    return render(request, "calendar_app/public_service_page.html", {
        "org": org,
        "services": services,
        "service": service,
    })


@login_required
@require_roles(['owner', 'admin', 'manager', 'staff'])
def services_page(request, org_slug):
    """
    List all services for this organization (internal management page).
    """
    org = request.organization
    services = Service.objects.filter(organization=org).order_by('name')
    
    return render(request, "calendar_app/services.html", {
        "org": org,
        "services": services,
    })


@login_required
@require_http_methods(['GET', 'POST'])
@require_roles(['owner', 'admin', 'manager'])
def create_service(request, org_slug):
    """
    Simple create-service form for coaches with refund fields.
    """
    org = request.organization

    if request.method == "POST":
        # Plan enforcement: Basic only allows 1 active service
        try:
            from billing.utils import enforce_service_limit
            ok, msg = enforce_service_limit(org)
            if not ok:
                messages.error(request, msg or "Upgrade required to add more services.")
                return redirect(f"/bus/{org.slug}/services/")
        except Exception:
            # Fail open if billing utils not available
            pass
        name = (request.POST.get("name") or "").strip()
        slug_input = (request.POST.get("slug") or "").strip()
        description = (request.POST.get("description") or "").strip()

        duration_raw = request.POST.get("duration") or "60"
        price_raw = request.POST.get("price") or "0"
        buffer_before_raw = request.POST.get("buffer_before") or "0"
        buffer_after_raw = request.POST.get("buffer_after") or "0"
        min_notice_hours_raw = request.POST.get("min_notice_hours") or "1"
        max_booking_days_raw = request.POST.get("max_booking_days") or "30"

        if not name:
            messages.error(request, "Name is required.")
        else:
            # Build slug (unique per organization)
            base_slug = slugify(slug_input or name)
            slug = base_slug or get_random_string(8)

            # ensure uniqueness for this org
            counter = 1
            while Service.objects.filter(organization=org, slug=slug).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1

            try:
                duration = int(duration_raw)
                buffer_before = int(buffer_before_raw)
                buffer_after = int(buffer_after_raw)
                min_notice_hours = int(min_notice_hours_raw)
                max_booking_days = int(max_booking_days_raw)
                price = float(price_raw)
            except ValueError:
                messages.error(request, "Numeric fields must be valid numbers.")
            else:
                svc = Service.objects.create(
                    organization=org,
                    name=name,
                    slug=slug,
                    description=description,
                    duration=duration,
                    price=price,
                    buffer_before=buffer_before,
                    buffer_after=buffer_after,
                    min_notice_hours=min_notice_hours,
                    max_booking_days=max_booking_days,
                    is_active=True,
                )
                # Refund fields
                svc.refunds_allowed = request.POST.get("refunds_allowed") is not None
                try:
                    svc.refund_cutoff_hours = int(request.POST.get("refund_cutoff_hours", svc.refund_cutoff_hours))
                except Exception:
                    pass
                svc.refund_policy_text = (request.POST.get("refund_policy_text") or "").strip()
                svc.save()

                messages.success(request, "Service created.")
                return redirect("calendar_app:services_page", org_slug=org.slug)

    # GET or form error → show empty/default form
    return render(request, "calendar_app/create_service.html", {
        "org": org,
    })


@login_required
@require_http_methods(['GET', 'POST'])
@require_roles(['owner', 'admin', 'manager'])
def edit_service(request, org_slug, service_id):
    """
    Edit an existing service, including refund fields.
    """
    org = request.organization
    service = get_object_or_404(Service, id=service_id, organization=org)

    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        description = (request.POST.get("description") or "").strip()

        duration_raw = request.POST.get("duration") or "60"
        price_raw = request.POST.get("price") or "0"
        buffer_before_raw = request.POST.get("buffer_before") or "0"
        buffer_after_raw = request.POST.get("buffer_after") or "0"
        min_notice_hours_raw = request.POST.get("min_notice_hours") or "1"
        max_booking_days_raw = request.POST.get("max_booking_days") or "30"
        is_active = request.POST.get("is_active") == "on"

        if not name:
            messages.error(request, "Name is required.")
        else:
            try:
                duration = int(duration_raw)
                buffer_before = int(buffer_before_raw)
                buffer_after = int(buffer_after_raw)
                min_notice_hours = int(min_notice_hours_raw)
                max_booking_days = int(max_booking_days_raw)
                price = float(price_raw)
            except ValueError:
                messages.error(request, "Numeric fields must be valid numbers.")
            else:
                service.name = name
                service.description = description
                service.duration = duration
                service.price = price
                service.buffer_before = buffer_before
                service.buffer_after = buffer_after
                service.min_notice_hours = min_notice_hours
                service.max_booking_days = max_booking_days
                service.is_active = is_active

                # Refund fields
                service.refunds_allowed = request.POST.get("refunds_allowed") is not None
                cutoff_raw = request.POST.get("refund_cutoff_hours")
                if service.refunds_allowed:
                    # When refunds are allowed, require cutoff >= 1
                    if cutoff_raw is not None and cutoff_raw != "":
                        try:
                            cutoff_val = int(cutoff_raw)
                            if cutoff_val < 1:
                                messages.error(request, "Refund cutoff must be at least 1 hour when refunds are allowed.")
                                cutoff_val = 1
                            service.refund_cutoff_hours = cutoff_val
                        except ValueError:
                            messages.error(request, "Invalid value for refund_cutoff_hours.")
                    else:
                        # Default to 24 if not provided
                        service.refund_cutoff_hours = max(1, service.refund_cutoff_hours or 24)
                else:
                    # If refunds are not allowed, cutoff is 0
                    service.refund_cutoff_hours = 0

                service.refund_policy_text = (request.POST.get("refund_policy_text") or "").strip()

                service.save()
                # Handle per-service weekly availability fields.
                # Expect form fields named `svc_avail_0` .. `svc_avail_6` representing UI weekday 0=Sunday..6=Saturday
                # Each field may contain comma-separated ranges like "09:00-12:00,13:00-17:00" or be empty.
                svc_windows = []
                for ui_day in range(7):
                    key = f"svc_avail_{ui_day}"
                    raw = request.POST.get(key, "") or ""
                    raw = raw.strip()
                    if not raw:
                        continue
                    # UI weekday 0=Sunday..6=Saturday -> model weekday 0=Monday..6=Sunday
                    model_wd = ((ui_day - 1) % 7)
                    parts = [p.strip() for p in raw.split(',') if p.strip()]
                    for part in parts:
                        try:
                            start_s, end_s = [x.strip() for x in part.split('-')]
                        except Exception:
                            messages.error(request, f"Invalid range format for {key}: {part}")
                            continue
                        # Basic sanity check
                        if len(start_s) != 5 or len(end_s) != 5 or start_s[2] != ':' or end_s[2] != ':':
                            messages.error(request, f"Invalid time format for {key}: {part}")
                            continue
                        svc_windows.append((model_wd, start_s, end_s))

                # Persist service windows: validate using model.clean() before saving
                if svc_windows:
                    # Build instances and validate
                    new_objs = []
                    from datetime import datetime
                    for (wd, start_s, end_s) in svc_windows:
                        try:
                            st = datetime.strptime(start_s, '%H:%M').time()
                            et = datetime.strptime(end_s, '%H:%M').time()
                        except Exception:
                            messages.error(request, f"Invalid time values: {start_s}-{end_s}")
                            continue
                        obj = ServiceWeeklyAvailability(
                            service=service,
                            weekday=wd,
                            start_time=st,
                            end_time=et,
                            is_active=True,
                        )
                        try:
                            obj.full_clean()
                        except Exception as e:
                            # Display first validation error
                            messages.error(request, f"Service availability error: {e}")
                        else:
                            new_objs.append(obj)

                    if new_objs:
                        # Replace existing windows
                        ServiceWeeklyAvailability.objects.filter(service=service).delete()
                        ServiceWeeklyAvailability.objects.bulk_create(new_objs)
                else:
                    # If no posted windows present, remove any existing per-service windows
                    ServiceWeeklyAvailability.objects.filter(service=service).delete()
                
                messages.success(request, "Service updated.")
                # Return to edit page to reflect saved values immediately
                return redirect("calendar_app:edit_service", org_slug=org.slug, service_id=service.id)

    # Prepare rows for editing: label, org defaults, and service-specific defaults (string joined)
    org_map = _build_org_weekly_map(org)
    svc_map = _build_service_weekly_map(service)
    weekday_labels = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat']
    weekly_edit_rows = []
    for ui in range(7):
        org_ranges = ', '.join(org_map[ui]) if org_map and org_map[ui] else ''
        svc_ranges = ', '.join(svc_map[ui]) if svc_map and svc_map[ui] else ''
        weekly_edit_rows.append({
            'ui': ui,
            'label': weekday_labels[ui],
            'org_ranges': org_ranges,
            'svc_ranges': svc_ranges,
        })

    return render(request, "calendar_app/edit_service.html", {
        "org": org,
        "service": service,
        "weekly_edit_rows": weekly_edit_rows,
    })


@login_required
@require_http_methods(['POST'])
@require_roles(['owner', 'admin'])
def delete_service(request, org_slug, service_id):
    """
    Delete a service (owner/admin only).
    """
    org = request.organization
    service = get_object_or_404(Service, id=service_id, organization=org)
    
    service_name = service.name
    service.delete()
    
    messages.success(request, f'Service "{service_name}" deleted.')
    return redirect("calendar_app:services_page", org_slug=org.slug)


@login_required
@require_roles(['owner', 'admin', 'manager', 'staff'])
def bookings_list(request, org_slug):
    """
    Display all bookings for this organization.
    """
    org = request.organization
    bookings = Booking.objects.filter(
        organization=org,
        is_blocking=False,           # Exclude full-day/blocking overrides
        service__isnull=False        # Exclude availability overrides; include only real bookings
    ).select_related('service').order_by('-start')
    
    services = Service.objects.filter(organization=org, is_active=True)
    
    now = timezone.now()
    today = date.today()
    
    return render(request, "calendar_app/bookings_list.html", {
        "organization": org,
        "bookings": bookings,
        "services": services,
        "now": now,
        "today": today,
    })


@login_required
@require_http_methods(['POST'])
@require_roles(['owner', 'admin', 'manager'])
def delete_booking(request, org_slug, booking_id):
    """
    Delete a specific booking (owner/admin/manager only).
    """
    org = request.organization
    booking = get_object_or_404(Booking, id=booking_id, organization=org)
    
    booking.delete()
    
    return JsonResponse({'status': 'ok'})