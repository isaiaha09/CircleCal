from django.http import JsonResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.timezone import make_aware
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.core.signing import TimestampSigner, BadSignature, SignatureExpired
from accounts.models import Business as Organization
from bookings.models import Service
from datetime import datetime
from zoneinfo import ZoneInfo
import json
from django.utils.dateparse import parse_datetime
from django.conf import settings
from django.core.mail import send_mail
from datetime import timedelta
from bookings.models import Booking
from bookings.models import WeeklyAvailability, OrgSettings
from calendar_app.utils import user_has_role  # <-- single source of truth
from calendar_app.permissions import require_roles
from billing.utils import get_subscription


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
            # Flag all overrides (service NULL) so frontend can reliably detect them after hard refresh
            'is_per_date': bk.service is None,
        }
    }

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

    return event


def _anchors_for_date(org, service, day_date, org_tz, total_length):
    """Return a list of aware datetimes representing the valid booking anchors
    for a given organization, service and date (in org_tz). Anchors are spaced
    by `total_length` and are computed from the service or org weekly windows.
    """
    anchors = []
    from datetime import datetime

    # Determine base windows for that weekday
    weekday = day_date.weekday()
    svc_rows = service.weekly_availability.filter(is_active=True, weekday=weekday)
    base_windows = []
    if svc_rows.exists():
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


def _has_overlap(org, start_dt, end_dt, service=None):
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

    booking.delete()

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

    # Query all per-date overrides for that calendar day (service NULL so they are not actual client bookings)
    override_qs = Booking.objects.filter(
        organization=org,
        start__date=start_dt.date(),
        service__isnull=True
    )

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
    # If a service-specific weekly availability exists, prefer that.
    if service is not None:
        try:
            svc_rows = service.weekly_availability.filter(is_active=True, weekday=start_dt.weekday())
        except Exception:
            svc_rows = None

        if svc_rows and svc_rows.exists():
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

    # Overlap check (buffer-aware when `service` provided)
    overlap_result = _has_overlap(org, start_dt, end_dt, service=service)
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

        # DON'T delete all existing overrides - allow multiple time ranges per date
        # Only check for exact duplicate (same start/end on same date)
        existing_duplicate = Booking.objects.filter(
            organization=org,
            start=s,
            end=e,
            service__isnull=True
        ).exists()
        
        if existing_duplicate:
            # Skip creating duplicate
            continue

        # Optional overlap prevention for real service bookings only
        if not bool(data.get("is_blocking", False)) and _has_overlap(org, s, e, service=None):
            continue

        bk = Booking.objects.create(
            organization=org,
            title=data.get('title', ''),
            start=s,
            end=e,
            client_name=data.get('client_name', ''),
            client_email=data.get('client_email', ''),
            is_blocking=bool(data.get('is_blocking', False)),
            service=None  # ensure overrides are stored as service NULL
        )
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
        deleted += qs.count()
        qs.delete()

    return JsonResponse({'status': 'ok', 'deleted': deleted})




def public_org_page(request, org_slug):
    org = get_object_or_404(Organization, slug=org_slug)
    services = org.services.filter(is_active=True)
    return render(request, "public/public_org_page.html", {"org": org, "services": services})





@require_http_methods(['GET', 'POST'])
def public_service_page(request, org_slug, service_slug):
    """
    Public booking page for a single service.
    GET  -> show calendar + booking modal
    POST -> create a Booking for the selected time
    """
    org = get_object_or_404(Organization, slug=org_slug)
    services = Service.objects.filter(organization=org).order_by("name")
    service = get_object_or_404(Service, slug=service_slug, organization=org)

    if request.method == "POST":
        client_name = request.POST.get("client_name")
        client_email = request.POST.get("client_email")
        start_str = request.POST.get("start")
        end_str = request.POST.get("end")
        # Allow selecting a different service from the modal
        posted_service_slug = request.POST.get("service_slug")
        if posted_service_slug and posted_service_slug != service_slug:
            service = get_object_or_404(Service, slug=posted_service_slug, organization=org)

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
            return render(request, "public/public_service_page.html", {
                "org": org,
                "service": service,
                "error": "Sorry, that time was just booked. Please choose another slot.",
            })

        booking = Booking.objects.create(
            organization=org,
            service=service,
            title=getattr(service, "name", "Booking"),
            client_name=client_name,
            client_email=client_email,
            start=start,
            end=end,
            is_blocking=False,
        )

        # Email notifications: send a single HTML confirmation to client
        try:
            from .emails import send_booking_confirmation
            if client_email:
                send_booking_confirmation(booking)

            if getattr(org, "owner", None) and org.owner.email:
                # Styled HTML owner notification
                try:
                    from .emails import send_owner_booking_notification
                    send_owner_booking_notification(booking)
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

    # GET - add trial context for banner
    subscription = get_subscription(org)
    trialing_active = False
    trial_end_date = None
    if subscription and subscription.status == 'trialing' and subscription.trial_end:
        now = timezone.now()
        if subscription.trial_end > now:
            trialing_active = True
            trial_end_date = subscription.trial_end
    
    return render(request, "public/public_service_page.html", {
        "org": org,
        "services": services,
        "service": service,
        "trialing_active": trialing_active,
        "trial_end_date": trial_end_date,
        # provide per-service weekly availability (UI index 0=Sun..6=Sat) as JSON
        "service_weekly_map_json": json.dumps({
            s.slug: [
                [f"{r.start_time.strftime('%H:%M')}-{r.end_time.strftime('%H:%M')}" for r in s.weekly_availability.filter(is_active=True, weekday=((ui-1)%7)).order_by('start_time')]
                for ui in range(7)
            ]
            for s in services
        })
    })



