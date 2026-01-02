from django.http import JsonResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.dateparse import parse_datetime
from django.utils.timezone import make_aware
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.core.signing import TimestampSigner, BadSignature, SignatureExpired
from django.db.models import Q, Count
from typing import Optional
from accounts.models import Business as Organization
from accounts.models import Membership
from bookings.models import Service
from bookings.models import FacilityResource, ServiceResource
from datetime import datetime
from zoneinfo import ZoneInfo
import json
from django.conf import settings
from django.core.mail import send_mail
from datetime import timedelta
from bookings.models import Booking
from bookings.models import WeeklyAvailability, OrgSettings
from bookings.models import ServiceAssignment
from bookings.models import PublicBookingIntent
from calendar_app.utils import user_has_role  # <-- single source of truth
from calendar_app.permissions import require_roles
from billing.utils import get_subscription
from billing.utils import get_plan_slug, TEAM_SLUG, PRO_SLUG
from billing.utils import can_use_offline_payment_methods
from bookings.models import build_offline_payment_instructions
from billing.utils import can_use_offline_payment_methods


def _can_use_per_date_overrides(org: Organization) -> bool:
    """Per-date overrides are available on Pro/Team only (not Trial/Basic)."""
    try:
        plan_slug = get_plan_slug(org)
        if plan_slug not in {PRO_SLUG, TEAM_SLUG}:
            return False
        sub = get_subscription(org)
        if sub and getattr(sub, 'status', '') == 'trialing':
            return False
        return True
    except Exception:
        return False


def _trial_single_active_service(org: Organization) -> bool:
    """True when trial onboarding should use Calendar (org weekly) availability.

    Requirement: when only one service is active (trial), per-service availability
    should be disabled and availability should follow calendar.html (org weekly).
    """
    try:
        sub = get_subscription(org)
        if not sub or getattr(sub, 'status', '') != 'trialing':
            return False
    except Exception:
        return False

    try:
        return org.services.filter(is_active=True).count() <= 1
    except Exception:
        # Be conservative: if we can't count, don't change behavior.
        return False


def _active_service_freeze_for_date(org, service, target_date, org_tz):
    """Return a ServiceSettingFreeze for (service, date) only when there are bookings.

    This matches the slot-generation behavior: we ignore stale freezes when
    there are no remaining bookings for that service/date.
    """
    if service is None or target_date is None:
        return None
    try:
        from bookings.models import ServiceSettingFreeze
        freeze = ServiceSettingFreeze.objects.filter(service=service, date=target_date).first()
        if not freeze:
            return None
    except Exception:
        return None

    try:
        day_start = datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0)
        if day_start.tzinfo is None:
            day_start = make_aware(day_start, org_tz)
        day_end = day_start + timedelta(days=1)
        has_bookings = Booking.objects.filter(service=service, organization=org, start__gte=day_start, start__lt=day_end).exists()
        return freeze if has_bookings else None
    except Exception:
        # Be conservative on errors: keep freeze.
        return freeze


def _dt_windows_from_weekly(date_obj, org_tz, weekly_rows):
    windows = []
    for w in weekly_rows:
        try:
            ws = datetime(date_obj.year, date_obj.month, date_obj.day, w.start_time.hour, w.start_time.minute, tzinfo=org_tz)
            we = datetime(date_obj.year, date_obj.month, date_obj.day, w.end_time.hour, w.end_time.minute, tzinfo=org_tz)
            if we > ws:
                windows.append((ws, we))
        except Exception:
            continue
    return windows


def _interval_is_within_any_window(start_dt, end_dt, windows):
    for ws, we in windows:
        if ws <= start_dt and end_dt <= we:
            return True
    return False


def _intervals_overlap(a_start, a_end, b_start, b_end):
    return a_start < b_end and b_start < a_end


def _member_weekly_windows_for_date(org, membership, date_obj, org_tz):
    """Return the member's effective weekly windows for a date.

    Prefer explicit MemberWeeklyAvailability; else fall back to org WeeklyAvailability.
    If org has no weekly rows at all, treat as fully available (legacy).
    """
    weekday = date_obj.weekday()
    try:
        from bookings.models import MemberWeeklyAvailability
        rows = MemberWeeklyAvailability.objects.filter(membership=membership, is_active=True, weekday=weekday)
        if rows.exists():
            return _dt_windows_from_weekly(date_obj, org_tz, rows)
    except Exception:
        pass

    any_org_rows = WeeklyAvailability.objects.filter(organization=org, is_active=True).exists()
    if not any_org_rows:
        # Legacy behavior: no weekly rows implies open availability.
        return [(datetime(date_obj.year, date_obj.month, date_obj.day, 0, 0, tzinfo=org_tz), datetime(date_obj.year, date_obj.month, date_obj.day, 23, 59, tzinfo=org_tz))]

    org_rows = WeeklyAvailability.objects.filter(organization=org, is_active=True, weekday=weekday)
    return _dt_windows_from_weekly(date_obj, org_tz, org_rows)


def _service_has_explicit_weekly(service):
    try:
        org = getattr(service, 'organization', None)
        if org and _trial_single_active_service(org):
            return False
        return service.weekly_availability.filter(is_active=True).exists()
    except Exception:
        return False


def _service_requires_explicit_weekly(org, service: Optional[Service]):
    """Return True when a service should NOT fall back to org weekly availability.

    We treat these as explicitly-scoped schedules:
    - Unassigned services (0 assignees): must have their own service schedule.
    - Shared services (2+ assignees): must have their own service schedule.
    - Single-assignee services where that assignee has multiple solo services:
      service schedules must be explicitly partitioned per service.

    In these cases, an empty service schedule means "no availability".
    """
    if not service:
        return False

    # Trial single-service onboarding: always fall back to org weekly availability.
    if _trial_single_active_service(org):
        return False

    try:
        assigned_ids = list(
            ServiceAssignment.objects.filter(service=service)
            .values_list('membership_id', flat=True)
            .distinct()
        )
    except Exception:
        assigned_ids = []

    if len(assigned_ids) == 0:
        return True
    if len(assigned_ids) >= 2:
        return True

    # Single assignee: if they have multiple solo services, require explicit partitioning.
    mid = assigned_ids[0]
    try:
        solo_service_ids = list(
            Service.objects.filter(organization=org, assignments__membership_id=mid)
            .annotate(num_assignees=Count('assignments'))
            .filter(num_assignees=1)
            .values_list('id', flat=True)
            .distinct()
        )
    except Exception:
        solo_service_ids = []

    return len(solo_service_ids) > 1


def _service_weekly_windows_for_date(service, date_obj, org_tz):
    try:
        org = getattr(service, 'organization', None)
        if org and _trial_single_active_service(org):
            return []
    except Exception:
        pass

    weekday = date_obj.weekday()
    try:
        rows = service.weekly_availability.filter(is_active=True, weekday=weekday)
    except Exception:
        rows = []
    return _dt_windows_from_weekly(date_obj, org_tz, rows)


def _service_scoped_per_date_windows(org, service_id, date_obj, org_tz):
    day_start = datetime(date_obj.year, date_obj.month, date_obj.day, 0, 0, 0, tzinfo=org_tz)
    day_end = day_start + timedelta(days=1)
    qs = Booking.objects.filter(
        organization=org,
        service__isnull=True,
        start__lt=day_end,
        end__gt=day_start,
        client_name=f'scope:svc:{int(service_id)}',
    )
    blocked = False
    windows = []
    for bk in qs:
        try:
            bk_start = bk.start.astimezone(org_tz)
            bk_end = bk.end.astimezone(org_tz)
        except Exception:
            bk_start = bk.start
            bk_end = bk.end

        if bk.is_blocking:
            # Treat any blocking override covering the whole day as a full-day block.
            # Note: UI uses 23:59 as end_time for full-day blocks.
            if bk_start <= day_start and bk_end >= (day_end - timedelta(minutes=1)):
                blocked = True
        else:
            if bk_end > bk_start:
                windows.append((bk_start.replace(second=0, microsecond=0), bk_end.replace(second=0, microsecond=0)))
    return blocked, windows


def _is_team_multi_solo_service_context(org, service: Service):
    """Return (membership, other_solo_service_ids) if this is a team-plan multi-solo context."""
    try:
        if get_plan_slug(org) != TEAM_SLUG:
            return None, []
    except Exception:
        return None, []

    # Only applies to solo services (exactly one assignee)
    assignees = list(ServiceAssignment.objects.filter(service=service).select_related('membership'))
    if len(assignees) != 1:
        return None, []
    membership = getattr(assignees[0], 'membership', None)
    if not membership:
        return None, []

    # Member must have multiple solo services
    solo_service_ids = list(
        Service.objects.filter(organization=org, assignments__membership=membership)
        .annotate(num_assignees=Count('assignments'))
        .filter(num_assignees=1)
        .values_list('id', flat=True)
    )
    if len(solo_service_ids) < 2:
        return None, []

    others = [sid for sid in solo_service_ids if int(sid) != int(service.id)]
    return membership, others


def _per_date_override_scope_q(service: Optional[Service]):
    """Return a Q() filter limiting per-date overrides to the relevant scope.

    Per-date overrides are stored as Booking rows with service=NULL.

    Scopes:
    - Org-scoped: assigned_user IS NULL and NOT service-scoped marker.
    - Service-scoped: client_name == 'scope:svc:<service_id>'
    - Member-scoped: assigned_user == <single assignee user> (only when service has exactly 1 assignee)

    Rationale: calendar.html can create member- or service-scoped overrides. Availability checks
    must respect that scoping; otherwise an override for one service/member leaks to others.
    """
    q = Q(assigned_user__isnull=True) & ~Q(client_name__startswith='scope:svc:')

    if not service:
        return q

    # Service-scoped overrides for this specific service
    q = q | Q(client_name=f'scope:svc:{service.id}')

    # Member-scoped overrides apply to a service only when it has a single assignee.
    try:
        assigned = list(
            ServiceAssignment.objects.filter(service=service).select_related('membership__user')
        )
        users = []
        for a in assigned:
            try:
                u = getattr(getattr(a, 'membership', None), 'user', None)
                if u:
                    users.append(u)
            except Exception:
                continue
        # only apply member-scope overrides when there is exactly one assignee
        if len(users) == 1:
            q = q | Q(assigned_user=users[0])
    except Exception:
        pass

    return q


def booking_to_event(bk: Booking):
    # Distinguish per-date override bookings (service NULL) from real service bookings
    event = {
        'id': bk.id,
        'title': bk.title or ('Unavailable' if bk.is_blocking else 'Booking'),
        'start': bk.start.isoformat(),
        'end': bk.end.isoformat(),
        'extendedProps': {
            'client_name': bk.client_name,
            'client_email': bk.client_email,
            'is_blocking': bk.is_blocking,
            'payment_method': getattr(bk, 'payment_method', None),
            'payment_status': getattr(bk, 'payment_status', None),
            # Flag all overrides (service NULL) so frontend can reliably detect them after hard refresh
            'is_per_date': bk.service is None,
        }
    }

    # Include any scope metadata so frontend can filter per-date overrides by selected member/service
    try:
        if getattr(bk, 'assigned_user', None):
            event['extendedProps']['assigned_user_id'] = getattr(bk.assigned_user, 'id', None)
        # Legacy/service-scoped marker stored in client_name like 'scope:svc:<id>'
        if isinstance(bk.client_name, str) and bk.client_name.startswith('scope:svc:'):
            try:
                svc_id = int(bk.client_name.split(':', 2)[2])
                event['extendedProps']['assigned_scope_service_id'] = svc_id
            except Exception:
                pass
    except Exception:
        pass

    if bk.service is None:
        # Per-date override
        if bk.is_blocking:
            # Blocking override: show grey background
            event['display'] = 'background'
            event['color'] = '#e0e0e0'
            event['backgroundColor'] = '#e0e0e0'
            event['extendedProps']['override_type'] = 'blocked'
        else:
            # Availability override: show green background range
            event['display'] = 'background'
            event['color'] = '#d0f0d0'
            event['backgroundColor'] = '#d0f0d0'
            event['extendedProps']['override_type'] = 'available'

    # Include service metadata so admin/front-end can consider buffers
    try:
        if bk.service is not None:
            event['extendedProps']['service_slug'] = bk.service.slug
            event['extendedProps']['service_buffer_after'] = int(getattr(bk.service, 'buffer_after', 0))
            event['extendedProps']['service_allow_ends_after_availability'] = bool(getattr(bk.service, 'allow_ends_after_availability', False))
            # New per-service client settings
            event['extendedProps']['service_time_increment_minutes'] = int(getattr(bk.service, 'time_increment_minutes', 30))
            event['extendedProps']['service_use_fixed_increment'] = bool(getattr(bk.service, 'use_fixed_increment', False))
            event['extendedProps']['service_allow_squished_bookings'] = bool(getattr(bk.service, 'allow_squished_bookings', False))
    except Exception:
        # be resilient to any model quirks
        pass

    # Facility resource assignment (cage/room/etc) - Team plan only
    try:
        org = getattr(bk, 'organization', None)
        from billing.utils import can_use_resources
        if org and can_use_resources(org) and getattr(bk, 'resource_id', None):
            event['extendedProps']['resource_id'] = bk.resource_id
            event['extendedProps']['resource_name'] = getattr(getattr(bk, 'resource', None), 'name', None)
    except Exception:
        pass

    return event


def _service_resource_ids(service: Optional[Service]):
    """Return list of facility resource IDs allowed for this service."""
    if not service:
        return []
    # Team-plan only: treat as if no resources exist on other plans.
    try:
        org = getattr(service, 'organization', None)
        if org is not None:
            from billing.utils import can_use_resources
            if not can_use_resources(org):
                return []
    except Exception:
        # Be conservative: if billing cannot be evaluated, disable resources.
        return []
    try:
        return list(
            ServiceResource.objects.filter(service=service, resource__is_active=True)
            .values_list('resource_id', flat=True)
        )
    except Exception:
        return []


def _resource_overlaps_any_booking(org: Organization, start_dt, end_dt, service: Optional[Service], resource_id: int) -> bool:
    """True if the given resource is busy for the candidate window."""
    if not resource_id:
        return True
    # Reuse the overlap logic but scope the candidates to a specific resource.
    return _has_overlap(org, start_dt, end_dt, service=service, resource_id=int(resource_id))


def _find_available_resource_id(org: Organization, service: Service, start_dt, end_dt) -> Optional[int]:
    """Return an available resource_id for this service/slot, else None.

    If the service has no resource links configured, returns None.
    """
    resource_ids = _service_resource_ids(service)
    if not resource_ids:
        return None

    for rid in resource_ids:
        if not _resource_overlaps_any_booking(org, start_dt, end_dt, service=service, resource_id=rid):
            return int(rid)
    return None


def _validate_resource_for_service(org: Organization, service: Service, resource_id: Optional[int]) -> Optional[FacilityResource]:
    """Return FacilityResource if it belongs to org and is allowed for service (when configured)."""
    if not resource_id:
        return None

    try:
        res = FacilityResource.objects.filter(id=int(resource_id), organization=org, is_active=True).first()
    except Exception:
        return None
    if not res:
        return None

    allowed_ids = _service_resource_ids(service)
    # If no resource links configured, treat as "service does not use discrete resources".
    if not allowed_ids:
        return None

    return res if res.id in allowed_ids else None


def _anchors_for_date(org, service, day_date, org_tz, total_length):
    """Return a list of aware datetimes representing the valid booking anchors
    for a given organization, service and date (in org_tz). Anchors are spaced
    by `total_length` and are computed from the service or org weekly windows.
    """
    anchors = []
    from datetime import datetime

    # Determine base windows for that weekday
    weekday = day_date.weekday()
    try:
        if _trial_single_active_service(org):
            svc_rows = None
        else:
            svc_rows = service.weekly_availability.filter(is_active=True, weekday=weekday)
    except Exception:
        svc_rows = None
    base_windows = []
    if svc_rows and svc_rows.exists():
        for w in svc_rows.order_by('start_time'):
            w_start = datetime(day_date.year, day_date.month, day_date.day, w.start_time.hour, w.start_time.minute, tzinfo=org_tz)
            w_end = datetime(day_date.year, day_date.month, day_date.day, w.end_time.hour, w.end_time.minute, tzinfo=org_tz)
            base_windows.append((w_start, w_end))
    else:
        weekly_rows = WeeklyAvailability.objects.filter(organization=org, is_active=True, weekday=weekday)
        for w in weekly_rows:
            w_start = datetime(day_date.year, day_date.month, day_date.day, w.start_time.hour, w.start_time.minute, tzinfo=org_tz)
            w_end = datetime(day_date.year, day_date.month, day_date.day, w.end_time.hour, w.end_time.minute, tzinfo=org_tz)
            base_windows.append((w_start, w_end))

    # If no windows, nothing to return
    if not base_windows:
        return anchors

    for win_start, win_end in base_windows:
        slot_start = win_start.replace(second=0, microsecond=0)
        while slot_start + total_length <= win_end:
            anchors.append(slot_start)
            slot_start = slot_start + total_length

    return anchors


def _require_org_and_role(request, roles=("owner", "admin", "manager", "staff")):
    """
    Enforces:
    - request.organization exists
    - user has active Membership in that org
    - user has one of allowed roles
    Returns: (org, error_response_or_None)
    """
    org = getattr(request, "organization", None)
    if not org:
        return None, HttpResponseForbidden("No organization on request.")

    if not user_has_role(request.user, org, roles):
        return None, HttpResponseForbidden("You don’t have access to this organization.")

    return org, None


def _has_overlap(org, start_dt, end_dt, service=None, resource_id: Optional[int] = None):
    """
    Prevent overlapping bookings inside the same organization.
    If `service` is provided, take its `buffer_before` and `buffer_after` into account
    for the proposed booking window. This matches the slot generation logic which
    treats the new booking's buffers as part of the conflict window.

    Overlap rule (with buffers):
      existing.start < proposed_end AND existing.end > proposed_start
    where proposed_start = start_dt - buffer_before, proposed_end = end_dt + buffer_after
    """
    # If a service is provided, expand the proposed window by its AFTER-buffer only.
    # We intentionally no longer apply a `buffer_before` for overlap prevention.
    if service is not None:
        try:
            buf_after = int(getattr(service, 'buffer_after', 0))
        except Exception:
            buf_after = 0
        from datetime import timedelta
        proposed_start = start_dt
        proposed_end = end_dt + timedelta(minutes=buf_after)
    else:
        proposed_start = start_dt
        proposed_end = end_dt

    # Fetch candidate bookings and evaluate conflicts explicitly so we can
    # apply the AFTER-buffer on existing bookings (not as part of the
    # proposed booking window).
    from datetime import timedelta
    try:
        buf_after = int(getattr(service, 'buffer_after', 0)) if service is not None else 0
    except Exception:
        buf_after = 0

    # Narrow DB query to likely-relevant bookings: any existing booking that
    # starts before the candidate's end plus the AFTER-buffer might matter.
    from django.utils import timezone as dj_tz
    # Normalize candidate times to UTC where possible
    try:
        start_utc = start_dt.astimezone(dj_tz.utc)
    except Exception:
        start_utc = start_dt
    try:
        end_utc = end_dt.astimezone(dj_tz.utc)
    except Exception:
        end_utc = end_dt

    buf_after_td = timedelta(minutes=buf_after)
    # Also compute UTC-projected proposed end so buffer-aware checks use the
    # expanded candidate window when comparing against existing bookings.
    try:
        proposed_end_utc = proposed_end.astimezone(dj_tz.utc)
    except Exception:
        proposed_end_utc = proposed_end
    candidate_qs = Booking.objects.filter(
        organization=org,
        is_blocking=False,
        start__lt=end_utc + buf_after_td,
    )

    # When a resource is specified, only treat that resource as conflicting.
    # This enables discrete facility booking (cage/room) where different resources
    # can be booked concurrently.
    if resource_id is not None:
        candidate_qs = candidate_qs.filter(resource_id=int(resource_id))

    for b in candidate_qs:
        # Normalize booking times to UTC if possible
        try:
            b_start = b.start.astimezone(dj_tz.utc)
        except Exception:
            b_start = b.start
        try:
            b_end = b.end.astimezone(dj_tz.utc)
        except Exception:
            b_end = b.end

        # 1) Direct overlap (use proposed_end including the candidate's after-buffer)
        if (b_start < proposed_end_utc) and (b_end > start_utc):
            return True

        # 2) Existing booking AFTER-buffer: if candidate starts in that window
        # (existing booking's AFTER-buffer prevents a candidate starting too soon)
        if start_utc >= b_end and start_utc < (b_end + buf_after_td):
            return True

    return False


def is_within_weekly_availability(org, start_dt, end_dt):
    """Weekly window only (legacy helper)."""
    any_rows = WeeklyAvailability.objects.filter(organization=org, is_active=True).exists()
    if not any_rows:
        return True
    windows = WeeklyAvailability.objects.filter(
        organization=org,
        is_active=True,
        weekday=start_dt.weekday(),
    )
    if not windows.exists():
        return False
    start_t = start_dt.time()
    end_t = end_dt.time()
    for w in windows:
        if w.start_time <= start_t and end_t <= w.end_time:
            return True
    return False


def cancel_booking(request, booking_id):
    """Cancel a booking using a signed token provided in the email.

    Expects query param `token` which is a signed value of the booking_id.
    Tokens are time-limited to 7 days.
    """
    token = request.GET.get("token")
    if not token:
        return render(request, "bookings/booking_cancel_invalid.html", status=400)

    signer = TimestampSigner()
    try:
        unsigned = signer.unsign(token, max_age=60*60*24*7)  # 7 days
        if str(unsigned) != str(booking_id):
            return render(request, "bookings/booking_cancel_invalid.html", status=400)
    except SignatureExpired:
        return render(request, "bookings/booking_cancel_token_expired.html", status=400)
    except BadSignature:
        return render(request, "bookings/booking_cancel_invalid.html", status=400)

    booking = get_object_or_404(Booking, id=booking_id)
    # Prevent cancelling blocking events
    if booking.is_blocking:
        return render(request, "bookings/booking_cancel_invalid.html", status=400)

    org_slug = booking.organization.slug
    service_slug = booking.service.slug if booking.service else None
    # Decide refund eligibility based on the service's policy
    refund_info = None
    if booking.service:
        svc = booking.service
        if svc.refunds_allowed:
            # If within cutoff window, no refund
            try:
                org_tz = ZoneInfo(getattr(booking.organization, 'timezone', getattr(settings, 'TIME_ZONE', 'UTC')))
            except Exception:
                org_tz = timezone.get_current_timezone()
            now_local = timezone.now().astimezone(org_tz)
            start_local = booking.start.astimezone(org_tz)
            hours_until = (start_local - now_local).total_seconds() / 3600.0
            if hours_until >= svc.refund_cutoff_hours:
                # Placeholder: integrate with Stripe to issue refund if payment exists
                refund_info = "Refund eligible"
            else:
                refund_info = f"No refund (within {svc.refund_cutoff_hours}h cutoff)"
        else:
            refund_info = "No refund (service policy)"

    # If the request is GET, render a confirmation page giving the client
    # the option to proceed or keep the appointment. The confirmation form
    # will POST back to this same URL with the same token.
    if request.method == 'GET':
        return render(request, "bookings/booking_cancel_confirm.html", {
            "org": booking.organization,
            "service": booking.service,
            "booking": booking,
            "refund_info": refund_info,
            "token": token,
        })

    # POST -> perform the cancellation
    if request.method == 'POST':
        # Mark this deletion as a client-initiated cancellation so audit records
        # can reflect 'cancelled' instead of 'deleted'. The post-delete signal
        # will read this attribute when creating the AuditBooking.
        try:
            setattr(booking, '_audit_event_type', 'cancelled')
        except Exception:
            pass

        # Send cancellation email (include refund_info in context) and then delete
        try:
            from .emails import send_booking_cancellation
            # send_booking_cancellation will return False on failure but we don't block
            send_booking_cancellation(booking, refund_info=refund_info)
        except Exception:
            pass

        try:
            booking.delete()
        except Exception:
            # If delete fails for DB reasons, still render the page with refund_info
            pass

        return render(request, "bookings/booking_cancelled.html", {
            "org_slug": org_slug,
            "service_slug": service_slug,
            "refund_info": refund_info,
        })