def booking_success(request, org_slug, service_slug, booking_id):
    org = get_object_or_404(Organization, slug=org_slug)
    service = get_object_or_404(Service, slug=service_slug, organization=org)
    booking = get_object_or_404(Booking, id=booking_id, organization=org, service=service)
    return render(request, "public/booking_success.html", {
        "org": org,
        "service": service,
        "booking": booking,
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
    # Fetch per-date overrides that overlap the requested day in org timezone
    day_start_candidate = range_start.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end_candidate = day_start_candidate.replace(hour=23, minute=59, second=59)
    override_qs = Booking.objects.filter(
        organization=org,
        service__isnull=True,
        start__lt=day_end_candidate,
        end__gt=day_start_candidate,
    )
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
            # Prefer service-specific weekly windows if defined
            svc_rows = service.weekly_availability.filter(is_active=True, weekday=weekday)
            if svc_rows.exists():
                base_windows = []
                for w in svc_rows.order_by('start_time'):
                    w_start = original_range_start.replace(hour=w.start_time.hour, minute=w.start_time.minute, second=0, microsecond=0)
                    w_end = original_range_start.replace(hour=w.end_time.hour, minute=w.end_time.minute, second=0, microsecond=0)
                    base_windows.append((w_start, w_end))
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

                # Min notice handling (only for same-day slots)
                try:
                    is_same_day = slot_start.date() == now_org.date()
                except Exception:
                    is_same_day = False
                if is_same_day and slot_start < earliest_allowed:
                    slot_start += slot_increment
                    continue

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

    # Default to org-local today if no valid date provided
    if not target_date:
        try:
            org_tz = ZoneInfo(getattr(org, 'timezone', getattr(settings, 'TIME_ZONE', 'UTC')))
        except Exception:
            org_tz = timezone.get_current_timezone()
        target_date = timezone.now().astimezone(org_tz).date()

    # Try to locate a freeze for this service/date
    try:
        from bookings.models import ServiceSettingFreeze
        freeze = ServiceSettingFreeze.objects.filter(service=service, date=target_date).first()
        # If a freeze exists but there are no bookings on that date anymore
        # (for example bookings were deleted/cancelled), ignore the freeze so
        # the date becomes malleable again.
        if freeze:
            try:
                # Determine org-local day range for the target_date
                try:
                    org_tz = ZoneInfo(getattr(org, 'timezone', getattr(settings, 'TIME_ZONE', 'UTC')))
                except Exception:
                    org_tz = timezone.get_current_timezone()
                day_start = datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0)
                if day_start.tzinfo is None:
                    day_start = make_aware(day_start, org_tz)
                day_end = day_start + timedelta(days=1)
                # If there are no bookings for this service in that day window, ignore the freeze
                has_bookings = Booking.objects.filter(service=service, organization=org, start__gte=day_start, start__lt=day_end).exists()
                if not has_bookings:
                    freeze = None
            except Exception:
                # If anything goes wrong checking bookings, fall back to honoring the freeze
                pass
    except Exception:
        freeze = None

    if freeze and isinstance(freeze.frozen_settings, dict):
        out = dict(freeze.frozen_settings)
        # Ensure types are normalized
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

    # Include service id/slug for convenience
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

        # Per-date overrides (service NULL bookings)
        # Consider any overrides that overlap the day window (org timezone)
        override_qs = Booking.objects.filter(
            organization=org,
            service__isnull=True,
            start__lt=day_end,
            end__gt=day_start,
        )
        if override_qs.exists():
            # If there is any availability override (non-blocking), the day should be considered available.
            # Only mark unavailable if there are blocking overrides that span the full day AND no availability overrides.
            has_avail = False
            full_block = False
            for bk in override_qs:
                # Normalize booking times to org timezone for comparison
                bk_start_org = bk.start.astimezone(org_tz) if bk.start.tzinfo else bk.start
                bk_end_org = bk.end.astimezone(org_tz) if bk.end.tzinfo else bk.end
                
                if bk.is_blocking:
                    # Treat a blocking override as full-day block if it covers the entire day
                    # Allow for small time differences (< 2 minutes) to account for 23:59 vs 23:59:59
                    covers_start = bk_start_org <= day_start + timedelta(minutes=1)
                    covers_end = bk_end_org >= day_end - timedelta(minutes=1)
                    if covers_start and covers_end:
                        full_block = True
                else:
                    has_avail = True
            if has_avail:
                summary[current.strftime('%Y-%m-%d')] = True
                current += timedelta(days=1)
                continue
            if full_block:
                summary[current.strftime('%Y-%m-%d')] = False
                current += timedelta(days=1)
                continue

        # Check if this weekday has ANY availability windows
        windows = WeeklyAvailability.objects.filter(
            organization=org,
            is_active=True,
            weekday=day_start.weekday()
        )
        
        if not windows.exists():
            # No weekly availability defined for this weekday
            summary[current.strftime('%Y-%m-%d')] = False
            current += timedelta(days=1)
            continue

        # For today's date, ensure windows actually contain future times after min-notice.
        # Otherwise, reporting this day as "available" is misleading (the modal will later show no slots).
        has_future_window = False
        for w in windows:
            # Construct window start/end in org timezone for this day
            w_start = day_start.replace(hour=w.start_time.hour, minute=w.start_time.minute, second=0, microsecond=0)
            w_end = day_start.replace(hour=w.end_time.hour, minute=w.end_time.minute, second=0, microsecond=0)
            # If any portion of the window is after earliest_allowed and before max booking, consider it potentially available
            if w_end > earliest_allowed and w_start < max_booking_date:
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