def is_within_availability(org, start_dt, end_dt, service=None):
    """Composite availability check including per-date overrides.

    Precedence:
    1. Per-date blocking override (is_blocking & service is NULL) covering slot => unavailable.
    2. Per-date availability override (non-blocking & service NULL) containing slot => available.
    3. Fallback to weekly availability windows.
    4. If no weekly rows at all => available (legacy behavior).
    """
    # Normalize incoming datetimes to the organization's timezone when naive
    try:
        org_tz = ZoneInfo(getattr(org, 'timezone', getattr(settings, 'TIME_ZONE', 'UTC')))
    except Exception:
        org_tz = timezone.get_current_timezone()
    if timezone.is_naive(start_dt):
        start_dt = make_aware(start_dt, org_tz)

    # Normalize seconds/microseconds to align with anchor generation
    start_dt = start_dt.replace(second=0, microsecond=0)
    if timezone.is_naive(end_dt):
        end_dt = make_aware(end_dt, org_tz)

    # Query per-date overrides for that calendar day (service NULL so they are not actual client bookings)
    # Scoped so overrides only affect the intended member/service.
    day_start = start_dt.astimezone(org_tz).replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    override_qs = Booking.objects.filter(
        organization=org,
        service__isnull=True,
        start__lt=day_end,
        end__gt=day_start,
    ).filter(_per_date_override_scope_q(service))

    # Partition overrides
    blocking_windows = []
    avail_windows = []
    for bk in override_qs:
        if bk.is_blocking:
            blocking_windows.append((bk.start, bk.end))
        else:
            avail_windows.append((bk.start, bk.end))

    # 1. Blocking override wins immediately
    try:
        org_tz = ZoneInfo(getattr(org, 'timezone', getattr(settings, 'TIME_ZONE', 'UTC')))
    except Exception:
        org_tz = timezone.get_current_timezone()

    for bs, be in blocking_windows:
        if timezone.is_naive(bs):
            bs = make_aware(bs, org_tz)
        if timezone.is_naive(be):
            be = make_aware(be, org_tz)
        if bs <= start_dt and end_dt <= be:
            return False

    # 2. Availability override allows slot
    for avs, ave in avail_windows:
        if timezone.is_naive(avs):
            avs = make_aware(avs, org_tz)
        if timezone.is_naive(ave):
            ave = make_aware(ave, org_tz)
        # If service explicitly allows ending after availability, permit slots
        # that start within an availability override even if their end extends past it.
        if avs <= start_dt and (end_dt <= ave or (service and getattr(service, 'allow_ends_after_availability', False) and start_dt < ave)):
            return True

    # 3. Fall back to weekly windows logic
    # If a per-date ServiceSettingFreeze contains a weekly window snapshot for this
    # date, prefer it. This preserves booked dates when weekly availability is
    # edited later.
    if service is not None:
        try:
            freeze = _active_service_freeze_for_date(org, service, start_dt.astimezone(org_tz).date(), org_tz)
        except Exception:
            freeze = None

        if freeze and isinstance(getattr(freeze, 'frozen_settings', None), dict) and freeze.frozen_settings.get('weekly_windows'):
            start_t = start_dt.time()
            end_t = end_dt.time()
            allow_ends_after = bool(freeze.frozen_settings.get(
                'allow_ends_after_availability', getattr(service, 'allow_ends_after_availability', False)
            ))

            for w in freeze.frozen_settings.get('weekly_windows', []):
                try:
                    st = str(w.get('start', ''))
                    et = str(w.get('end', ''))
                    if len(st) != 5 or len(et) != 5 or st[2] != ':' or et[2] != ':':
                        continue
                    st_h, st_m = (int(x) for x in st.split(':'))
                    et_h, et_m = (int(x) for x in et.split(':'))
                    w_start = datetime(2000, 1, 1, st_h, st_m).time()
                    w_end = datetime(2000, 1, 1, et_h, et_m).time()
                except Exception:
                    continue

                if allow_ends_after:
                    if w_start <= start_t and start_t < w_end:
                        return True
                else:
                    if w_start <= start_t and end_t <= w_end:
                        return True
            return False

        # A service can be explicitly scoped either because it has explicit
        # service-weekly rows, OR because it is a service type that must have its
        # own schedule (unassigned/shared/partitioned solo). In that case, days
        # with no service rows should be unavailable (do NOT fall back to org weekly).
        try:
            if _trial_single_active_service(org):
                svc_has_any = False
            else:
                svc_has_any = service.weekly_availability.filter(is_active=True).exists()
        except Exception:
            svc_has_any = False

        try:
            svc_requires_explicit = _service_requires_explicit_weekly(org, service)
        except Exception:
            svc_requires_explicit = False

        svc_is_scoped = bool(svc_has_any or svc_requires_explicit)

        try:
            if _trial_single_active_service(org):
                svc_rows = None
            else:
                svc_rows = service.weekly_availability.filter(is_active=True, weekday=start_dt.weekday())
        except Exception:
            svc_rows = None

        if svc_is_scoped:
            if not (svc_rows and svc_rows.exists()):
                return False

            # Check if any service window fully contains the slot
            start_t = start_dt.time()
            end_t = end_dt.time()
            for w in svc_rows:
                # If service allows ending after availability, only require the slot START
                # to be within the service window (start >= w.start_time and start < w.end_time).
                if getattr(service, 'allow_ends_after_availability', False):
                    if w.start_time <= start_t and start_t < w.end_time:
                        return True
                else:
                    if w.start_time <= start_t and end_t <= w.end_time:
                        return True
            return False

    # Fallback to organization-wide weekly availability. If the service allows
    # ending after availability, relax the requirement to only ensure the slot
    # START is within a weekly window; otherwise require the slot END to also be within.
    any_rows = WeeklyAvailability.objects.filter(organization=org, is_active=True).exists()
    if not any_rows:
        return True
    windows = WeeklyAvailability.objects.filter(
        organization=org,
        is_active=True,
        weekday=start_dt.weekday(),
    )
    if not windows.exists():
        return False
    start_t = start_dt.time()
    end_t = end_dt.time()
    for w in windows:
        if getattr(service, 'allow_ends_after_availability', False):
            if w.start_time <= start_t and start_t < w.end_time:
                return True
        else:
            if w.start_time <= start_t and end_t <= w.end_time:
                return True
    return False

@csrf_exempt
@require_http_methods(['GET'])
@require_roles(['owner', 'admin', 'manager', 'staff'])
def events(request, org_slug):
    """Return org-scoped bookings as FullCalendar events (JSON)."""
    org, err = _require_org_and_role(request)
    if err:
        return err

    qs = Booking.objects.filter(organization=org)
    
    # Helper to parse incoming range params into the organization's timezone
    def _parse_to_org_tz(param: str, org_tz: ZoneInfo):
        if not param:
            return None
        s = param.replace('Z', '+00:00')  # allow simple Z format
        try:
            dt = datetime.fromisoformat(s)
        except Exception:
            return None
        if dt.tzinfo is None:
            # Interpret naive value as an org-local time
            dt = make_aware(dt, org_tz)
        else:
            dt = dt.astimezone(org_tz)
        return dt

    # Filter by date range if provided (interpreted in the organization's timezone)
    start_param = request.GET.get('start')
    end_param = request.GET.get('end')
    if start_param and end_param:
        try:
            org_tz = ZoneInfo(getattr(org, 'timezone', getattr(settings, 'TIME_ZONE', 'UTC')))
        except Exception:
            org_tz = ZoneInfo(getattr(settings, 'TIME_ZONE', 'UTC'))

        range_start = _parse_to_org_tz(start_param, org_tz)
        range_end = _parse_to_org_tz(end_param, org_tz)
        if range_start and range_end:
            qs = qs.filter(start__lt=range_end, end__gt=range_start)
    
    events = [booking_to_event(b) for b in qs]
    return JsonResponse(events, safe=False)

@csrf_exempt
@require_http_methods(['POST'])
@require_roles(['owner', 'admin', 'manager', 'staff'])
def create_booking(request, org_slug):
    """Create a booking with service-based rules (duration, buffers, notice limits)."""
    org, err = _require_org_and_role(request)
    if err:
        return err

    
    
    # -----------------------------
    # 1. Parse JSON
    # -----------------------------
    try:
        data = json.loads(request.body.decode('utf-8'))
    except Exception:
        return HttpResponseBadRequest('Invalid JSON')

    service_id = data.get("service_id")
    if not service_id:
        return HttpResponseBadRequest("`service_id` is required")

    # Load service & validate org ownership
    try:
        service = Service.objects.get(id=service_id, organization=org)
    except Service.DoesNotExist:
        return HttpResponseBadRequest("Invalid service_id")

    start_raw = data.get('start')
    if not start_raw:
        return HttpResponseBadRequest('`start` is required')

    # -----------------------------
    # 2. Parse start datetime
    # -----------------------------
    try:
        start_dt = datetime.fromisoformat(start_raw)
    except Exception:
        try:
            start_dt = datetime.fromisoformat(start_raw + 'T00:00:00')
        except Exception:
            return HttpResponseBadRequest('Invalid start datetime')

    # Make timezone-aware using organization's timezone for public bookings
    try:
        org_tz = ZoneInfo(getattr(org, 'timezone', getattr(settings, 'TIME_ZONE', 'UTC')))
    except Exception:
        org_tz = timezone.get_current_timezone()
    if timezone.is_naive(start_dt):
        start_dt = make_aware(start_dt, org_tz)

    # -----------------------------
    # 3. Apply service rules
    # -----------------------------
    # Calculate end time using service duration only (buffers are not stored on the
    # booking record; they are enforced during overlap checks and slot generation).
    end_dt = start_dt + timedelta(minutes=service.duration)

    now = timezone.now()

    # Min notice (Calendly-style)
    if start_dt < now + timedelta(hours=service.min_notice_hours):
        return HttpResponseBadRequest("Booking violates min notice time.")

    # Max booking window
    if start_dt > now + timedelta(days=service.max_booking_days):
        return HttpResponseBadRequest("Booking too far in the future.")

    # -----------------------------
    # 4. Overlap prevention
    # -----------------------------
    # Use your existing overlap logic
    # Enforce that requested start is aligned to service anchors (duration + buffers)
        # Do NOT require client-provided start times to align to internal anchors.
        # The public availability endpoint controls UI increments. Here we only
        # enforce that the requested booking doesn't overlap existing bookings
        # when considering the service's buffers.

    # Resource selection/validation (optional)
    requested_resource_id = data.get('resource_id')
    selected_resource = None
    selected_resource_id = None
    # If the service is configured with facility resources, either validate the requested
    # resource or auto-assign an available one.
    svc_resource_ids = _service_resource_ids(service)
    if svc_resource_ids:
        if requested_resource_id is not None:
            selected_resource = _validate_resource_for_service(org, service, requested_resource_id)
            if not selected_resource:
                return HttpResponseBadRequest('Invalid or unavailable resource for this service.')
            selected_resource_id = selected_resource.id
        else:
            selected_resource_id = _find_available_resource_id(org, service, start_dt, end_dt)
            if not selected_resource_id:
                return HttpResponseBadRequest('No facility resources are available for that time slot.')

    # Overlap check (buffer-aware when `service` provided). If this service uses
    # discrete resources, scope overlap checks to the selected resource.
    overlap_result = _has_overlap(org, start_dt, end_dt, service=service, resource_id=selected_resource_id)
    squish_warning = None
    if overlap_result:
        # If the service allows 'squished' bookings, permit creation but add a non-blocking warning
        if getattr(service, 'allow_squished_bookings', False):
            squish_warning = 'slot_violates_buffer'
        else:
            return HttpResponseBadRequest("Time slot overlaps an existing booking.")

    # -----------------------------
    # -----------------------------
    # 4b. Weekly availability enforcement (if windows defined)
    # -----------------------------
    # Availability enforcement (weekly + per-date overrides)
    if not is_within_availability(org, start_dt, end_dt, service):
        return HttpResponseBadRequest("Outside available hours.")

    # -----------------------------
    # 5. Create booking
    # -----------------------------
    bk = Booking.objects.create(
        organization=org,
        service=service,
        title=service.name,  # title = service name
        start=start_dt,
        end=end_dt,
        client_name=data.get('client_name', ''),
        client_email=data.get('client_email', ''),
        is_blocking=False,   # service bookings are never "blocking" events
        resource_id=selected_resource_id,
    )
    resp = {
        'status': 'ok',
        'id': bk.id,
        'event': booking_to_event(bk)
    }
    if squish_warning:
        resp['warning'] = squish_warning
        # Notify owner about squished booking (non-blocking warning)
        try:
            owner = getattr(org, 'owner', None)
            if owner and owner.email:
                send_mail(
                    subject=f"Booking created that violates buffers for {service.name}",
                    message=f"A booking was created on {bk.start.astimezone(timezone.get_default_timezone()).isoformat()} for service {service.name} which does not conform to the configured buffer rules.",
                    from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None),
                    recipient_list=[owner.email],
                    fail_silently=True,
                )
        except Exception:
            pass
    return JsonResponse(resp)


@csrf_exempt
@require_http_methods(['POST'])
@require_roles(['owner', 'admin', 'manager'])
def batch_create(request, org_slug):
    """Create many bookings for an array of dates (org-scoped, role-protected)."""
    org, err = _require_org_and_role(request)
    if err:
        return err

    if not _can_use_per_date_overrides(org):
        return JsonResponse({'error': 'Per-date overrides are available on the Pro or Team plan.'}, status=403)

    try:
        data = json.loads(request.body.decode('utf-8'))
    except Exception:
        return HttpResponseBadRequest('Invalid JSON')

    dates = data.get('dates') or []
    start_time = data.get('start_time')
    end_time = data.get('end_time')
    if not dates or not start_time or not end_time:
        return HttpResponseBadRequest('`dates`, `start_time`, and `end_time` are required')

    created = []
    # Use organization's configured timezone for per-date overrides so public calendar matches
    try:
        tz = ZoneInfo(getattr(org, 'timezone', getattr(settings, 'TIME_ZONE', 'UTC')))
    except Exception:
        tz = timezone.get_current_timezone()

    target = data.get('target') if isinstance(data, dict) else None
    is_blocking = bool(data.get('is_blocking', False))

    # If this is a service-scoped per-date "available" override in a team multi-solo context,
    # enforce that it does not bypass the weekly partitioning rules.
    target_service = None
    team_multi_solo_membership = None
    other_solo_service_ids = []
    if target and isinstance(target, str) and target.startswith('svc:') and not is_blocking:
        try:
            svc_id = int(str(target).split(':', 1)[1])
            target_service = Service.objects.filter(id=svc_id, organization=org).first()
        except Exception:
            target_service = None

        if target_service:
            team_multi_solo_membership, other_solo_service_ids = _is_team_multi_solo_service_context(org, target_service)

    for d in dates:
        dobj = parse_date(d)
        if not dobj:
            continue

        try:
            s = datetime.combine(dobj, datetime.strptime(start_time, '%H:%M').time())
            e = datetime.combine(dobj, datetime.strptime(end_time, '%H:%M').time())
        except Exception:
            continue

        s = make_aware(s, tz) if timezone.is_naive(s) else s
        e = make_aware(e, tz) if timezone.is_naive(e) else e

        if e <= s:
            continue

        # Guardrails for team + multi-solo services: service-scoped "available" overrides
        # cannot create overlapping availability across services or expand beyond weekly partitions.
        if target_service and team_multi_solo_membership and other_solo_service_ids:
            # Require explicit service weekly windows; do not allow per-date overrides
            # to create availability outside the service's partition.
            if not _service_has_explicit_weekly(target_service):
                return HttpResponseBadRequest(
                    "This service inherits member availability. Set service weekly availability first to make room before using per-date overrides."
                )

            # Must fit within the service's weekly windows for that weekday
            svc_windows = _service_weekly_windows_for_date(target_service, dobj, tz)
            if not svc_windows or not _interval_is_within_any_window(s, e, svc_windows):
                return HttpResponseBadRequest(
                    "Per-date availability must be within this service's weekly availability. Update service weekly availability first to make room."
                )

            # Must also fit within the member's overall weekly availability
            mem_windows = _member_weekly_windows_for_date(org, team_multi_solo_membership, dobj, tz)
            if mem_windows and not _interval_is_within_any_window(s, e, mem_windows):
                return HttpResponseBadRequest(
                    "Per-date availability must be within the assigned member's weekly availability. Update member availability first."
                )

            # Must not overlap any other solo service's effective availability for that date.
            for other_id in other_solo_service_ids:
                other_service = Service.objects.filter(id=int(other_id), organization=org).first()
                if not other_service:
                    continue

                # If the other service is blocked for the whole day, it frees the day.
                other_blocked, other_override_windows = _service_scoped_per_date_windows(org, int(other_id), dobj, tz)
                if other_blocked:
                    continue

                # Effective windows for overlap check:
                # - prefer per-date availability overrides if present
                # - else prefer other service weekly windows if defined
                # - else treat as inheriting member availability (occupies all, unless blocked)
                if other_override_windows:
                    other_windows = other_override_windows
                else:
                    other_weekly = _service_weekly_windows_for_date(other_service, dobj, tz)
                    if other_weekly:
                        other_windows = other_weekly
                    else:
                        other_windows = mem_windows

                for ws, we in (other_windows or []):
                    if _intervals_overlap(s, e, ws, we):
                        other_label = getattr(other_service, 'name', None) or f"service {other_id}"
                        return HttpResponseBadRequest(
                            f"Per-date availability overlaps {other_label}. Override that service to unavailable for this day (or adjust weekly availability first) to make room."
                        )

        # DON'T delete all existing overrides - allow multiple time ranges per date.
        # Only skip exact duplicates *within the same scope* (target).
        dup_qs = Booking.objects.filter(
            organization=org,
            start=s,
            end=e,
            service__isnull=True
        )
        try:
            if target:
                if isinstance(target, str) and target.startswith('svc:'):
                    try:
                        svc_id = int(str(target).split(':', 1)[1])
                        dup_qs = dup_qs.filter(client_name=f'scope:svc:{svc_id}')
                    except Exception:
                        pass
                else:
                    try:
                        mid = int(target)
                        mem = Membership.objects.filter(id=mid).first()
                        if mem and getattr(mem, 'user', None):
                            dup_qs = dup_qs.filter(assigned_user=mem.user)
                    except Exception:
                        pass
        except Exception:
            pass

        existing_duplicate = dup_qs.exists()
        
        if existing_duplicate:
            # Skip creating duplicate
            continue

        # Per-date overrides (service NULL) are schedule annotations, not bookings.
        # They must be allowed to overlap existing bookings.

        # Scope the override to the provided target (membership id or svc:<id>)
        create_kwargs = {
            'organization': org,
            'title': data.get('title', ''),
            'start': s,
            'end': e,
            'client_name': data.get('client_name', ''),
            'client_email': data.get('client_email', ''),
            'is_blocking': bool(data.get('is_blocking', False)),
            'service': None
        }
        try:
            if target:
                # Service-scoped: encode into client_name marker
                if isinstance(target, str) and target.startswith('svc:'):
                    try:
                        svc_id = int(str(target).split(':', 1)[1])
                        create_kwargs['client_name'] = f'scope:svc:{svc_id}'
                    except Exception:
                        pass
                else:
                    # Membership target (membership id) -> assign to that membership's user
                    try:
                        mid = int(target)
                        mem = Membership.objects.filter(id=mid).first()
                        if mem and getattr(mem, 'user', None):
                            create_kwargs['assigned_user'] = mem.user
                    except Exception:
                        pass
        except Exception:
            pass
        try:
            bk = Booking.objects.create(**create_kwargs)
        except Exception as e:
            continue
        created.append(booking_to_event(bk))

    return JsonResponse({'status': 'ok', 'created': created})


@csrf_exempt
@require_http_methods(['POST'])
@require_roles(['owner', 'admin', 'manager'])
def batch_delete(request, org_slug):
    """Delete bookings that fall on provided dates (org-scoped, role-protected)."""
    org, err = _require_org_and_role(request)
    if err:
        return err

    if not _can_use_per_date_overrides(org):
        return JsonResponse({'error': 'Per-date overrides are available on the Pro or Team plan.'}, status=403)

    try:
        data = json.loads(request.body.decode('utf-8'))
    except Exception:
        return HttpResponseBadRequest('Invalid JSON')

    dates = data.get('dates') or []
    if not dates:
        return HttpResponseBadRequest('`dates` required')

    deleted = 0
    # Delete only per-date overrides (service is NULL) that overlap the given day in org timezone
    try:
        org_tz = ZoneInfo(getattr(org, 'timezone', getattr(settings, 'TIME_ZONE', 'UTC')))
    except Exception:
        org_tz = ZoneInfo(getattr(settings, 'TIME_ZONE', 'UTC'))

    for d in dates:
        dobj = parse_date(d)
        if not dobj:
            continue

        day_start = datetime(dobj.year, dobj.month, dobj.day, 0, 0, 0, tzinfo=org_tz)
        day_end = datetime(dobj.year, dobj.month, dobj.day, 23, 59, 59, tzinfo=org_tz)

        qs = Booking.objects.filter(
            organization=org,
            service__isnull=True,
            start__lt=day_end,
            end__gt=day_start,
        )
        # If a target was provided, narrow deletions to that scope only
        try:
            target = data.get('target') if isinstance(data, dict) else None
            if target:
                if isinstance(target, str) and target.startswith('svc:'):
                    try:
                        svc_id = int(str(target).split(':', 1)[1])
                        qs = qs.filter(client_name__startswith=f'scope:svc:{svc_id}')
                    except Exception:
                        pass
                else:
                    try:
                        mid = int(target)
                        mem = Membership.objects.filter(id=mid).first()
                        if mem and getattr(mem, 'user', None):
                            qs = qs.filter(assigned_user=mem.user)
                    except Exception:
                        pass
        except Exception:
            pass
        # These are per-date overrides (service NULL), not customer bookings.
        # Use a raw delete to avoid cancellation emails / audit trail entries.
        try:
            deleted += qs._raw_delete(qs.db)
        except Exception:
            # Fallback: standard delete (may emit signals)
            deleted += qs.count()
            qs.delete()

    return JsonResponse({'status': 'ok', 'deleted': deleted})




def public_org_page(request, org_slug):
    org = get_object_or_404(Organization, slug=org_slug)
    services = org.services.filter(is_active=True)
    return render(request, "public/public_org_page.html", {"org": org, "services": services})


def _build_public_service_page_context(
    request,
    *,
    org: Organization,
    services,
    service: Service,
    show_with_line: bool,
    offline_methods_allowed: bool,
    offline_methods: list,
    offline_instructions: str,
):
    """Build template context for the public service booking page."""

    # GET - add trial context for banner
    subscription = get_subscription(org)
    trialing_active = False
    trial_end_date = None
    if subscription and subscription.status == 'trialing' and subscription.trial_end:
        now = timezone.now()
        if subscription.trial_end > now:
            trialing_active = True
            trial_end_date = subscription.trial_end

    # Attach assigned member display names to services for client UI (Team plan only)
    if show_with_line:
        try:
            from .models import ServiceAssignment
            ass_qs = ServiceAssignment.objects.filter(service__in=services).select_related('membership__user__profile', 'service', 'membership__user')
            ass_map = {}
            for sa in ass_qs:
                sslug = sa.service.slug
                user = getattr(sa.membership, 'user', None)
                name = ''
                try:
                    profile = getattr(user, 'profile', None)
                    if profile and getattr(profile, 'display_name', None):
                        name = profile.display_name
                    else:
                        full = user.get_full_name() if getattr(user, 'get_full_name', None) else ''
                        name = full if full else getattr(user, 'email', '')
                except Exception:
                    name = getattr(user, 'email', '')
                ass_map.setdefault(sslug, []).append(name)
            for s in services:
                s.assigned_names = ', '.join(ass_map.get(s.slug, []))
            service.assigned_names = ', '.join(ass_map.get(service.slug, []))
        except Exception:
            for s in services:
                s.assigned_names = ''
            service.assigned_names = ''
    else:
        for s in services:
            s.assigned_names = ''
        service.assigned_names = ''

    # Provide per-service EFFECTIVE weekly availability (UI index 0=Sun..6=Sat) as JSON.
    any_org_rows = WeeklyAvailability.objects.filter(organization=org, is_active=True).exists()
    service_weekly_map = {}
    for s in services:
        try:
            if _trial_single_active_service(org):
                svc_has_any = False
            else:
                svc_has_any = s.weekly_availability.filter(is_active=True).exists()
        except Exception:
            svc_has_any = False

        try:
            svc_requires_explicit = _service_requires_explicit_weekly(org, s)
        except Exception:
            svc_requires_explicit = False

        svc_is_scoped = bool(svc_has_any or svc_requires_explicit)
        per_day = []
        for ui in range(7):
            model_wd = (ui - 1) % 7  # convert UI 0=Sun..6=Sat -> model 0=Mon..6=Sun
            if _trial_single_active_service(org):
                svc_rows = []
            else:
                svc_rows = list(s.weekly_availability.filter(is_active=True, weekday=model_wd).order_by('start_time'))
            if svc_rows:
                per_day.append([f"{r.start_time.strftime('%H:%M')}-{r.end_time.strftime('%H:%M')}" for r in svc_rows])
            else:
                if svc_is_scoped:
                    per_day.append([])
                else:
                    if any_org_rows:
                        org_rows = WeeklyAvailability.objects.filter(
                            organization=org,
                            is_active=True,
                            weekday=model_wd,
                        ).order_by('start_time')
                        per_day.append([f"{r.start_time.strftime('%H:%M')}-{r.end_time.strftime('%H:%M')}" for r in org_rows])
                    else:
                        per_day.append(['00:00-23:59'])
        service_weekly_map[s.slug] = per_day

    return {
        "org": org,
        "services": services,
        "service": service,
        "trialing_active": trialing_active,
        "trial_end_date": trial_end_date,
        "show_with_line": show_with_line,
        "offline_methods_allowed": offline_methods_allowed,
        "offline_methods": offline_methods,
        "offline_instructions": offline_instructions,
        "service_weekly_map_json": json.dumps(service_weekly_map),
        # Support reschedule GET params to prefill client info and attach reschedule metadata
        'reschedule_source': request.GET.get('reschedule_source') or request.GET.get('source'),
        'reschedule_token': request.GET.get('reschedule_token') or request.GET.get('token'),
        'prefill_client_name': request.GET.get('client_name') or '',
        'prefill_client_email': request.GET.get('client_email') or '',
    }





@require_http_methods(['GET', 'POST'])
def public_service_page(request, org_slug, service_slug):
    """
    Public booking page for a single service.
    GET  -> show calendar + booking modal
    POST -> create a Booking for the selected time
    """
    org = get_object_or_404(Organization, slug=org_slug)
    services = Service.objects.filter(organization=org, is_active=True).order_by("name")
    # Inactive services should not be bookable or selectable on the public page.
    service = Service.objects.filter(slug=service_slug, organization=org, is_active=True).first()
    if not service:
        return redirect(reverse('bookings:public_org_page', args=[org.slug]))

    show_with_line = (get_plan_slug(org) == TEAM_SLUG)

    offline_methods_allowed = can_use_offline_payment_methods(org)
    try:
        org_settings = getattr(org, 'settings', None)
    except Exception:
        org_settings = None
    offline_methods = []
    offline_instructions = ''
    try:
        if org_settings:
            offline_methods = list(getattr(org_settings, 'offline_payment_methods', []) or [])
            offline_instructions = build_offline_payment_instructions(org_settings)
    except Exception:
        offline_methods = []
        offline_instructions = ''

    # In this deployment, business users manage offline method acceptance per service.
    # If offline methods are plan-enabled but org-level settings are empty, fall back
    # to a standard set so the UI/validation behaves predictably.
    if offline_methods_allowed and (not offline_methods):
        offline_methods = ['cash', 'venmo', 'zelle']

    def _effective_offline_methods_for_service(svc):
        """Return the effective list of offline methods allowed for a specific service.

        In this deployment, offline method acceptance is configured per-service.

        - If svc.allowed_offline_payment_methods is None: treat as none (no inheritance)
        - If []: none
        - Else: subset of org settings (for safety)
        """
        try:
            svc_methods = getattr(svc, 'allowed_offline_payment_methods', None)
        except Exception:
            svc_methods = None

        try:
            desired = list(svc_methods or [])
        except Exception:
            desired = []

        # Safety: never allow a method that's not enabled at the org level.
        org_set = set([str(x).strip().lower() for x in (offline_methods or []) if str(x).strip()])
        cleaned = []
        for x in desired:
            v = str(x).strip().lower()
            if v and v in org_set:
                cleaned.append(v)
        return cleaned

    def _offline_methods_label(methods):
        pretty = {
            'cash': 'cash',
            'venmo': 'Venmo',
            'zelle': 'Zelle',
        }
        parts = [pretty.get(m, m) for m in (methods or [])]
        if not parts:
            return ''
        return '/'.join(parts)

    if request.method == "POST":
        client_name = request.POST.get("client_name")
        client_email = request.POST.get("client_email")
        start_str = request.POST.get("start")
        end_str = request.POST.get("end")
        # Allow selecting a different service from the modal
        posted_service_slug = request.POST.get("service_slug")
        if posted_service_slug and posted_service_slug != service_slug:
            service = Service.objects.filter(slug=posted_service_slug, organization=org, is_active=True).first()
            if not service:
                return HttpResponseBadRequest("Service is not available")

        if not all([client_name, client_email, start_str, end_str]):
            return HttpResponseBadRequest("Missing required fields")

        start = parse_datetime(start_str)
        end = parse_datetime(end_str)
        # Interpret naive datetimes in the organization's timezone
        try:
            org_tz = ZoneInfo(getattr(org, 'timezone', getattr(settings, 'TIME_ZONE', 'UTC')))
        except Exception:
            org_tz = timezone.get_current_timezone()
        if timezone.is_naive(start):
            start = make_aware(start, org_tz)
        else:
            start = start.astimezone(org_tz)
        if timezone.is_naive(end):
            end = make_aware(end, org_tz)
        else:
            end = end.astimezone(org_tz)

        # Double-check there's still no conflict (exclude per-date overrides)
        conflict = Booking.objects.filter(
            organization=org,
            start__lt=end,
            end__gt=start,
            service__isnull=False  # only check real service bookings
        ).exists()
        if conflict:
            ctx = _build_public_service_page_context(
                request,
                org=org,
                services=services,
                service=service,
                show_with_line=show_with_line,
                offline_methods_allowed=offline_methods_allowed,
                offline_methods=offline_methods,
                offline_instructions=offline_instructions,
            )
            ctx["error"] = "Sorry, that time was just booked. Please choose another slot."
            return render(request, "public/public_service_page.html", ctx)

        # Determine if this POST is part of a reschedule flow by validating
        # the provided `reschedule_source`/`reschedule_token` before creating
        # the new booking so we can mark the booking to suppress the normal
        # confirmation email when appropriate.
        reschedule_old_id = None
        res_src = request.POST.get('reschedule_source') or request.POST.get('source')
        res_token = request.POST.get('reschedule_token') or request.POST.get('token')
        try:
            if res_src and res_token:
                signer = TimestampSigner()
                try:
                    unsigned = signer.unsign(res_token, max_age=60*60*24*30)
                    if str(unsigned) == str(res_src):
                        try:
                            reschedule_old_id = int(res_src)
                        except Exception:
                            reschedule_old_id = None
                except Exception:
                    reschedule_old_id = None
        except Exception:
            reschedule_old_id = None

        # Payment selection
        payment_selection = (request.POST.get('payment_method') or '').strip().lower()
        chosen_offline_method = ''
        # Free services bypass payments regardless of selection
        try:
            service_price = float(getattr(service, 'price', 0) or 0)
        except Exception:
            service_price = 0
        is_paid_service = service_price > 0

        # Enforce per-service allowed methods
        try:
            stripe_allowed_for_service = bool(getattr(service, 'allow_stripe_payments', True))
        except Exception:
            stripe_allowed_for_service = True
        effective_offline_methods = _effective_offline_methods_for_service(service)
        offline_allowed_for_service = bool(offline_methods_allowed and effective_offline_methods)

        if not is_paid_service:
            payment_method = 'none'
        else:
            if not stripe_allowed_for_service and not offline_allowed_for_service:
                return HttpResponseBadRequest('No payment methods are enabled for this service.')

            allowed_offline_set = set([str(x).strip().lower() for x in (effective_offline_methods or []) if str(x).strip()])

            if payment_selection == 'stripe':
                if stripe_allowed_for_service:
                    payment_method = 'stripe'
                elif offline_allowed_for_service:
                    # Fallback to first allowed offline method
                    chosen_offline_method = next(iter(allowed_offline_set), '')
                    if not chosen_offline_method:
                        return HttpResponseBadRequest('Offline payment is not enabled for this service.')
                    payment_method = 'offline'
                else:
                    payment_method = 'none'
            elif payment_selection in allowed_offline_set:
                # Client chose a specific offline method (venmo/zelle/cash)
                if not offline_allowed_for_service:
                    if not offline_methods_allowed:
                        return HttpResponseBadRequest('Offline payment methods require a Pro or Team subscription.')
                    return HttpResponseBadRequest('Offline payment is not enabled for this service.')
                chosen_offline_method = payment_selection
                payment_method = 'offline'
            else:
                # Invalid/blank selection: pick a valid default
                if stripe_allowed_for_service:
                    payment_method = 'stripe'
                elif offline_allowed_for_service:
                    chosen_offline_method = next(iter(allowed_offline_set), '')
                    if not chosen_offline_method:
                        return HttpResponseBadRequest('Offline payment is not enabled for this service.')
                    payment_method = 'offline'
                else:
                    payment_method = 'none'

        # Stripe payments must NOT create a Booking until Stripe confirms payment.
        if payment_method == 'stripe':
            try:
                import stripe
                stripe.api_key = getattr(settings, 'STRIPE_SECRET_KEY', None)
                if not stripe.api_key:
                    return HttpResponseBadRequest('Stripe is not configured.')

                publishable_key = getattr(settings, 'STRIPE_PUBLISHABLE_KEY', None)
                if not publishable_key:
                    return HttpResponseBadRequest('Stripe publishable key is not configured.')

                # Create an intent first; we'll create the real Booking on Stripe return.
                intent = PublicBookingIntent.objects.create(
                    organization=org,
                    service=service,
                    client_name=client_name,
                    client_email=client_email,
                    start=start,
                    end=end,
                    payment_method='stripe',
                    payment_status='pending',
                    rescheduled_from_booking_id=reschedule_old_id,
                )

                unit_amount = int(round(service_price * 100))
                return_path = reverse('bookings:public_stripe_return', args=[org.slug, service.slug, intent.id])
                site_url = (getattr(settings, 'SITE_URL', None) or '').strip()
                if site_url:
                    return_url = site_url.rstrip('/') + return_path + '?session_id={CHECKOUT_SESSION_ID}'
                else:
                    return_url = request.build_absolute_uri(return_path) + '?session_id={CHECKOUT_SESSION_ID}'

                session = stripe.checkout.Session.create(
                    ui_mode='embedded',
                    mode='payment',
                    customer_email=client_email,
                    line_items=[{
                        'price_data': {
                            'currency': 'usd',
                            'product_data': {
                                'name': f"{org.name} — {service.name}",
                            },
                            'unit_amount': unit_amount,
                        },
                        'quantity': 1,
                    }],
                    metadata={
                        'public_booking_intent_id': str(intent.id),
                        'org_slug': str(org.slug),
                        'service_slug': str(service.slug),
                    },
                    return_url=return_url,
                )

                intent.stripe_checkout_session_id = session.id or ''
                try:
                    intent.save(update_fields=['stripe_checkout_session_id'])
                except Exception:
                    pass

                # Render the same public booking page but mount Embedded Checkout in the modal.
                ctx = _build_public_service_page_context(
                    request,
                    org=org,
                    services=services,
                    service=service,
                    show_with_line=show_with_line,
                    offline_methods_allowed=offline_methods_allowed,
                    offline_methods=offline_methods,
                    offline_instructions=offline_instructions,
                )
                ctx.update({
                    'stripe_publishable_key': publishable_key,
                    'stripe_client_secret': getattr(session, 'client_secret', None),
                    'open_checkout': True,
                })
                return render(request, "public/public_service_page.html", ctx)
            except Exception:
                return HttpResponseBadRequest('Unable to start Stripe checkout. Please try again.')

        # Offline/free: create a booking immediately.
        booking = Booking.objects.create(
            organization=org,
            service=service,
            title=getattr(service, "name", "Booking"),
            client_name=client_name,
            client_email=client_email,
            start=start,
            end=end,
            is_blocking=False,
            payment_method=payment_method,
            offline_payment_method=(chosen_offline_method if payment_method == 'offline' else ''),
            payment_status=('not_required' if payment_method == 'none' else 'offline_due'),
            rescheduled_from_booking_id=reschedule_old_id,
        )

        try:
            setattr(booking, '_suppress_confirmation', bool(reschedule_old_id))
        except Exception:
            pass

        # Owner notification for offline/free is immediate.
        try:
            from django.db import transaction
            from .emails import send_owner_booking_notification
            if getattr(org, "owner", None) and org.owner.email:
                try:
                    transaction.on_commit(lambda: send_owner_booking_notification(booking))
                except Exception:
                    try:
                        send_owner_booking_notification(booking)
                    except Exception:
                        pass
        except Exception:
            pass

        # Offline/free: do reschedule reconciliation and send the appropriate emails now.
        try:
            if reschedule_old_id:
                try:
                    old = Booking.objects.filter(id=reschedule_old_id, organization=org).first()
                    if old:
                        old.delete()
                    else:
                        from .models import AuditBooking
                        ab = AuditBooking.objects.filter(booking_id=reschedule_old_id, organization=org, event_type=AuditBooking.EVENT_CANCELLED).order_by('-created_at').first()
                        if ab:
                            ab.event_type = AuditBooking.EVENT_DELETED
                            try:
                                ab.save()
                            except Exception:
                                pass
                except Exception:
                    pass

                try:
                    from django.db import transaction
                    from .emails import send_booking_rescheduled
                    try:
                        transaction.on_commit(lambda: send_booking_rescheduled(booking, old_booking_id=reschedule_old_id))
                    except Exception:
                        try:
                            send_booking_rescheduled(booking, old_booking_id=reschedule_old_id)
                        except Exception:
                            pass
                except Exception:
                    pass
        except Exception:
            pass

        try:
            from django.db import transaction
            from .emails import send_booking_confirmation
            if not reschedule_old_id:
                try:
                    def _maybe_send():
                        try:
                            if not getattr(booking, '_suppress_confirmation', False):
                                send_booking_confirmation(booking)
                        except Exception:
                            pass
                    transaction.on_commit(_maybe_send)
                except Exception:
                    try:
                        if not getattr(booking, '_suppress_confirmation', False):
                            send_booking_confirmation(booking)
                    except Exception:
                        pass
        except Exception:
            pass

        return redirect(reverse("bookings:booking_success", args=[org.slug, service.slug, booking.id]))
        # Notify owner if this booking would violate service buffers (squished)
        try:
            if getattr(service, 'allow_squished_bookings', False):
                if _has_overlap(org, start, end, service=service):
                    owner = getattr(org, 'owner', None)
                    if owner and owner.email:
                        send_mail(
                            subject=f"Public booking violates buffers for {service.name}",
                            message=f"A public booking was made for {service.name} at {start.isoformat()} which violates buffer settings.",
                            from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None),
                            recipient_list=[owner.email],
                            fail_silently=True,
                        )
        except Exception:
            pass

    ctx = _build_public_service_page_context(
        request,
        org=org,
        services=services,
        service=service,
        show_with_line=show_with_line,
        offline_methods_allowed=offline_methods_allowed,
        offline_methods=offline_methods,
        offline_instructions=offline_instructions,
    )

    # Per-service payment method controls (for client-side toggling when the service dropdown changes)
    try:
        svc_payment = {}
        for s in services:
            try:
                allow_stripe = bool(getattr(s, 'allow_stripe_payments', True))
            except Exception:
                allow_stripe = True
            eff_off = _effective_offline_methods_for_service(s)
            svc_payment[str(getattr(s, 'slug', ''))] = {
                'allow_stripe': bool(allow_stripe),
                'offline_methods': eff_off,
                'offline_label': _offline_methods_label(eff_off),
                'offline_allowed': bool(offline_methods_allowed and eff_off),
            }
        ctx['service_payment_controls'] = svc_payment
    except Exception:
        ctx['service_payment_controls'] = {}

    return render(request, "public/public_service_page.html", ctx)

def booking_success(request, org_slug, service_slug, booking_id):
    org = get_object_or_404(Organization, slug=org_slug)
    service = get_object_or_404(Service, slug=service_slug, organization=org)
    booking = get_object_or_404(Booking, id=booking_id, organization=org, service=service)

    # Stripe return: verify payment and perform any deferred side effects.
    session_id = (request.GET.get('session_id') or '').strip()
    paid_now = False
    if getattr(booking, 'payment_method', '') == 'stripe' and getattr(booking, 'payment_status', '') != 'paid':
        if session_id and session_id == getattr(booking, 'stripe_checkout_session_id', ''):
            try:
                import stripe
                stripe.api_key = getattr(settings, 'STRIPE_SECRET_KEY', None)
                if stripe.api_key:
                    sess = stripe.checkout.Session.retrieve(session_id)
                    if sess and getattr(sess, 'payment_status', None) == 'paid':
                        booking.payment_status = 'paid'
                        try:
                            booking.save(update_fields=['payment_status'])
                        except Exception:
                            pass
                        paid_now = True
            except Exception:
                paid_now = False

    # If payment was confirmed on this request, run deferred reschedule cleanup and send emails.
    if paid_now:
        old_id = getattr(booking, 'rescheduled_from_booking_id', None)
        try:
            if old_id:
                try:
                    old = Booking.objects.filter(id=old_id, organization=org).first()
                    if old:
                        old.delete()
                    else:
                        from .models import AuditBooking
                        ab = AuditBooking.objects.filter(booking_id=old_id, organization=org, event_type=AuditBooking.EVENT_CANCELLED).order_by('-created_at').first()
                        if ab:
                            ab.event_type = AuditBooking.EVENT_DELETED
                            try:
                                ab.save()
                            except Exception:
                                pass
                except Exception:
                    pass

                try:
                    from django.db import transaction
                    from .emails import send_booking_rescheduled
                    try:
                        transaction.on_commit(lambda: send_booking_rescheduled(booking, old_booking_id=old_id))
                    except Exception:
                        try:
                            send_booking_rescheduled(booking, old_booking_id=old_id)
                        except Exception:
                            pass
                except Exception:
                    pass
        except Exception:
            pass

        try:
            if not old_id:
                from django.db import transaction
                from .emails import send_booking_confirmation
                try:
                    transaction.on_commit(lambda: send_booking_confirmation(booking))
                except Exception:
                    try:
                        send_booking_confirmation(booking)
                    except Exception:
                        pass
        except Exception:
            pass

        try:
            from django.db import transaction
            from .emails import send_owner_booking_notification
            if getattr(org, "owner", None) and org.owner.email:
                try:
                    transaction.on_commit(lambda: send_owner_booking_notification(booking))
                except Exception:
                    try:
                        send_owner_booking_notification(booking)
                    except Exception:
                        pass
        except Exception:
            pass

    offline_instructions = ''
    try:
        org_settings = getattr(org, 'settings', None)
        offline_instructions = build_offline_payment_instructions(org_settings) if org_settings else ''
    except Exception:
        offline_instructions = ''

    return render(request, "public/booking_success.html", {
        "org": org,
        "service": service,
        "booking": booking,
        "offline_instructions": offline_instructions,
    })


@require_http_methods(["GET"])
def public_stripe_return(request, org_slug, service_slug, intent_id: int):
    """Finalize a Stripe-paid public booking.

    This endpoint is used as the Stripe Embedded Checkout `return_url`.
    It verifies the Checkout Session, then creates the real Booking.
    """
    org = get_object_or_404(Organization, slug=org_slug)
    service = get_object_or_404(Service, slug=service_slug, organization=org)
    intent = get_object_or_404(PublicBookingIntent, id=intent_id, organization=org, service=service)

    session_id = (request.GET.get('session_id') or '').strip()
    if not session_id or session_id != (getattr(intent, 'stripe_checkout_session_id', '') or ''):
        return HttpResponseBadRequest('Invalid Stripe session.')

    paid = False
    try:
        import stripe
        stripe.api_key = getattr(settings, 'STRIPE_SECRET_KEY', None)
        if stripe.api_key:
            sess = stripe.checkout.Session.retrieve(session_id)
            if sess and getattr(sess, 'payment_status', None) == 'paid':
                paid = True
    except Exception:
        paid = False

    if not paid:
        # Payment not complete => no booking.
        ctx = _build_public_service_page_context(
            request,
            org=org,
            services=Service.objects.filter(organization=org, is_active=True).order_by('name'),
            service=service,
            show_with_line=(get_plan_slug(org) == TEAM_SLUG),
            offline_methods_allowed=can_use_offline_payment_methods(org),
            offline_methods=list(getattr(getattr(org, 'settings', None), 'offline_payment_methods', []) or []),
            offline_instructions=build_offline_payment_instructions(getattr(org, 'settings', None)),
        )
        ctx['error'] = 'Payment was not completed. No booking was created.'
        return render(request, 'public/public_service_page.html', ctx)

    # Re-check conflicts at finalize time.
    conflict = Booking.objects.filter(
        organization=org,
        start__lt=intent.end,
        end__gt=intent.start,
        service__isnull=False,
    ).exists()
    if conflict:
        ctx = _build_public_service_page_context(
            request,
            org=org,
            services=Service.objects.filter(organization=org, is_active=True).order_by('name'),
            service=service,
            show_with_line=(get_plan_slug(org) == TEAM_SLUG),
            offline_methods_allowed=can_use_offline_payment_methods(org),
            offline_methods=list(getattr(getattr(org, 'settings', None), 'offline_payment_methods', []) or []),
            offline_instructions=build_offline_payment_instructions(getattr(org, 'settings', None)),
        )
        ctx['error'] = 'Sorry, that time was just booked. Please contact the business.'
        return render(request, 'public/public_service_page.html', ctx)

    booking = Booking.objects.create(
        organization=org,
        service=service,
        title=getattr(service, 'name', 'Booking'),
        client_name=getattr(intent, 'client_name', '') or '',
        client_email=getattr(intent, 'client_email', '') or '',
        start=intent.start,
        end=intent.end,
        is_blocking=False,
        payment_method='stripe',
        payment_status='paid',
        stripe_checkout_session_id=session_id,
        rescheduled_from_booking_id=getattr(intent, 'rescheduled_from_booking_id', None),
    )

    try:
        intent.delete()
    except Exception:
        pass

    # Owner notification
    try:
        from django.db import transaction
        from .emails import send_owner_booking_notification
        if getattr(org, 'owner', None) and org.owner.email:
            try:
                transaction.on_commit(lambda: send_owner_booking_notification(booking))
            except Exception:
                try:
                    send_owner_booking_notification(booking)
                except Exception:
                    pass
    except Exception:
        pass

    old_id = getattr(booking, 'rescheduled_from_booking_id', None)
    try:
        if old_id:
            try:
                old = Booking.objects.filter(id=old_id, organization=org).first()
                if old:
                    old.delete()
                else:
                    from .models import AuditBooking
                    ab = AuditBooking.objects.filter(booking_id=old_id, organization=org, event_type=AuditBooking.EVENT_CANCELLED).order_by('-created_at').first()
                    if ab:
                        ab.event_type = AuditBooking.EVENT_DELETED
                        try:
                            ab.save()
                        except Exception:
                            pass
            except Exception:
                pass

            try:
                from django.db import transaction
                from .emails import send_booking_rescheduled
                try:
                    transaction.on_commit(lambda: send_booking_rescheduled(booking, old_booking_id=old_id))
                except Exception:
                    try:
                        send_booking_rescheduled(booking, old_booking_id=old_id)
                    except Exception:
                        pass
            except Exception:
                pass
        else:
            try:
                from django.db import transaction
                from .emails import send_booking_confirmation

                def _maybe_send():
                    try:
                        send_booking_confirmation(booking)
                    except Exception:
                        pass

                try:
                    transaction.on_commit(_maybe_send)
                except Exception:
                    try:
                        send_booking_confirmation(booking)
                    except Exception:
                        pass
            except Exception:
                pass
    except Exception:
        pass

    return redirect(reverse('bookings:booking_success', args=[org.slug, service.slug, booking.id]))

@require_http_methods(["GET"])
def reschedule_booking(request, booking_id):
    """Landing page for reschedule links sent in emails.

    Validates signed token (query param `token`) and displays a button
    that opens the public booking page for the same service with
    client info prefilled.
    """
    token = request.GET.get('token')
    if not token:
        return HttpResponseBadRequest('Missing token')

    signer = TimestampSigner()
    try:
        unsigned = signer.unsign(token, max_age=60*60*24*30)  # allow 30 days
        if str(unsigned) != str(booking_id):
            return HttpResponseBadRequest('Invalid token')
    except Exception:
        return HttpResponseBadRequest('Invalid or expired token')

    # Try to locate booking or audit snapshot (in case booking was deleted)
    from .models import AuditBooking
    try:
        bk = Booking.objects.filter(id=booking_id).first()
    except Exception:
        bk = None

    service_slug = None
    org_slug = None
    client_name = ''
    client_email = ''
    if bk:
        service_slug = bk.service.slug if bk.service else None
        org_slug = bk.organization.slug if bk.organization else None
        client_name = bk.client_name or ''
        client_email = bk.client_email or ''
    else:
        # Try audit snapshot
        ab = AuditBooking.objects.filter(booking_id=booking_id).order_by('-created_at').first()
        if ab:
            service_slug = ab.service.slug if ab.service else None
            org_slug = ab.organization.slug if ab.organization else None
            client_name = ab.client_name or ''
            client_email = ab.client_email or ''

    if not org_slug or not service_slug:
        return HttpResponseBadRequest('Unable to determine service for this booking')

    # Build a link to the public service page with prefill params
    from django.utils.http import urlencode
    base = reverse('bookings:public_service_page', args=[org_slug, service_slug])
    qs = urlencode({
        'client_name': client_name,
        'client_email': client_email,
        'reschedule_source': booking_id,
        'reschedule_token': token,
    })
    reschedule_link = f"{base}?{qs}"

    return render(request, 'bookings/reschedule_landing.html', {
        'reschedule_link': reschedule_link,
        'org_slug': org_slug,
        'service_slug': service_slug,
    })


@require_http_methods(['GET', 'POST'])
@require_roles(['owner', 'admin', 'manager'])
def create_service(request, org_slug):
    org, err = _require_org_and_role(request)
    if err:
        return err

    if request.method == "POST":
        name = request.POST.get("name")
        slug = request.POST.get("slug")

        Service.objects.create(
            organization=org,
            name=name,
            slug=slug,
            description=request.POST.get("description", ""),
            duration=int(request.POST.get("duration", 30)),
            price=float(request.POST.get("price", 0)),
            buffer_after=int(request.POST.get("buffer_after", 0)),
            allow_ends_after_availability=bool(request.POST.get('allow_ends_after_availability', False)),
            min_notice_hours=int(request.POST.get("min_notice_hours", 1)),
            max_booking_days=int(request.POST.get("max_booking_days", 30)),
        )

        return redirect("services_page", org_slug=org.slug)

    return render(request, "calendar/create_service.html", { "org": org })




@require_http_methods(["GET"])
def service_availability(request, org_slug, service_slug):
    """
    Returns a list of *AVAILABLE* time slots for a specific service.
    This powers the public booking calendar.
    """

    org = get_object_or_404(Organization, slug=org_slug)
    service = get_object_or_404(Service, slug=service_slug, organization=org)

    # Parse date range from FullCalendar
    start_param = request.GET.get("start")
    end_param = request.GET.get("end")

    if not start_param or not end_param:
        return HttpResponseBadRequest("start & end are required")

    # Parse the provided window into the organization's timezone
    try:
        org_tz = ZoneInfo(getattr(org, 'timezone', getattr(settings, 'TIME_ZONE', 'UTC')))
    except Exception:
        org_tz = ZoneInfo(getattr(settings, 'TIME_ZONE', 'UTC'))

    def _parse_to_org_tz(param: str, org_tz: ZoneInfo):
        s = (param or '').replace('Z', '+00:00')
        try:
            dt = datetime.fromisoformat(s)
        except Exception:
            return None
        if dt.tzinfo is None:
            dt = make_aware(dt, org_tz)
        else:
            dt = dt.astimezone(org_tz)
        return dt

    range_start = _parse_to_org_tz(start_param, org_tz)
    range_end = _parse_to_org_tz(end_param, org_tz)

    if not range_start or not range_end:
        return HttpResponseBadRequest("Invalid datetime format")

    # ---------------------------------------------
    # STEP 1: Filter out time too soon or too far
    # ---------------------------------------------
    # Use org timezone consistently for windowing logic
    now_org = timezone.now().astimezone(org_tz)
    earliest_allowed = now_org + timedelta(hours=service.min_notice_hours)
    latest_allowed = now_org + timedelta(days=service.max_booking_days)
    
    # Trial limit: cap calendar to trial_end date if org is on active trial
    subscription = get_subscription(org)
    if subscription and subscription.status == 'trialing' and subscription.trial_end:
        trial_end_dt = subscription.trial_end
        if timezone.is_naive(trial_end_dt):
            trial_end_dt = make_aware(trial_end_dt, org_tz)
        else:
            trial_end_dt = trial_end_dt.astimezone(org_tz)
        # Cap latest_allowed to trial end date (end of day)
        trial_end_eod = trial_end_dt.replace(hour=23, minute=59, second=59, microsecond=999999)
        if trial_end_eod < latest_allowed:
            latest_allowed = trial_end_eod

    # Preserve original requested start for anchoring window starts (before min-notice clamp)
    original_range_start = range_start
    # For filtering extremely early requested ranges we still use a clamped value where needed, but we do NOT
    # shift the slot anchor; we only skip early slots.
    effective_range_start = max(range_start, earliest_allowed)

    # Normalize seconds/micros ONLY (do not round to 15-min boundary; keep irregular starts like 08:20)
    range_start = range_start.replace(second=0, microsecond=0)

    if range_end > latest_allowed:
        range_end = latest_allowed

    if range_end > latest_allowed:
        range_end = latest_allowed
    # ---------------------------------------------
    # Per-date overrides live as bookings with service NULL
    # Fetch per-date overrides that overlap the requested day in org timezone,
    # scoped to this service/member context.
    day_start_candidate = range_start.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end_candidate = day_start_candidate.replace(hour=23, minute=59, second=59)
    override_qs = Booking.objects.filter(
        organization=org,
        service__isnull=True,
        start__lt=day_end_candidate,
        end__gt=day_start_candidate,
    ).filter(_per_date_override_scope_q(service))
    blocking_full_day = False
    availability_override_windows = []  # list of (start,end) datetimes in org timezone
    for bk in override_qs:
        # Normalize booking times to org timezone for consistent comparisons
        bk_start_org = bk.start.astimezone(org_tz)
        bk_end_org = bk.end.astimezone(org_tz)
        # Detect a full-day blocking override (covers the entire requested day)
        if bk.is_blocking:
            # Treat any blocking override that spans from <= day start to >= day end as full-day block
            day_start_candidate = range_start.replace(hour=0, minute=0, second=0, microsecond=0)
            day_end_candidate = day_start_candidate.replace(hour=23, minute=59, second=59)
            if bk_start_org <= day_start_candidate and bk_end_org >= day_end_candidate:
                blocking_full_day = True
        else:
            availability_override_windows.append((bk_start_org, bk_end_org))

    if blocking_full_day:
        return JsonResponse([], safe=False)

    # Existing busy bookings: ALWAYS include real service bookings (exclude all per-date overrides)
    # Use the full local day window to ensure we catch bookings even if range_start
    # is clamped by min_notice hours.
    day_start = range_start.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start.replace(hour=23, minute=59, second=59)
    existing = Booking.objects.filter(
        organization=org,
        start__lt=day_end,
        end__gt=day_start
    ).exclude(service__isnull=True)

    # Normalize busy windows into org timezone for consistent overlap checks
    busy = [(bk.start.astimezone(org_tz), bk.end.astimezone(org_tz)) for bk in existing]
    # DEBUG: Temporarily log busy windows for troubleshooting
    if settings.DEBUG:
        try:
            print(f"[availability] org={org.slug} day={day_start.date()} busy:")
            for bs, be in busy:
                print(f"  - {bs.isoformat()} -> {be.isoformat()}")
        except Exception:
            pass

    # ---------------------------------------------
    # STEP 3: Walk through each minute block and
    # find valid start times based on:
    # - service duration
    # - buffer_before / buffer_after
    # - overlaps
    # - weekly availability (your logic)
    # ---------------------------------------------

    # We'll compute effective per-window settings to honor any per-date freezes
    # (created when an owner updates a service but wants days with existing
    # bookings to continue using the old settings). For each window we fetch
    # the freeze (if any) and derive duration, buffer_after and increment.

    # Whether to apply buffers to the window edges/spacing. This can be
    # controlled by the public page via the `edge_buffers` query param. When
    # false (default) the availability will show denser UI increments and
    # only hide slots after bookings using the booked appointment buffers.
    apply_edge_buffers = request.GET.get('edge_buffers') in ('1', 'true', 'True')

    available_slots = []

    # Build base windows (override windows supersede weekly windows)
    if availability_override_windows:
        base_windows = [(ov_start.astimezone(org_tz), ov_end.astimezone(org_tz)) for ov_start, ov_end in availability_override_windows]
    else:
        # First, allow a frozen per-date weekly window snapshot to override
        # current weekly availability. This preserves the exact windows that were
        # in effect when a ServiceSettingFreeze was created for a date with
        # existing bookings.
        weekday = original_range_start.weekday()
        freeze = None
        try:
            from bookings.models import ServiceSettingFreeze
            freeze = ServiceSettingFreeze.objects.filter(service=service, date=original_range_start.date()).first()
            if freeze:
                try:
                    # If there are no bookings for that frozen date, ignore the freeze
                    try:
                        org_tz_check = org_tz
                    except Exception:
                        org_tz_check = timezone.get_current_timezone()
                    day_start_chk = datetime(original_range_start.year, original_range_start.month, original_range_start.day, 0, 0, 0)
                    if day_start_chk.tzinfo is None:
                        day_start_chk = make_aware(day_start_chk, org_tz_check)
                    day_end_chk = day_start_chk + timedelta(days=1)
                    has_bookings_chk = Booking.objects.filter(service=service, organization=org, start__gte=day_start_chk, start__lt=day_end_chk).exists()
                    if not has_bookings_chk:
                        freeze = None
                except Exception:
                    # on error, be conservative and keep freeze
                    pass
        except Exception:
            freeze = None

        if freeze and isinstance(freeze.frozen_settings, dict) and freeze.frozen_settings.get('weekly_windows'):
            base_windows = []
            for w in freeze.frozen_settings.get('weekly_windows', []):
                try:
                    sh, sm = (int(x) for x in (w.get('start', '00:00').split(':')))
                    eh, em = (int(x) for x in (w.get('end', '00:00').split(':')))
                    w_start = original_range_start.replace(hour=sh, minute=sm, second=0, microsecond=0)
                    w_end = original_range_start.replace(hour=eh, minute=em, second=0, microsecond=0)
                    base_windows.append((w_start, w_end))
                except Exception:
                    continue
        else:
            # Prefer service-specific weekly windows if defined.
            # IMPORTANT: if a service has any active service-weekly rows, it is
            # restricted to ONLY those days/times (no per-day fallback to org weekly).
            try:
                if _trial_single_active_service(org):
                    svc_has_any = False
                else:
                    svc_has_any = service.weekly_availability.filter(is_active=True).exists()
            except Exception:
                svc_has_any = False

            try:
                svc_requires_explicit = _service_requires_explicit_weekly(org, service)
            except Exception:
                svc_requires_explicit = False

            svc_is_scoped = bool(svc_has_any or svc_requires_explicit)

            if _trial_single_active_service(org):
                svc_rows = None
            else:
                svc_rows = service.weekly_availability.filter(is_active=True, weekday=weekday)

            if svc_rows and svc_rows.exists():
                base_windows = []
                for w in svc_rows.order_by('start_time'):
                    w_start = original_range_start.replace(hour=w.start_time.hour, minute=w.start_time.minute, second=0, microsecond=0)
                    w_end = original_range_start.replace(hour=w.end_time.hour, minute=w.end_time.minute, second=0, microsecond=0)
                    base_windows.append((w_start, w_end))
            else:
                if svc_is_scoped:
                    base_windows = []
                else:
                    weekly_rows = WeeklyAvailability.objects.filter(organization=org, is_active=True, weekday=weekday)
                    base_windows = []
                    for w in weekly_rows:
                        w_start = original_range_start.replace(hour=w.start_time.hour, minute=w.start_time.minute, second=0, microsecond=0)
                        w_end = original_range_start.replace(hour=w.end_time.hour, minute=w.end_time.minute, second=0, microsecond=0)
                        base_windows.append((w_start, w_end))

    # Determine slot increment: front-end may pass `?inc=` (minutes) to control
    # the UI tick spacing. If provided and valid, use it for slot iteration;
    # otherwise fall back to the service's configured increment or, when
    # `use_fixed_increment` is True, to duration+buffer.
    inc_param = request.GET.get('inc')
    try:
        if inc_param:
            val = int(inc_param)
            display_inc = timedelta(minutes=val) if 1 <= val <= 24 * 60 else None
        else:
            display_inc = None
    except Exception:
        display_inc = None

    if display_inc is None:
        # server-configured default
        try:
            if getattr(service, 'use_fixed_increment', False):
                display_inc = timedelta(minutes=(service.duration + (service.buffer_after or 0)))
            else:
                display_inc = timedelta(minutes=getattr(service, 'time_increment_minutes', service.duration))
        except Exception:
            display_inc = timedelta(minutes=service.duration)

    slot_increment = display_inc

    # If this service is configured with discrete facility resources (cages/rooms),
    # availability should be computed as "any resource free" rather than org-wide capacity=1.
    svc_resource_ids = _service_resource_ids(service)

    for win_start, win_end in base_windows:
        # Keep windows even if their early portion violates min notice; we'll just skip early slots.
        if win_end <= original_range_start or win_start >= range_end:
            continue
        window_end = min(win_end, range_end, latest_allowed)
        # Resolve per-date freeze (if present)
        freeze = None
        try:
            from bookings.models import ServiceSettingFreeze
            freeze = ServiceSettingFreeze.objects.filter(service=service, date=win_start.date()).first()
            if freeze:
                try:
                    try:
                        org_tz_check = org_tz
                    except Exception:
                        org_tz_check = timezone.get_current_timezone()
                    day_start_chk = datetime(win_start.year, win_start.month, win_start.day, 0, 0, 0)
                    if day_start_chk.tzinfo is None:
                        day_start_chk = make_aware(day_start_chk, org_tz_check)
                    day_end_chk = day_start_chk + timedelta(days=1)
                    has_bookings_chk = Booking.objects.filter(service=service, organization=org, start__gte=day_start_chk, start__lt=day_end_chk).exists()
                    if not has_bookings_chk:
                        freeze = None
                except Exception:
                    pass
        except Exception:
            freeze = None

        if freeze and isinstance(freeze.frozen_settings, dict):
            f = freeze.frozen_settings
            try:
                duration = timedelta(minutes=int(f.get('duration', getattr(service, 'duration'))))
            except Exception:
                duration = timedelta(minutes=service.duration)
            try:
                buffer_after = timedelta(minutes=int(f.get('buffer_after', getattr(service, 'buffer_after', 0))))
            except Exception:
                buffer_after = timedelta(minutes=getattr(service, 'buffer_after', 0))
            try:
                if bool(f.get('use_fixed_increment', False)):
                    display_inc = timedelta(minutes=(int(f.get('duration', getattr(service, 'duration'))) + int(f.get('buffer_after', getattr(service, 'buffer_after', 0)))))
                else:
                    display_inc = timedelta(minutes=int(f.get('time_increment_minutes', getattr(service, 'time_increment_minutes', service.duration))))
            except Exception:
                display_inc = timedelta(minutes=getattr(service, 'time_increment_minutes', service.duration))
            allow_ends_after = bool(f.get('allow_ends_after_availability', getattr(service, 'allow_ends_after_availability', False)))
            allow_squished = bool(f.get('allow_squished_bookings', getattr(service, 'allow_squished_bookings', False)))
        else:
            duration = timedelta(minutes=service.duration)
            buffer_after = timedelta(minutes=service.buffer_after)
            try:
                if getattr(service, 'use_fixed_increment', False):
                    display_inc = timedelta(minutes=(service.duration + (service.buffer_after or 0)))
                else:
                    display_inc = timedelta(minutes=getattr(service, 'time_increment_minutes', service.duration))
            except Exception:
                display_inc = timedelta(minutes=service.duration)
            allow_ends_after = getattr(service, 'allow_ends_after_availability', False)
            allow_squished = getattr(service, 'allow_squished_bookings', False)

        total_length = duration + buffer_after

        # Determine minimal needed window length. If the service owner allows
        # appointments to end after availability, we only require room for the
        # duration; otherwise respect the full spacing (duration + buffer_after)
        min_needed = duration if allow_ends_after else (total_length if apply_edge_buffers else duration)
        if window_end - win_start < min_needed:
            continue

        # Resource-aware availability: iterate candidate slots and include them when
        # at least one allowed resource is available.
        if svc_resource_ids:
            if apply_edge_buffers:
                slot_increment = total_length
            else:
                slot_increment = display_inc

            slot_start = win_start.replace(second=0, microsecond=0)
            while slot_start < window_end:
                slot_end = slot_start + duration

                # If owner disallows ending after availability, make sure slot fits in window
                if not allow_ends_after:
                    needed = (total_length if apply_edge_buffers else duration)
                    if slot_start + needed > window_end:
                        break

                    # If service does NOT allow squished bookings, ensure candidate's
                    # post-buffer does not violate the window edge.
                    if not allow_squished:
                        try:
                            cand_end_plus = slot_end + buffer_after
                        except Exception:
                            cand_end_plus = slot_end
                        try:
                            ends_at_window = (slot_end == window_end)
                        except Exception:
                            ends_at_window = False
                        if not ends_at_window and cand_end_plus > window_end:
                            slot_start += slot_increment
                            continue

                # Weekly availability enforcement
                if not availability_override_windows and not is_within_availability(org, slot_start, slot_end, service):
                    slot_start += slot_increment
                    continue

                # Min notice handling
                try:
                    if slot_start < earliest_allowed:
                        slot_start += slot_increment
                        continue
                except Exception:
                    pass

                # Facility resource enforcement: require at least one free resource.
                if _find_available_resource_id(org, service, slot_start, slot_end) is None:
                    slot_start += slot_increment
                    continue

                slot_info = {
                    "start": slot_start.isoformat(),
                    "end": slot_end.isoformat(),
                    "used_freeze": bool(freeze),
                    "freeze_date": (freeze.date.isoformat() if freeze and getattr(freeze, 'date', None) else None),
                }
                available_slots.append(slot_info)

                slot_start += slot_increment

            continue

        # Build a list of busy intervals (with after-buffers applied) that intersect this window
        busy_intervals = []
        for b in existing:
            try:
                b_start = b.start.astimezone(org_tz)
                b_end = b.end.astimezone(org_tz)
            except Exception:
                b_start = b.start
                b_end = b.end
            try:
                b_buf_after = timedelta(minutes=getattr(b.service, 'buffer_after', 0) or 0)
            except Exception:
                b_buf_after = timedelta(0)
            if b_end <= win_start or b_start >= window_end:
                continue
            busy_intervals.append((max(b_start, win_start), min(b_end, window_end), b_buf_after))

        busy_intervals.sort(key=lambda x: x[0])

        # Build free segments by walking busy intervals and moving the cursor past
        # each booking's end + after-buffer.
        segments = []
        cursor = win_start
        for b_start, b_end, b_buf_after in busy_intervals:
            if b_start > cursor:
                segments.append((cursor, b_start))
            cursor = max(cursor, b_end + b_buf_after)
        if cursor < window_end:
            segments.append((cursor, window_end))

        # Determine slot stepping (use display_inc computed earlier)
        if apply_edge_buffers:
            slot_increment = total_length
        else:
            slot_increment = display_inc

        # Generate slots within each free segment. Start at segment start so
        # bookings shift subsequent anchors forward (e.g., booking ended at 10:05 -> first
        # slot in segment is 10:05, then +increment etc.).
        for seg_start, seg_end in segments:
            slot_start = seg_start.replace(second=0, microsecond=0)
            while slot_start < seg_end:
                slot_end = slot_start + duration

                # If owner disallows ending after availability, make sure slot fits in segment
                if not allow_ends_after:
                    needed = (total_length if apply_edge_buffers else duration)
                    if slot_start + needed > seg_end:
                        break

                    # If service does NOT allow squished bookings, ensure candidate's
                    # post-buffer does not collide with the next busy interval (seg_end)
                    if not allow_squished:
                        try:
                            cand_end_plus = slot_end + timedelta(minutes=(buffer_after.total_seconds() / 60))
                        except Exception:
                            cand_end_plus = slot_end
                        # Allow the candidate if it ends exactly at the segment/window end
                        # (owners should not need to enable `allow_ends_after_availability` when
                        #  the appointment finishes exactly at the availability end).
                        try:
                            ends_at_window = (slot_end == seg_end)
                        except Exception:
                            ends_at_window = False
                        if not ends_at_window and cand_end_plus > seg_end:
                            # This candidate would violate the post-buffer before the next booking; skip it
                            slot_start += slot_increment
                            continue

                # Weekly availability enforcement
                if not availability_override_windows and not is_within_availability(org, slot_start, slot_end, service):
                    slot_start += slot_increment
                    continue

                # Min notice handling: skip any slot earlier than the earliest allowed time
                try:
                    if slot_start < earliest_allowed:
                        slot_start += slot_increment
                        continue
                except Exception:
                    # If comparison fails for any reason, skip enforcing and proceed
                    pass

                # Determine whether to mark buffer violations. Only expose this
                # information to authenticated org members (owners/admins/managers).
                try:
                    is_org_member = False
                    if getattr(request, 'user', None) and request.user.is_authenticated:
                        from accounts.models import Membership
                        is_org_member = Membership.objects.filter(user=request.user, organization=org, is_active=True, role__in=['owner','admin','manager']).exists()
                except Exception:
                    is_org_member = False

                slot_info = {
                    "start": slot_start.isoformat(),
                    "end": slot_end.isoformat(),
                    "used_freeze": bool(freeze),
                    "freeze_date": (freeze.date.isoformat() if freeze and getattr(freeze, 'date', None) else None),
                }

                if is_org_member:
                    try:
                        slot_info['violates_buffer'] = (slot_end + buffer_after > seg_end)
                    except Exception:
                        slot_info['violates_buffer'] = False

                available_slots.append(slot_info)

                slot_start += slot_increment

    return JsonResponse(available_slots, safe=False)


@require_http_methods(["GET"])
def service_effective_settings(request, org_slug, service_slug):
    """Return the effective service settings for a given date.

    If a `ServiceSettingFreeze` exists for the requested date, return the
    frozen settings; otherwise return the current service fields.
    Query params: date=YYYY-MM-DD (optional, defaults to org-local today)
    """
    org = get_object_or_404(Organization, slug=org_slug)
    service = get_object_or_404(Service, slug=service_slug, organization=org)

    date_param = request.GET.get('date')
    target_date = None
    try:
        if date_param:
            from django.utils.dateparse import parse_date
            target_date = parse_date(date_param)
    except Exception:
        target_date = None

    try:
        org_tz = ZoneInfo(getattr(org, 'timezone', getattr(settings, 'TIME_ZONE', 'UTC')))
    except Exception:
        org_tz = timezone.get_current_timezone()

    if not target_date:
        target_date = timezone.now().astimezone(org_tz).date()

    freeze = None
    try:
        freeze = _active_service_freeze_for_date(org, service, target_date, org_tz)
    except Exception:
        freeze = None

    if freeze and isinstance(getattr(freeze, 'frozen_settings', None), dict):
        out = dict(freeze.frozen_settings)
        out['use_fixed_increment'] = bool(out.get('use_fixed_increment', False))
        out['allow_ends_after_availability'] = bool(out.get('allow_ends_after_availability', False))
        out['allow_squished_bookings'] = bool(out.get('allow_squished_bookings', False))
    else:
        out = {
            'duration': int(getattr(service, 'duration', 0) or 0),
            'buffer_after': int(getattr(service, 'buffer_after', 0) or 0),
            'time_increment_minutes': int(getattr(service, 'time_increment_minutes', 0) or 0),
            'use_fixed_increment': bool(getattr(service, 'use_fixed_increment', False)),
            'allow_ends_after_availability': bool(getattr(service, 'allow_ends_after_availability', False)),
            'allow_squished_bookings': bool(getattr(service, 'allow_squished_bookings', False)),
        }

    out['service_id'] = service.id
    out['service_slug'] = service.slug
    return JsonResponse(out)


@require_http_methods(["GET"])
def batch_availability_summary(request, org_slug, service_slug):
    """Returns a daily availability summary for a date range.
    Query params: start, end (ISO 8601 date strings).
    Returns: {"YYYY-MM-DD": boolean, ...} where true = has available slots.
    """
    org = get_object_or_404(Organization, slug=org_slug)
    service = get_object_or_404(Service, slug=service_slug, organization=org)

    start_param = request.GET.get("start")
    end_param = request.GET.get("end")
    if not start_param or not end_param:
        return HttpResponseBadRequest("start & end are required")

    try:
        org_tz = ZoneInfo(getattr(org, 'timezone', getattr(settings, 'TIME_ZONE', 'UTC')))
    except Exception:
        org_tz = ZoneInfo(getattr(settings, 'TIME_ZONE', 'UTC'))

    def _parse_to_org_tz(param: str, org_tz: ZoneInfo):
        s = (param or '').replace('Z', '+00:00')
        try:
            dt = datetime.fromisoformat(s)
        except Exception:
            return None
        if dt.tzinfo is None:
            dt = make_aware(dt, org_tz)
        else:
            dt = dt.astimezone(org_tz)
        return dt

    range_start = _parse_to_org_tz(start_param, org_tz)
    range_end = _parse_to_org_tz(end_param, org_tz)
    if not range_start or not range_end:
        return HttpResponseBadRequest("Invalid datetime format")

    # Iterate each day and check if it has slots
    summary = {}
    current = range_start.replace(hour=0, minute=0, second=0, microsecond=0)
    
    # Calculate the max booking date (max_booking_days from today)
    now_org = timezone.now().astimezone(org_tz)
    today_midnight = now_org.replace(hour=0, minute=0, second=0, microsecond=0)
    max_booking_date = today_midnight + timedelta(days=service.max_booking_days)
    earliest_allowed = now_org + timedelta(hours=service.min_notice_hours)
    
    # Trial limit: cap max_booking_date to trial_end if org is on active trial
    subscription = get_subscription(org)
    if subscription and subscription.status == 'trialing' and subscription.trial_end:
        trial_end_dt = subscription.trial_end
        if timezone.is_naive(trial_end_dt):
            trial_end_dt = make_aware(trial_end_dt, org_tz)
        else:
            trial_end_dt = trial_end_dt.astimezone(org_tz)
        # Cap max_booking_date to trial end date (midnight next day)
        trial_end_midnight = trial_end_dt.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        if trial_end_midnight < max_booking_date:
            max_booking_date = trial_end_midnight

    # If this service is explicitly scoped (has any active service-weekly rows OR
    # is unassigned/shared/partitioned), days without service rows are unavailable.
    try:
        if _trial_single_active_service(org):
            svc_has_any_weekly = False
        else:
            svc_has_any_weekly = service.weekly_availability.filter(is_active=True).exists()
    except Exception:
        svc_has_any_weekly = False

    try:
        svc_requires_explicit = _service_requires_explicit_weekly(org, service)
    except Exception:
        svc_requires_explicit = False

    svc_is_scoped = bool(svc_has_any_weekly or svc_requires_explicit)
    
    while current < range_end:
        day_start = current
        day_end = current.replace(hour=23, minute=59, second=59)
        
        # If entire day is in the past or before min notice, no slots
        if day_end < earliest_allowed:
            summary[current.strftime('%Y-%m-%d')] = False
            current += timedelta(days=1)
            continue
        
        # If day is beyond max_booking_days, no slots
        if day_start >= max_booking_date:
            summary[current.strftime('%Y-%m-%d')] = False
            current += timedelta(days=1)
            continue

        # Per-date overrides (service NULL bookings), scoped to the selected service.
        # Consider overrides that overlap the day window (org timezone).
        override_qs = Booking.objects.filter(
            organization=org,
            service__isnull=True,
            start__lt=day_end,
            end__gt=day_start,
        ).filter(_per_date_override_scope_q(service))

        availability_override_windows = []
        has_avail = False
        full_block = False
        for bk in override_qs:
            try:
                bk_start_org = bk.start.astimezone(org_tz)
            except Exception:
                bk_start_org = bk.start
            try:
                bk_end_org = bk.end.astimezone(org_tz)
            except Exception:
                bk_end_org = bk.end

            if bk.is_blocking:
                # Treat a blocking override as full-day block if it covers the entire day.
                # Allow for small time differences (< 2 minutes) to account for 23:59 vs 23:59:59.
                covers_start = bk_start_org <= day_start + timedelta(minutes=1)
                covers_end = bk_end_org >= day_end - timedelta(minutes=1)
                if covers_start and covers_end:
                    full_block = True
            else:
                has_avail = True
                if bk_end_org > bk_start_org:
                    availability_override_windows.append((bk_start_org, bk_end_org))

        # Full-day block with no availability overrides => no availability for that date.
        if full_block and not has_avail:
            summary[current.strftime('%Y-%m-%d')] = False
            current += timedelta(days=1)
            continue

        # Determine effective weekly windows for this day:
        # 1) availability override windows (if any)
        # 2) per-date freeze weekly_windows (only when bookings exist)
        # 3) service-specific weekly windows
        # 4) org weekly windows
        # 5) legacy: if org has no weekly rows at all, treat as fully available
        base_windows = []

        if availability_override_windows:
            base_windows = [(s, e) for (s, e) in availability_override_windows if e > s]
        else:
            freeze = None
            try:
                freeze = _active_service_freeze_for_date(org, service, day_start.date(), org_tz)
            except Exception:
                freeze = None

            if freeze and isinstance(getattr(freeze, 'frozen_settings', None), dict) and freeze.frozen_settings.get('weekly_windows'):
                for w in freeze.frozen_settings.get('weekly_windows', []):
                    try:
                        sh, sm = (int(x) for x in (str(w.get('start', '00:00')).split(':')))
                        eh, em = (int(x) for x in (str(w.get('end', '00:00')).split(':')))
                        ws = day_start.replace(hour=sh, minute=sm, second=0, microsecond=0)
                        we = day_start.replace(hour=eh, minute=em, second=0, microsecond=0)
                        if we > ws:
                            base_windows.append((ws, we))
                    except Exception:
                        continue
            else:
                try:
                    if _trial_single_active_service(org):
                        svc_rows = None
                    else:
                        svc_rows = service.weekly_availability.filter(is_active=True, weekday=day_start.weekday()).order_by('start_time')
                except Exception:
                    svc_rows = None

                if svc_rows and svc_rows.exists():
                    for w in svc_rows:
                        ws = day_start.replace(hour=w.start_time.hour, minute=w.start_time.minute, second=0, microsecond=0)
                        we = day_start.replace(hour=w.end_time.hour, minute=w.end_time.minute, second=0, microsecond=0)
                        if we > ws:
                            base_windows.append((ws, we))
                else:
                    if svc_is_scoped:
                        base_windows = []
                    else:
                        try:
                            any_org_rows = WeeklyAvailability.objects.filter(organization=org, is_active=True).exists()
                        except Exception:
                            any_org_rows = False

                        if not any_org_rows:
                            base_windows = [(day_start.replace(hour=0, minute=0, second=0, microsecond=0), day_start.replace(hour=23, minute=59, second=0, microsecond=0))]
                        else:
                            windows = WeeklyAvailability.objects.filter(
                                organization=org,
                                is_active=True,
                                weekday=day_start.weekday()
                            )
                            for w in windows:
                                ws = day_start.replace(hour=w.start_time.hour, minute=w.start_time.minute, second=0, microsecond=0)
                                we = day_start.replace(hour=w.end_time.hour, minute=w.end_time.minute, second=0, microsecond=0)
                                if we > ws:
                                    base_windows.append((ws, we))

        # If no base windows, day cannot be available.
        if not base_windows:
            summary[current.strftime('%Y-%m-%d')] = False
            current += timedelta(days=1)
            continue

        # Ensure windows actually contain future times after min-notice and before max booking.
        has_future_window = False
        for ws, we in base_windows:
            if we > earliest_allowed and ws < max_booking_date:
                has_future_window = True
                break

        summary[current.strftime('%Y-%m-%d')] = bool(has_future_window)

        current += timedelta(days=1)

    return JsonResponse(summary, safe=False)


@require_http_methods(["GET"])
def public_busy(request, org_slug):
    """
    Public endpoint returning busy intervals (booked events) for an org over a date range.
    Query params: start, end (ISO 8601). Returns [{start, end}, ...]
    """
    org = get_object_or_404(Organization, slug=org_slug)

    start_param = request.GET.get("start")
    end_param = request.GET.get("end")
    if not start_param or not end_param:
        return HttpResponseBadRequest("start & end are required")

    try:
        range_start = datetime.fromisoformat(start_param.replace("Z", ""))
        range_end = datetime.fromisoformat(end_param.replace("Z", ""))
    except Exception:
        return HttpResponseBadRequest("Invalid datetime format")

    # Make timezone-aware
    if timezone.is_naive(range_start):
        range_start = make_aware(range_start, timezone.get_current_timezone())
    if timezone.is_naive(range_end):
        range_end = make_aware(range_end, timezone.get_current_timezone())

    busy_qs = Booking.objects.filter(
        organization=org,
        start__lt=range_end,
        end__gt=range_start,
    )

    payload = [{"start": b.start.isoformat(), "end": b.end.isoformat()} for b in busy_qs]
    return JsonResponse(payload, safe=False)

