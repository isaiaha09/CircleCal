from django.shortcuts import render, redirect
from django.http import JsonResponse, HttpResponseBadRequest, HttpResponse
from django.views.decorators.http import require_http_methods
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from calendar_app.forms import OrganizationCreateForm
import json
from django.utils.text import slugify
from django.middleware.csrf import get_token
from accounts.models import Business as Organization, Membership, Invite
from bookings.models import Booking, Service, ServiceSettingFreeze, AuditBooking
from bookings.views import _has_overlap
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
from datetime import date, datetime
from bookings.models import OrgSettings
from zoneinfo import ZoneInfo
from django.conf import settings
from datetime import timedelta
from bookings.emails import send_booking_confirmation


@require_http_methods(['POST'])
@require_roles(['owner', 'admin', 'manager'])
def update_service_settings(request, org_slug, service_id):
    org = request.organization
    if not org:
        return HttpResponseBadRequest('Organization required')

    try:
        svc = Service.objects.get(id=service_id, organization=org)
    except Service.DoesNotExist:
        return HttpResponseBadRequest('Invalid service')

    try:
        payload = json.loads(request.body.decode('utf-8'))
    except Exception:
        return HttpResponseBadRequest('Invalid JSON')

    # Read settings
    fields = {}
    if 'time_increment_minutes' in payload:
        try:
            fields['time_increment_minutes'] = int(payload.get('time_increment_minutes') or 0) or 30
        except Exception:
            fields['time_increment_minutes'] = 30
    if 'use_fixed_increment' in payload:
        fields['use_fixed_increment'] = bool(payload.get('use_fixed_increment'))
    if 'allow_squished_bookings' in payload:
        fields['allow_squished_bookings'] = bool(payload.get('allow_squished_bookings'))
    if 'allow_ends_after_availability' in payload:
        # Expect boolean-like values
        try:
            fields['allow_ends_after_availability'] = bool(payload.get('allow_ends_after_availability'))
        except Exception:
            pass
    if 'is_active' in payload:
        try:
            fields['is_active'] = bool(payload.get('is_active'))
        except Exception:
            pass

    # If no fields to update, nothing to do
    if not fields:
        return JsonResponse({'status': 'noop'})

    # Conflict detection: find other services whose service-weekly windows overlap this service
    apply_to_conflicts = bool(payload.get('apply_to_conflicts'))
    conflicting = []
    # Build set of (weekday, start, end) for this service
    my_windows = list(svc.weekly_availability.filter(is_active=True).values_list('weekday', 'start_time', 'end_time'))
    # Determine proposed field values (use current svc values when not provided)
    proposed_time_inc = fields.get('time_increment_minutes', getattr(svc, 'time_increment_minutes', 30))
    proposed_use_fixed = fields.get('use_fixed_increment', getattr(svc, 'use_fixed_increment', False))
    proposed_allow_squished = fields.get('allow_squished_bookings', getattr(svc, 'allow_squished_bookings', False))

    if my_windows:
        other_svcs = Service.objects.filter(organization=org).exclude(id=svc.id)
        for other in other_svcs:
            other_rows = other.weekly_availability.filter(is_active=True)
            overlap_found = False
            for r in other_rows:
                for (wd, st, et) in my_windows:
                    if r.weekday == wd:
                        # times overlap if r.start < et and r.end > st
                        if (r.start_time < et) and (r.end_time > st):
                            overlap_found = True
                            break
                if overlap_found:
                    break
            if not overlap_found:
                continue

            # Only consider it a conflict if the other service's current settings
            # would differ from the proposed settings for this service. If they
            # are already identical, no action needed.
            other_time_inc = getattr(other, 'time_increment_minutes', 30)
            other_use_fixed = getattr(other, 'use_fixed_increment', False)
            other_allow_squished = getattr(other, 'allow_squished_bookings', False)

            if (other_time_inc != proposed_time_inc) or (bool(other_use_fixed) != bool(proposed_use_fixed)) or (bool(other_allow_squished) != bool(proposed_allow_squished)):
                # Include detailed settings and weekly windows to help the client UI decide
                other_windows = list(other.weekly_availability.filter(is_active=True).values_list('weekday', 'start_time', 'end_time'))
                # Convert model weekday (0=Mon..6=Sun) to UI index (0=Sun..6=Sat)
                ui_windows = []
                for (wd, st, et) in other_windows:
                    ui_idx = (wd + 1) % 7
                    ui_windows.append({'weekday': ui_idx, 'start': st.strftime('%H:%M'), 'end': et.strftime('%H:%M')})

                conflicting.append({
                    'id': other.id,
                    'name': other.name,
                    'time_increment_minutes': other_time_inc,
                    'use_fixed_increment': bool(other_use_fixed),
                    'allow_squished_bookings': bool(other_allow_squished),
                    'weekly_windows': ui_windows,
                })

    # If conflicts found and not explicitly applying, return info for confirmation
    if conflicting and not apply_to_conflicts:
        return JsonResponse({'status': 'conflicts', 'conflicting': conflicting})

    # Apply fields to primary service
    for k, v in fields.items():
        setattr(svc, k, v)
    svc.save()

    # Optionally apply to conflicting services
    if conflicting and apply_to_conflicts:
        other_ids = [c['id'] for c in conflicting]
        Service.objects.filter(id__in=other_ids).update(**fields)

    return JsonResponse({'status': 'ok', 'applied_to_conflicts': bool(conflicting and apply_to_conflicts)})


@require_http_methods(['POST'])
@require_roles(['owner', 'admin', 'manager'])
def preview_service_update(request, org_slug, service_id):
    """Return dates that have existing bookings for this service so the UI
    can present a confirmation modal listing affected bookings.
    Expects JSON body with optional updated fields (we only need max_booking_days
    to scope the preview window)."""
    org = request.organization
    if not org:
        return HttpResponseBadRequest('Organization required')

    try:
        svc = Service.objects.get(id=service_id, organization=org)
    except Service.DoesNotExist:
        return HttpResponseBadRequest('Invalid service')

    try:
        payload = json.loads(request.body.decode('utf-8'))
    except Exception:
        payload = {}

    # Determine scanning window: from today (org tz) to a reasonable horizon
    try:
        org_tz = ZoneInfo(getattr(org, 'timezone', getattr(settings, 'TIME_ZONE', 'UTC')))
    except Exception:
        org_tz = timezone.get_current_timezone()
    today_org = timezone.now().astimezone(org_tz).replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        new_max = int(payload.get('max_booking_days', svc.max_booking_days))
    except Exception:
        new_max = svc.max_booking_days
    horizon = today_org + timedelta(days=max(svc.max_booking_days or 0, new_max or 0, 365))

    # Fetch bookings for this service within the horizon
    b_qs = Booking.objects.filter(
        organization=org,
        service=svc,
        start__gte=today_org,
        start__lte=horizon
    ).order_by('start')

    # Build the proposed settings map (use current svc values when a key
    # isn't present in the payload). We'll compare these against any existing
    # ServiceSettingFreeze for a date to decide whether a freeze would be
    # redundant and therefore can be omitted from the preview.
    def _get_proposed(key, caster=lambda x: x):
        if key in payload:
            try:
                return caster(payload.get(key))
            except Exception:
                return caster(getattr(svc, key, None))
        return caster(getattr(svc, key, None))

    proposed = {
        'duration': _get_proposed('duration', lambda v: int(v) if v is not None else None),
        'buffer_after': _get_proposed('buffer_after', lambda v: int(v) if v is not None else 0),
        'time_increment_minutes': _get_proposed('time_increment_minutes', lambda v: int(v) if v is not None else 30),
        'use_fixed_increment': bool(_get_proposed('use_fixed_increment', lambda v: bool(v))),
        'allow_ends_after_availability': bool(_get_proposed('allow_ends_after_availability', lambda v: bool(v))),
        'allow_squished_bookings': bool(_get_proposed('allow_squished_bookings', lambda v: bool(v))),
    }

    # Keys that define a freeze's relevant fingerprint
    slot_keys = ['duration', 'buffer_after', 'time_increment_minutes', 'use_fixed_increment', 'allow_ends_after_availability', 'allow_squished_bookings']

    # Helper to test if a freeze's settings match the proposed values
    def _freeze_matches_proposed(frozen_settings):
        if not isinstance(frozen_settings, dict):
            return False
        for k in slot_keys:
            fval = frozen_settings.get(k, None)
            pval = proposed.get(k, None)
            # normalize booleans/ints for comparison
            if isinstance(pval, bool):
                if bool(fval) != pval:
                    return False
            else:
                # allow None/int comparison
                try:
                    if fval is None and pval is None:
                        continue
                    if fval is None and pval is not None:
                        return False
                    if pval is None and fval is not None:
                        return False
                    if int(fval) != int(pval):
                        return False
                except Exception:
                    return False
        return True

    conflicts = {}
    for b in b_qs:
        try:
            local_start = b.start.astimezone(org_tz)
        except Exception:
            local_start = b.start
        # determine local end and duration (minutes) when available so client can render ranges
        try:
            local_end = b.end.astimezone(org_tz) if b.end else None
        except Exception:
            local_end = b.end

        # Determine booking date and any existing freeze for metadata. We
        # include all bookings in the preview so owners can inspect them —
        # even if a per-date freeze exists that matches the proposed values.
        try:
            b_date = local_start.date()
        except Exception:
            b_date = (b.start.date() if getattr(b, 'start', None) else None)

        try:
            existing_freeze = ServiceSettingFreeze.objects.filter(service=svc, date=b_date).first()
        except Exception:
            existing_freeze = None

        # Compute duration_minutes for this booking (best-effort)
        duration_minutes = None
        if b.service and getattr(b.service, 'duration', None) is not None:
            try:
                duration_minutes = int(b.service.duration)
            except Exception:
                duration_minutes = None
        elif b.start and b.end:
            try:
                duration_minutes = int((b.end - b.start).total_seconds() / 60)
            except Exception:
                duration_minutes = None

        # Determine effective per-date increment behaviour: if a freeze exists for
        # the date, its frozen_settings control the increments; otherwise the
        # current service settings apply.
        if existing_freeze and isinstance(existing_freeze.frozen_settings, dict):
            eff_use_fixed = bool(existing_freeze.frozen_settings.get('use_fixed_increment', False))
            eff_time_inc = existing_freeze.frozen_settings.get('time_increment_minutes', getattr(svc, 'time_increment_minutes', 30))
        else:
            eff_use_fixed = bool(getattr(svc, 'use_fixed_increment', False))
            eff_time_inc = getattr(svc, 'time_increment_minutes', 30)

        day = local_start.date().isoformat()
        conflicts.setdefault(day, []).append({
            'id': b.id,
            'start': local_start.isoformat(),
            'end': local_end.isoformat() if local_end else None,
            'time': local_start.strftime('%H:%M'),
            'client_name': b.client_name,
            'client_email': b.client_email,
            'duration': duration_minutes,
            # Metadata to help client group bookings
            'uses_fixed_increment': bool(eff_use_fixed),
            'time_increment_minutes': int(eff_time_inc) if eff_time_inc is not None else None,
        })

    # Defensive UX: if there are bookings but our earlier filtering removed
    # them all (for example because existing per-date freezes match the
    # proposed payload), still show the modal when the user explicitly
    # toggled the `use_fixed_increment` value — owners expect to confirm
    # such a toggle when bookings exist. Add at least one representative
    # booking to the payload so the client will render the modal.
    try:
        any_bookings = b_qs.exists()
    except Exception:
        any_bookings = False

    # If the user posted a change to `use_fixed_increment` and it differs
    # from the current service setting, ensure we show the modal when there
    # are bookings even if conflicts is empty.
    try:
        toggled_use_fixed = None
        if 'use_fixed_increment' in payload:
            toggled_use_fixed = bool(payload.get('use_fixed_increment'))
    except Exception:
        toggled_use_fixed = None

    if (not conflicts) and any_bookings and (toggled_use_fixed is not None) and (toggled_use_fixed != bool(getattr(svc, 'use_fixed_increment', False))):
        # Pick the earliest booking and include it so the modal appears.
        try:
            sample = b_qs.first()
            if sample:
                try:
                    sample_start = sample.start.astimezone(org_tz)
                except Exception:
                    sample_start = sample.start
                try:
                    sample_end = sample.end.astimezone(org_tz) if sample.end else None
                except Exception:
                    sample_end = sample.end
                sample_duration = None
                if sample.service and getattr(sample.service, 'duration', None) is not None:
                    try:
                        sample_duration = int(sample.service.duration)
                    except Exception:
                        sample_duration = None
                elif sample.start and sample.end:
                    try:
                        sample_duration = int((sample.end - sample.start).total_seconds() / 60)
                    except Exception:
                        sample_duration = None

                day = sample_start.date().isoformat() if sample_start else 'unknown'
                conflicts.setdefault(day, []).append({
                    'id': sample.id,
                    'start': sample_start.isoformat() if sample_start else None,
                    'end': sample_end.isoformat() if sample_end else None,
                    'time': sample_start.strftime('%H:%M') if sample_start else '',
                    'client_name': sample.client_name,
                    'client_email': sample.client_email,
                    'duration': sample_duration,
                    'uses_fixed_increment': bool(getattr(svc, 'use_fixed_increment', False)),
                    'time_increment_minutes': getattr(svc, 'time_increment_minutes', 30),
                })
        except Exception:
            pass

    return JsonResponse({'status': 'ok', 'conflicts': conflicts})


@require_http_methods(['POST'])
@require_roles(['owner', 'admin', 'manager'])
def apply_service_update(request, org_slug, service_id):
    """Apply service updates but freeze old settings on dates that have bookings.
    Expects JSON body with the same fields as `update_service_settings` plus
    `confirm` boolean. When `confirm` is true, create `ServiceSettingFreeze`
    rows for booked dates preserving prior settings, then save the new values.
    """
    org = request.organization
    if not org:
        return HttpResponseBadRequest('Organization required')

    try:
        svc = Service.objects.get(id=service_id, organization=org)
    except Service.DoesNotExist:
        return HttpResponseBadRequest('Invalid service')

    try:
        payload = json.loads(request.body.decode('utf-8'))
    except Exception:
        return HttpResponseBadRequest('Invalid JSON')

    if not payload.get('confirm'):
        return HttpResponseBadRequest('Must include confirm=true to apply changes')

    # Determine horizon similar to preview
    try:
        org_tz = ZoneInfo(getattr(org, 'timezone', getattr(settings, 'TIME_ZONE', 'UTC')))
    except Exception:
        org_tz = timezone.get_current_timezone()
    today_org = timezone.now().astimezone(org_tz).replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        new_max = int(payload.get('max_booking_days', svc.max_booking_days))
    except Exception:
        new_max = svc.max_booking_days
    horizon = today_org + timedelta(days=max(svc.max_booking_days or 0, new_max or 0, 365))

    # Bookings that should cause freezes
    b_qs = Booking.objects.filter(
        organization=org,
        service=svc,
        start__gte=today_org,
        start__lte=horizon
    )

    # Build set of dates with bookings
    booked_dates = set()
    for b in b_qs:
        try:
            d = b.start.astimezone(org_tz).date()
        except Exception:
            d = b.start.date()
        booked_dates.add(d)

    # Save freezes: preserve current service values for affected dates
    freezes_created = 0
    frozen_dates = []
    freeze_error = None
    try:
        from django.db.utils import OperationalError
        for d in booked_dates:
            # Snapshot weekly windows for the specific weekday so that changes
            # to weekly availability made later do not affect dates that already
            # have bookings. Format: list of {'start': 'HH:MM', 'end': 'HH:MM'}
            weekly_windows = []
            try:
                wd = d.weekday()
                svc_rows = svc.weekly_availability.filter(is_active=True, weekday=wd)
                if svc_rows.exists():
                    for rw in svc_rows.order_by('start_time'):
                        weekly_windows.append({'start': rw.start_time.strftime('%H:%M'), 'end': rw.end_time.strftime('%H:%M')})
                else:
                    org_rows = WeeklyAvailability.objects.filter(organization=org, is_active=True, weekday=wd)
                    for rw in org_rows:
                        weekly_windows.append({'start': rw.start_time.strftime('%H:%M'), 'end': rw.end_time.strftime('%H:%M')})
            except Exception:
                weekly_windows = []

            frozen = {
                'duration': svc.duration,
                'buffer_after': svc.buffer_after,
                'time_increment_minutes': svc.time_increment_minutes,
                'use_fixed_increment': bool(svc.use_fixed_increment),
                'allow_ends_after_availability': bool(getattr(svc, 'allow_ends_after_availability', False)),
                'allow_squished_bookings': bool(getattr(svc, 'allow_squished_bookings', False)),
                'weekly_windows': weekly_windows,
            }
            try:
                obj, created = ServiceSettingFreeze.objects.update_or_create(
                    service=svc, date=d, defaults={'frozen_settings': frozen}
                )
                if created:
                    freezes_created += 1
                frozen_dates.append(d.isoformat())
            except OperationalError as oe:
                # Likely missing table (migrations not applied) — record and continue
                freeze_error = str(oe)
                break
    except Exception as e:
        # Defensive: if import or DB access fails, record and continue
        try:
            freeze_error = str(e)
        except Exception:
            freeze_error = 'unknown error while creating freezes'

    # Apply provided fields to service (similar to update_service_settings)
    fields = {}
    if 'time_increment_minutes' in payload:
        try:
            fields['time_increment_minutes'] = int(payload.get('time_increment_minutes') or 0) or 30
        except Exception:
            fields['time_increment_minutes'] = 30
    if 'use_fixed_increment' in payload:
        fields['use_fixed_increment'] = bool(payload.get('use_fixed_increment'))
    if 'allow_squished_bookings' in payload:
        fields['allow_squished_bookings'] = bool(payload.get('allow_squished_bookings'))
    if 'buffer_after' in payload:
        try:
            fields['buffer_after'] = int(payload.get('buffer_after'))
        except Exception:
            pass
    if 'duration' in payload:
        try:
            fields['duration'] = int(payload.get('duration'))
        except Exception:
            pass
    # Additional fields that the edit form may post
    if 'allow_ends_after_availability' in payload:
        try:
            fields['allow_ends_after_availability'] = bool(payload.get('allow_ends_after_availability'))
        except Exception:
            pass
    if 'is_active' in payload:
        try:
            fields['is_active'] = bool(payload.get('is_active'))
        except Exception:
            pass
    if 'min_notice_hours' in payload:
        try:
            fields['min_notice_hours'] = int(payload.get('min_notice_hours'))
        except Exception:
            pass
    if 'max_booking_days' in payload:
        try:
            fields['max_booking_days'] = int(payload.get('max_booking_days'))
        except Exception:
            pass
    if 'time_increment_minutes' in payload and 'time_increment_minutes' not in fields:
        try:
            fields['time_increment_minutes'] = int(payload.get('time_increment_minutes'))
        except Exception:
            pass

    for k, v in fields.items():
        setattr(svc, k, v)
    svc.save()

    resp = {'status': 'ok', 'freezes_created': freezes_created, 'booked_dates_count': len(booked_dates), 'booked_dates': frozen_dates}
    if freeze_error:
        resp['freeze_error'] = str(freeze_error)
        resp['warning'] = 'Freezes could not be created (DB may need migrations). Changes were still applied to the service.'

    return JsonResponse(resp)


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
    # Prevent any accidental </script> sequences from being embedded raw into templates
    if isinstance(coach_availability_json, str):
        coach_availability_json = coach_availability_json.replace('</script>', '<\\/script>')
    services_qs = Service.objects.filter(organization=org, is_active=True).order_by('name')
    services = []
    for s in services_qs:
        services.append({
            'id': s.id,
            'name': s.name,
            'slug': s.slug,
            'duration': s.duration,
            'time_increment_minutes': getattr(s, 'time_increment_minutes', 30),
            'use_fixed_increment': bool(getattr(s, 'use_fixed_increment', False)),
            'allow_squished_bookings': bool(getattr(s, 'allow_squished_bookings', False)),
            'allow_ends_after_availability': bool(getattr(s, 'allow_ends_after_availability', False)),
            # Provide a simple weekly availability map for the client to compute next-available dates
            'weekly_map': _build_service_weekly_map(s),
        })
    services_json = json.dumps(services)
    # Guard against raw closing script tags in service names/descriptions
    if isinstance(services_json, str):
        services_json = services_json.replace('</script>', '<\\/script>')
    get_token(request)
    # Support auto-opening the Day Schedule modal via query params
    auto_open_service = request.GET.get('open_day_schedule_for', '')
    auto_open_date = request.GET.get('open_day_schedule_date', '')

    return render(request, "calendar_app/calendar.html", {
        'organization': org,
        'coach_availability_json': coach_availability_json,
        'org_timezone': org.timezone,  # Pass organization's timezone to template
        'services': services_qs,
        'services_json': services_json,
        'auto_open_service': auto_open_service,
        'auto_open_date': auto_open_date,
        'audit_entries': AuditBooking.objects.filter(organization=org).order_by('-created_at')[:10],
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


def admin_pin_view(request):
    """Render a simple PIN entry form to gate access to the admin area.

    The required PIN should be configured via the `ADMIN_PIN` setting or the
    `ADMIN_PIN` environment variable. On success the middleware will allow
    subsequent requests to the admin by setting `request.session['admin_pin_ok']`.
    """
    from django.conf import settings
    from django.views.decorators.http import require_http_methods
    from django.shortcuts import render, redirect
    from django.middleware.csrf import get_token

    # Determine configured PIN: prefer environment/settings, otherwise DB
    admin_pin_setting = getattr(settings, 'ADMIN_PIN', None)
    next_url = request.GET.get('next') or request.POST.get('next') or '/admin/'
    error = None

    # Rate-limiting using Django cache; use AXES settings for thresholds
    from django.core.cache import cache
    ip = request.META.get('HTTP_X_FORWARDED_FOR', request.META.get('REMOTE_ADDR', 'unknown'))
    if isinstance(ip, str) and ',' in ip:
        ip = ip.split(',')[0].strip()
    cache_key = f"admin_pin_attempts:{ip}"
    failure_limit = getattr(settings, 'AXES_FAILURE_LIMIT', 5)
    cooloff_hours = getattr(settings, 'AXES_COOLOFF_TIME', 0.25)
    cooloff_seconds = int(float(cooloff_hours) * 3600)

    # If no PIN configured in settings, check DB
    db_pin_exists = False
    try:
        from .models import AdminPin
        if AdminPin.get_latest_hash():
            db_pin_exists = True
    except Exception:
        db_pin_exists = False

    if not admin_pin_setting and not db_pin_exists:
        return redirect(next_url)

    # If we've exceeded attempts, show a lockout message
    attempts = cache.get(cache_key, 0) or 0
    if attempts >= failure_limit:
        error = f"Too many attempts. Try again in {cooloff_seconds} seconds."
        get_token(request)
        return render(request, 'calendar_app/admin_pin.html', {'error': error, 'next': next_url})

    if request.method == 'POST':
        pin = request.POST.get('pin')
        ok = False
        # First check env/settings PIN
        if admin_pin_setting and pin and pin == admin_pin_setting:
            ok = True
        else:
            try:
                # Check DB-stored hashed PIN
                if pin and AdminPin.check_pin(pin):
                    ok = True
            except Exception:
                ok = False

        if ok:
            # Success: clear attempts and set session flag
            try:
                cache.delete(cache_key)
            except Exception:
                pass
            request.session['admin_pin_ok'] = True
            return redirect(next_url)

        # Failure: increment attempts and set expiry
        attempts = attempts + 1
        cache.set(cache_key, attempts, timeout=cooloff_seconds)
        error = 'Incorrect PIN'

    # Ensure CSRF token is set for the template
    get_token(request)
    return render(request, 'calendar_app/admin_pin.html', {'error': error, 'next': next_url})


@login_required
def admin_pin_manage(request):
    """Admin UI to set or clear the stored DB PIN.

    Only superusers may access this view. The UI writes to the `AdminPin`
    model when setting a new PIN or clears all rows to remove DB-managed PIN.
    """
    if not request.user.is_superuser:
        return HttpResponseForbidden()

    from .models import AdminPin
    message = None

    if request.method == 'POST':
        if 'set_pin' in request.POST:
            new_pin = request.POST.get('new_pin')
            if new_pin and len(new_pin) >= 4:
                AdminPin.set_pin(new_pin)
                message = 'PIN set successfully.'
            else:
                message = 'PIN must be at least 4 characters.'
        elif 'clear_pin' in request.POST:
            AdminPin.clear_pins()
            message = 'PIN cleared.'
    latest = AdminPin.objects.order_by('-id').first()
    current_exists = bool(latest)
    last_set = latest.created_at if latest else None
    return render(request, 'calendar_app/admin_pin_manage.html', {
        'message': message,
        'current_exists': current_exists,
        'last_set': last_set,
    })


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
    # Determine whether the org has weekly availability configured so we can
    # disable access to Services until the calendar schedule is set up.
    try:
        from bookings.models import WeeklyAvailability
        has_availability = WeeklyAvailability.objects.filter(organization=org, is_active=True).exists()
    except Exception:
        has_availability = True

    return render(request, "calendar_app/dashboard.html", {
        "memberships": memberships,
        "org": org,
        "subscription": subscription,
        "trialing_active": trialing_active,
        "has_availability": has_availability,
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

        # Create kwargs dynamically so absence of the DB migration won't crash the form
        svc_kwargs = dict(
            organization=org,
            name=name,
            slug=slug,
            description=request.POST.get("description", ""),
            duration=int(request.POST.get("duration", 30)),
            price=float(request.POST.get("price", 0)),
            buffer_after=int(request.POST.get("buffer_after", 0)),
            min_notice_hours=int(request.POST.get("min_notice_hours", 1)),
            max_booking_days=int(request.POST.get("max_booking_days", 30)),
        )
        field_names = [f.name for f in Service._meta.get_fields()]
        if 'allow_ends_after_availability' in field_names:
            svc_kwargs['allow_ends_after_availability'] = request.POST.get('allow_ends_after_availability') is not None

        svc = Service.objects.create(**svc_kwargs)
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
    # Check whether the DB field exists so we avoid touching it when not migrated
    field_names = [f.name for f in Service._meta.get_fields()]
    field_present = 'allow_ends_after_availability' in field_names

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
        # buffer_before is deprecated and no longer used in availability logic
        service.buffer_after = _set_int("buffer_after", service.buffer_after)
        if field_present:
            # Read all posted values for the checkbox (hidden fallback + checkbox)
            try:
                raw_list = request.POST.getlist('allow_ends_after_availability')
                # raw_list may be ['0'] when unchecked, or ['0','1'] when checked (hidden + checkbox)
                present = bool(raw_list)
                raw_val = ','.join(raw_list)
                # Store a small info message (kept for a short time) — will also set session debug below
                try:
                    messages.debug(request, f"DEBUG POST allow_ends_after_availability list={raw_list}")
                except Exception:
                    pass
            except Exception:
                raw_list = []
                present = False
                raw_val = None
            # If any of posted values equals '1', treat as checked
            try:
                service.allow_ends_after_availability = any(v == '1' or v.lower() == 'true' for v in raw_list)
            except Exception:
                service.allow_ends_after_availability = False
        service.min_notice_hours = _set_int("min_notice_hours", service.min_notice_hours)
        service.max_booking_days = _set_int("max_booking_days", service.max_booking_days)

        service.is_active = request.POST.get("is_active") is not None

        # Refund fields
        service.refunds_allowed = request.POST.get("refunds_allowed") is not None
        service.refund_cutoff_hours = _set_int("refund_cutoff_hours", service.refund_cutoff_hours)
        service.refund_policy_text = (request.POST.get("refund_policy_text") or "").strip()

        # Prevent deactivating the last active service for the org
        try:
            if not service.is_active:
                other_active = Service.objects.filter(organization=org, is_active=True).exclude(id=service.id).count()
                if other_active == 0:
                    messages.error(request, "At least one service must remain active for your public booking page. Activate another service first.")
                    return redirect("calendar_app:edit_service", org_slug=org.slug, service_id=service.id)
        except Exception:
            pass

        service.save()
        service.refresh_from_db()
        # Store a short debug payload in session so PRG redirect can display raw POST state
        try:
            present = 'allow_ends_after_availability' in request.POST
            raw_val = request.POST.get('allow_ends_after_availability')
            request.session['cc_debug_post'] = {
                'present': bool(present),
                'raw': raw_val,
                'saved': bool(service.allow_ends_after_availability)
            }
        except Exception:
            try:
                request.session['cc_debug_post'] = {'error': 'failed to capture POST debug'}
            except Exception:
                pass
        messages.success(request, "Service updated.")
        # Post-Redirect-Get: redirect so the saved state is authoritative and URL/query params propagate
        # Add temporary query params with debug info to surface POST/DB state immediately (safe, short-lived)
        try:
            from urllib.parse import urlencode
            qs = urlencode({
                'cc_dbg_present': int(bool(present)),
                'cc_dbg_raw': raw_val if raw_val is not None else '',
                'cc_dbg_saved': int(bool(service.allow_ends_after_availability))
            })
            return redirect(f"{request.path}?{qs}")
        except Exception:
            return redirect("calendar_app:edit_service", org_slug=org.slug, service_id=service.id)

    # Pop any debug payload saved during the POST redirect so the template can show raw POST info
    debug_post = None
    try:
        debug_post = request.session.pop('cc_debug_post', None)
    except Exception:
        debug_post = None

    # Determine whether the slug may be edited: allow edits only when the service
    # has no real bookings (to avoid breaking existing public booking links).
    try:
        now = timezone.now()
        has_bookings = Booking.objects.filter(service=service, is_blocking=False, end__gte=now).exists()
    except Exception:
        has_bookings = False
    can_edit_slug = not has_bookings

    return render(request, "calendar_app/edit_service.html", {
        "org": org,
        "service": service,
        'needs_migration': not field_present,
        'cc_debug_post': debug_post,
        'can_edit_slug': can_edit_slug,
    })



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
                # Per-service client slot settings
                try:
                    svc.time_increment_minutes = int(request.POST.get('time_increment_minutes', svc.time_increment_minutes if hasattr(svc, 'time_increment_minutes') else 30))
                except Exception:
                    svc.time_increment_minutes = 30
                svc.use_fixed_increment = request.POST.get('use_fixed_increment') is not None
                svc.allow_squished_bookings = request.POST.get('allow_squished_bookings') is not None
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

        # Snapshot current service settings so we can freeze them for booked dates
        try:
            current_settings_snapshot = {
                'duration': int(getattr(service, 'duration', 0) or 0),
                'buffer_after': int(getattr(service, 'buffer_after', 0) or 0),
                'time_increment_minutes': int(getattr(service, 'time_increment_minutes', 0) or 0),
                'use_fixed_increment': bool(getattr(service, 'use_fixed_increment', False)),
                'allow_ends_after_availability': bool(getattr(service, 'allow_ends_after_availability', False)),
                'allow_squished_bookings': bool(getattr(service, 'allow_squished_bookings', False)),
            }
        except Exception:
            current_settings_snapshot = None

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

                # Per-service slot settings
                try:
                    service.time_increment_minutes = int(request.POST.get('time_increment_minutes', service.time_increment_minutes if hasattr(service, 'time_increment_minutes') else 30))
                except Exception:
                    service.time_increment_minutes = 30
                service.use_fixed_increment = request.POST.get('use_fixed_increment') is not None
                service.allow_squished_bookings = request.POST.get('allow_squished_bookings') is not None

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

                # Allow slug update only when there are no bookings for this service.
                try:
                    has_bookings = Booking.objects.filter(service=service).exists()
                except Exception:
                    has_bookings = False
                if not has_bookings:
                    new_slug_input = (request.POST.get('slug') or '').strip()
                    if new_slug_input:
                        base_slug = slugify(new_slug_input) or slugify(service.name) or get_random_string(6)
                        slug_candidate = base_slug
                        counter = 1
                        while Service.objects.filter(organization=org, slug=slug_candidate).exclude(id=service.id).exists():
                            slug_candidate = f"{base_slug}-{counter}"
                            counter += 1
                        service.slug = slug_candidate

                # Gather proposed slot settings (do not persist yet)
                try:
                    new_time_increment = int(request.POST.get('time_increment_minutes', service.time_increment_minutes if hasattr(service, 'time_increment_minutes') else 30))
                except Exception:
                    new_time_increment = service.time_increment_minutes if hasattr(service, 'time_increment_minutes') else 30
                new_use_fixed = request.POST.get('use_fixed_increment') is not None
                new_allow_squished = request.POST.get('allow_squished_bookings') is not None

                # Conflict detection with other services (sharing overlapping weekly windows)
                conflict_services = []
                my_windows = []
                for w in service.weekly_availability.filter(is_active=True):
                    my_windows.append((w.weekday, w.start_time, w.end_time))
                if my_windows:
                    others = Service.objects.filter(organization=org).exclude(id=service.id)
                    for other in others:
                        for r in other.weekly_availability.filter(is_active=True):
                            for (wd, st, et) in my_windows:
                                if r.weekday == wd and (r.start_time < et) and (r.end_time > st):
                                    conflict_services.append(other.name)
                                    break
                            if conflict_services and conflict_services[-1] == other.name:
                                break

                apply_to_conflicts = request.POST.get('apply_to_conflicts') is not None
                # If conflicts exist and user didn't confirm applying, re-render with warning
                if conflict_services and not apply_to_conflicts:
                    # Do not persist changes yet; show prompt to user
                    messages.warning(request, 'Conflicting services detected. Confirm to apply settings to them as well.')
                    # Render template with conflict_services context so the template shows the checkbox
                    org_map = _build_org_weekly_map(org)
                    svc_map = _build_service_weekly_map(service)
                    weekday_labels = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat']
                    weekly_edit_rows = []
                    for ui in range(7):
                        org_ranges = ', '.join(org_map[ui]) if org_map and org_map[ui] else ''
                        svc_ranges = ', '.join(svc_map[ui]) if svc_map and svc_map[ui] else ''
                        weekly_edit_rows.append({'ui': ui, 'label': weekday_labels[ui], 'org_ranges': org_ranges, 'svc_ranges': svc_ranges})
                    return render(request, "calendar_app/edit_service.html", {
                        "org": org,
                        "service": service,
                        "weekly_edit_rows": weekly_edit_rows,
                        "conflict_services": conflict_services,
                    })

                # Persist changes (and optionally apply to conflicts)
                # Before saving, create freezes for any future dates that already have bookings
                try:
                    if current_settings_snapshot is not None:
                        # Determine horizon similar to preview/apply logic
                        try:
                            org_tz = ZoneInfo(getattr(org, 'timezone', getattr(settings, 'TIME_ZONE', 'UTC')))
                        except Exception:
                            org_tz = timezone.get_current_timezone()
                        today_org = timezone.now().astimezone(org_tz).replace(hour=0, minute=0, second=0, microsecond=0)
                        try:
                            new_max = int(max_booking_days)
                        except Exception:
                            new_max = service.max_booking_days or 0
                        horizon = today_org + timedelta(days=max(service.max_booking_days or 0, new_max or 0, 365))

                        # Bookings that should cause freezes
                        b_qs = Booking.objects.filter(
                            organization=org,
                            service=service,
                            start__gte=today_org,
                            start__lte=horizon
                        )
                        booked_dates = set()
                        for b in b_qs:
                            try:
                                d = b.start.astimezone(org_tz).date()
                            except Exception:
                                d = b.start.date()
                            booked_dates.add(d)

                        # Create freezes preserving the current settings for these dates
                        from django.db.utils import OperationalError
                        for d in booked_dates:
                            try:
                                ServiceSettingFreeze.objects.update_or_create(
                                    service=service,
                                    date=d,
                                    defaults={'frozen_settings': current_settings_snapshot}
                                )
                            except OperationalError:
                                # If migrations missing or DB error, skip freezes but continue
                                break
                except Exception:
                    # Defensive: don't block saving if freeze creation fails
                    pass

                # Prevent deactivating the last active service for the org
                try:
                    if not service.is_active:
                        other_active = Service.objects.filter(organization=org, is_active=True).exclude(id=service.id).count()
                        if other_active == 0:
                            messages.error(request, "At least one service must remain active for your public booking page. Activate another service first.")
                            return redirect("calendar_app:edit_service", org_slug=org.slug, service_id=service.id)
                except Exception:
                    pass

                service.save()
                if conflict_services and apply_to_conflicts:
                    # Apply slot fields to conflicting services
                    others_qs = Service.objects.filter(organization=org).exclude(id=service.id)
                    for other in others_qs:
                        for r in other.weekly_availability.filter(is_active=True):
                            for (wd, st, et) in my_windows:
                                if r.weekday == wd and (r.start_time < et) and (r.end_time > st):
                                    other.time_increment_minutes = new_time_increment
                                    other.use_fixed_increment = new_use_fixed
                                    other.allow_squished_bookings = new_allow_squished
                                    other.save()
                                    break
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

    try:
        now = timezone.now()
        has_bookings = Booking.objects.filter(service=service, is_blocking=False, end__gte=now).exists()
    except Exception:
        has_bookings = False
    can_edit_slug = not has_bookings

    try:
        other_active = Service.objects.filter(organization=org, is_active=True).exclude(id=service.id).count()
    except Exception:
        other_active = 0
    is_only_active_service = (other_active == 0)

    return render(request, "calendar_app/edit_service.html", {
        "org": org,
        "service": service,
        "weekly_edit_rows": weekly_edit_rows,
        "can_edit_slug": can_edit_slug,
        "is_only_active_service": is_only_active_service,
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
        "audit_entries": AuditBooking.objects.filter(organization=org).order_by('-created_at')[:50],
    })


@login_required
@require_roles(['owner', 'admin', 'manager', 'staff'])
def bookings_recent(request, org_slug):
    """Return bookings created after the `since` ISO timestamp query param.

    GET params:
      since: ISO8601 timestamp (e.g. 2025-12-13T15:00:00Z)
    """
    org = request.organization
    since_raw = request.GET.get('since')
    try:
        if since_raw:
            # support trailing Z by converting to +00:00
            s = since_raw.strip()
            if s.endswith('Z'):
                s = s[:-1] + '+00:00'
            since_dt = datetime.fromisoformat(s)
        else:
            # if none provided, return nothing to avoid large payloads
            return JsonResponse({'items': []})
    except Exception:
        return HttpResponseBadRequest('Invalid since timestamp')

    qs = Booking.objects.filter(organization=org, is_blocking=False, service__isnull=False, created_at__gt=since_dt).select_related('service').order_by('created_at')

    items = []
    try:
        org_tz_name = getattr(org, 'timezone', None) or 'UTC'
        org_tz = ZoneInfo(org_tz_name)
    except Exception:
        org_tz = ZoneInfo('UTC')

    def _fmt(dt):
        if not dt:
            return None
        try:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=ZoneInfo('UTC'))
            d = dt.astimezone(org_tz)
            return d.strftime('%b %d, %Y'), d.strftime('%I:%M %p')
        except Exception:
            return None, None

    for b in qs:
        start_date = None
        time_range = None
        try:
            sd, st = _fmt(b.start) if b.start else (None, None)
            if sd:
                start_date = sd
                if b.end:
                    ed, et = _fmt(b.end)
                    time_range = f"{st} - {et}"
                else:
                    time_range = st
        except Exception:
            start_date = b.start.date().isoformat() if b.start else ''
            time_range = ''

        items.append({
            'id': b.id,
            'booking_id': b.id,
            'public_ref': getattr(b, 'public_ref', None),
            'service_name': b.service.name if b.service else None,
            'service_id': b.service.id if b.service else None,
            'duration': (int(b.service.duration) if (b.service and getattr(b.service, 'duration', None) is not None) else (None if not (b.start and b.end) else int((b.end - b.start).total_seconds()/60))),
            'service_price': (float(b.service.price) if (b.service and getattr(b.service, 'price', None) is not None) else None),
            'start': b.start.isoformat() if b.start else None,
            'end': b.end.isoformat() if b.end else None,
            'start_date': start_date,
            'time_range': time_range,
            'client_name': b.client_name,
            'client_email': b.client_email,
            'created_at': b.created_at.isoformat(),
        })

    return JsonResponse({'items': items})


@login_required
@require_http_methods(['POST'])
@require_roles(['owner', 'admin', 'manager'])
def delete_booking(request, org_slug, booking_id):
    """
    Delete a specific booking (owner/admin/manager only).
    """
    org = request.organization
    booking = get_object_or_404(Booking, id=booking_id, organization=org)
    # Prevent deleting an ongoing appointment (current time between start and end)
    now = timezone.now()
    try:
        if booking.start and booking.end and (booking.start <= now <= booking.end):
            return HttpResponseBadRequest('Cannot delete ongoing appointment')
    except Exception:
        pass

    booking.delete()

    # Try to find the audit entry created by the post-delete signal and return it
    try:
        ab = AuditBooking.objects.filter(organization=org, booking_id=booking_id).order_by('-created_at').first()
        if ab:
            audit_data = {
                'id': ab.id,
                'booking_id': ab.booking_id,
                'event_type': ab.event_type,
                'service': ab.service.name if ab.service else None,
                'service_price': float(ab.service.price) if (ab.service and getattr(ab.service, 'price', None) is not None) else None,
                'business': org.name,
                'start': ab.start.isoformat() if ab.start else None,
                'start_display': None,
                'end': ab.end.isoformat() if ab.end else None,
                'end_display': None,
                'client_name': ab.client_name,
                'client_email': ab.client_email,
                'created_at': ab.created_at.isoformat(),
                'snapshot': ab.booking_snapshot,
            }
            # compute display strings in org timezone if possible
            try:
                org_tz_name = getattr(org, 'timezone', None) or 'UTC'
                org_tz = ZoneInfo(org_tz_name)
                def _fmt(dt):
                    if not dt: return None
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=ZoneInfo('UTC'))
                    return dt.astimezone(org_tz).strftime('%b %d, %Y %I:%M %p')
                audit_data['start_display'] = _fmt(ab.start)
                audit_data['end_display'] = _fmt(ab.end)
                audit_data['created_display'] = _fmt(ab.created_at)
            except Exception:
                pass
            return JsonResponse({'status': 'ok', 'audit': audit_data})
    except Exception:
        pass

    return JsonResponse({'status': 'ok'})


@login_required
@require_roles(['owner', 'admin', 'manager', 'staff'])
def bookings_audit_list(request, org_slug):
    """Return a paginated JSON list of audit entries for the organization.

    Query params:
      - page (int)
      - per_page (int)
    """
    org = request.organization
    page = int(request.GET.get('page', 1))
    per_page = int(request.GET.get('per_page', 25))
    # support incremental polling: ?since=ISO8601
    since_raw = request.GET.get('since')
    if since_raw:
        try:
            s = since_raw.strip()
            if s.endswith('Z'):
                s = s[:-1] + '+00:00'
            from datetime import datetime
            since_dt = datetime.fromisoformat(s)
            qs = qs.filter(created_at__gt=since_dt)
        except Exception:
            # ignore parsing errors and return full page
            pass
    qs = AuditBooking.objects.filter(organization=org).order_by('-created_at')
    total = qs.count()
    start = (page - 1) * per_page
    end = start + per_page
    items = qs[start:end]
    data = []
    for a in items:
        # Determine refund-related flags for cancelled events so the UI can
        # display whether the cancellation occurred within the refund cutoff
        non_refunded = False
        refund_within_cutoff = False
        try:
            if a.event_type == AuditBooking.EVENT_CANCELLED and a.service and a.start and a.created_at:
                hrs = (a.start - a.created_at).total_seconds() / 3600.0
                if getattr(a.service, 'refunds_allowed', False):
                    cutoff = float(getattr(a.service, 'refund_cutoff_hours', 0) or 0)
                    refundable = (hrs >= cutoff)
                    refund_within_cutoff = (hrs < cutoff)
                else:
                    refundable = False
                non_refunded = not refundable
        except Exception:
            non_refunded = False
            refund_within_cutoff = False

        data.append({
            'id': a.id,
            'booking_id': a.booking_id,
            'event_type': a.event_type,
            'service': a.service.name if a.service else None,
            'service_price': float(a.service.price) if (a.service and getattr(a.service, 'price', None) is not None) else None,
            'business': org.name,
            'start': a.start.isoformat() if a.start else None,
            'client_name': a.client_name,
            'client_email': a.client_email,
            'created_at': a.created_at.isoformat(),
            'snapshot': a.booking_snapshot,
            'non_refunded': non_refunded,
            'refund_within_cutoff': refund_within_cutoff,
        })
    return JsonResponse({'total': total, 'page': page, 'per_page': per_page, 'items': data})


@login_required
@require_roles(['owner', 'admin', 'manager', 'staff'])
def bookings_audit_for_booking(request, org_slug, booking_id):
    """Return audit entries for a specific original booking id."""
    org = request.organization
    qs = AuditBooking.objects.filter(organization=org, booking_id=booking_id).order_by('-created_at')
    items = []
    try:
        org_tz_name = getattr(org, 'timezone', None) or 'UTC'
        org_tz = ZoneInfo(org_tz_name)
    except Exception:
        org_tz = ZoneInfo('UTC')

    def _fmt(dt):
        if not dt: return None
        try:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=ZoneInfo('UTC'))
            return dt.astimezone(org_tz).strftime('%b %d, %Y %I:%M %p')
        except Exception:
            try: return dt.isoformat()
            except: return str(dt)

    for a in qs:
        items.append({
            'id': a.id,
            'booking_id': a.booking_id,
            'event_type': a.event_type,
            'service': a.service.name if a.service else None,
            'service_price': float(a.service.price) if (a.service and getattr(a.service, 'price', None) is not None) else None,
            'business': org.name,
            'start': a.start.isoformat() if a.start else None,
            'start_display': _fmt(a.start),
            'client_name': a.client_name,
            'client_email': a.client_email,
            'created_at': a.created_at.isoformat(),
            'created_display': _fmt(a.created_at),
            'snapshot': a.booking_snapshot,
        })

    return JsonResponse({'items': items})


@login_required
@require_roles(['owner', 'admin', 'manager'])
@require_http_methods(['POST'])
def bookings_audit_undo(request, org_slug):
    """Restore a deleted booking from an audit entry. Expects JSON {'audit_id': <id>}.

    Returns created booking details for client-side insertion.
    """
    org = request.organization
    try:
        payload = json.loads(request.body.decode('utf-8'))
        audit_id = int(payload.get('audit_id'))
    except Exception:
        return HttpResponseBadRequest('Invalid JSON')

    ab = get_object_or_404(AuditBooking, id=audit_id, organization=org)

    # Only allow undo for deleted or cancelled events and when start is in the future
    now = timezone.now()
    if ab.event_type not in (AuditBooking.EVENT_DELETED, AuditBooking.EVENT_CANCELLED):
        return HttpResponseBadRequest('Only deleted or cancelled bookings can be restored')

    if ab.start and ab.start <= now:
        return HttpResponseBadRequest('Cannot restore past bookings')

    # Build booking fields from snapshot / audit record
    svc = ab.service
    start_dt = ab.start
    end_dt = ab.end
    title = None
    client_name = ab.client_name
    client_email = ab.client_email

    # snapshot may include additional fields
    try:
        snap = ab.booking_snapshot or {}
        if not title:
            title = snap.get('title') or ''
        if not client_name:
            client_name = snap.get('client_name') or client_name
        if not client_email:
            client_email = snap.get('client_email') or client_email
    except Exception:
        snap = {}

    # If end not present, try to compute from service.duration
    if not end_dt and svc and getattr(svc, 'duration', None):
        end_dt = (start_dt + timedelta(minutes=svc.duration)) if start_dt else None

    # If possible, validate that restoring this booking won't overlap existing bookings.
    try:
        if start_dt:
            # Use the same service when checking overlap so buffers are respected.
            if _has_overlap(org, start_dt, end_dt, service=svc):
                return HttpResponseBadRequest('Cannot restore booking: time slot overlaps an existing booking.')
    except Exception:
        # If overlap check fails unexpectedly, proceed conservatively (allow restore).
        pass

    # Create booking
    # Try to preserve original public_ref from the audit snapshot when available.
    # This helps client-side deduplication (avoid new public_ref causing duplicate rows)
    try:
        snap = snap if 'snap' in locals() else {}
    except Exception:
        snap = {}
    preferred_ref = None
    try:
        preferred_ref = (snap.get('public_ref') if isinstance(snap, dict) else None) or (ab.booking_snapshot or {}).get('public_ref')
    except Exception:
        preferred_ref = None

    create_kwargs = {
        'organization': org,
        'title': title or '',
        'start': start_dt,
        'end': end_dt,
        'client_name': client_name or '',
        'client_email': client_email or '',
        'service': svc,
    }
    # Only include preferred_ref if it's not already used by another booking
    try:
        if preferred_ref and not Booking.objects.filter(public_ref=preferred_ref).exists():
            create_kwargs['public_ref'] = preferred_ref
    except Exception:
        # defensive: if anything goes wrong checking uniqueness, skip prefilling
        pass

    b = Booking.objects.create(**create_kwargs)

    # Format display strings in org timezone
    try:
        org_tz_name = getattr(org, 'timezone', None) or 'UTC'
        org_tz = ZoneInfo(org_tz_name)
    except Exception:
        org_tz = ZoneInfo('UTC')

    def _fmt(dt, date_only=False):
        if not dt: return None
        try:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=ZoneInfo('UTC'))
            d = dt.astimezone(org_tz)
            if date_only:
                return d.strftime('%b %d, %Y')
            return d.strftime('%b %d, %Y'), d.strftime('%I:%M %p')
        except Exception:
            return None

    start_date = None
    time_range = None
    if b.start:
        try:
            local = b.start.astimezone(org_tz)
            start_date = local.strftime('%b %d, %Y')
            if b.end:
                end_local = b.end.astimezone(org_tz)
                time_range = f"{local.strftime('%I:%M %p')} - {end_local.strftime('%I:%M %p')}"
            else:
                time_range = local.strftime('%I:%M %p')
        except Exception:
            start_date = b.start.date().isoformat() if b.start else ''
            time_range = ''

    resp = {
        'id': b.id,
        'booking_id': b.id,
        'public_ref': getattr(b, 'public_ref', None),
        'service_name': svc.name if svc else None,
        'service_id': svc.id if svc else None,
        'start_date': start_date,
        'time_range': time_range,
        'client_name': b.client_name,
        'client_email': b.client_email,
    }

    # include iso timestamps so client can compute status immediately
    try:
        resp['start'] = b.start.isoformat() if getattr(b, 'start', None) else None
        resp['end'] = b.end.isoformat() if getattr(b, 'end', None) else None
    except Exception:
        pass

    # include duration and a booked-at display string so the client can render correctly
    try:
        created_display = None
        if b.created_at:
            try:
                cd = b.created_at
                if cd.tzinfo is None:
                    cd = cd.replace(tzinfo=ZoneInfo('UTC'))
                created_display = cd.astimezone(org_tz).strftime('%b %d, %Y %I:%M %p')
            except Exception:
                created_display = None
        resp['duration'] = svc.duration if svc and getattr(svc, 'duration', None) is not None else None
        resp['created_display'] = created_display
    except Exception:
        pass

    # Since this undo operation restores the booking from the audit entry,
    # remove the audit record so it no longer appears in the cancelled/deleted list.
    # Send confirmation email to the client to notify them the booking is restored.
    try:
        send_booking_confirmation(b)
    except Exception:
        # don't block the response on email failures
        pass

    try:
        ab.delete()
    except Exception:
        pass

    return JsonResponse({'status': 'ok', 'booking': resp})


@login_required
@require_roles(['owner', 'admin', 'manager', 'staff'])
def bookings_audit_export(request, org_slug):
    """Export selected audit entries. Accepts POST with {'ids': [1,2,3]}

    Attempts to produce a PDF if reportlab is available; otherwise falls back to JSON.
    """
    org = request.organization
    try:
        payload = json.loads(request.body.decode('utf-8'))
        ids = payload.get('ids', [])
    except Exception:
        return HttpResponseBadRequest('Invalid JSON')

    qs = AuditBooking.objects.filter(organization=org, id__in=ids).order_by('-created_at')
    export = []
    # Determine organization timezone for display
    try:
        org_tz_name = getattr(org, 'timezone', None) or 'UTC'
        org_tz = ZoneInfo(org_tz_name)
    except Exception:
        org_tz = ZoneInfo('UTC')

    def _fmt(dt):
        if not dt:
            return None
        try:
            if dt.tzinfo is None:
                # assume UTC for naive datetimes
                dt = dt.replace(tzinfo=ZoneInfo('UTC'))
            dt_local = dt.astimezone(org_tz)
            return dt_local.strftime('%b %d, %Y %I:%M %p')
        except Exception:
            try:
                return dt.isoformat()
            except Exception:
                return str(dt)

    for a in qs:
        # Determine booking reference to show (prefer snapshot.public_ref when available)
        try:
            snap = a.booking_snapshot or {}
            snap_ref = snap.get('public_ref') if isinstance(snap, dict) else None
        except Exception:
            snap = {}
            snap_ref = None
        booking_ref = snap_ref or getattr(a, 'public_ref', None) or a.booking_id

        # Compute display label: treat past deleted events as 'Successful'
        display_event = a.event_type or ''
        try:
            if a.event_type == AuditBooking.EVENT_DELETED and a.start and (a.start < timezone.now()):
                display_event = 'successful'
        except Exception:
            pass

        # Determine whether a client cancellation was refundable per service policy
        non_refunded = False
        try:
            if a.event_type == AuditBooking.EVENT_CANCELLED and a.service and a.start and a.created_at:
                # cancellation occurred at a.created_at; compare to start to decide refund eligibility
                try:
                    # compute hours between cancellation time and appointment start
                    hrs = (a.start - a.created_at).total_seconds() / 3600.0
                    if getattr(a.service, 'refunds_allowed', False):
                        cutoff = float(getattr(a.service, 'refund_cutoff_hours', 0) or 0)
                        refundable = (hrs >= cutoff)
                    else:
                        refundable = False
                except Exception:
                    refundable = False
                non_refunded = not refundable
        except Exception:
            non_refunded = False

        export.append({
            'id': a.id,
            'booking_id': a.booking_id,
            'booking_ref': booking_ref,
            'event_type': a.event_type,
            'display_event': display_event,
            'service': a.service.name if a.service else None,
            'service_price': float(a.service.price) if (a.service and getattr(a.service, 'price', None) is not None) else None,
            'business': org.name,
            'start': a.start.isoformat() if a.start else None,
            'start_display': _fmt(a.start),
            'end': a.end.isoformat() if a.end else None,
            'end_display': _fmt(a.end),
            'client_name': a.client_name,
            'client_email': a.client_email,
            'non_refunded': non_refunded,
            'created_at': a.created_at.isoformat(),
            'snapshot': a.booking_snapshot,
        })

    # Compute earnings summary: counts for successful/cancelled/deleted and
    # total gross and per-service subtotals only for successful appointments
    try:
        total_count = len(export)
        successful_count = 0
        cancelled_count = 0
        deleted_count = 0
        total_gross = 0.0
        potential_gross = 0.0
        per_service = {}
        now_dt = timezone.now()
        for it in export:
            ev = (it.get('display_event') or it.get('event_type') or '').lower()
            # Treat previously computed 'successful' display_event as successful
            is_successful = (ev == 'successful')
            if is_successful:
                successful_count += 1
            else:
                # Cancelled vs deleted based on raw event_type
                raw = (it.get('event_type') or '').lower()
                if raw == AuditBooking.EVENT_CANCELLED:
                    cancelled_count += 1
                elif raw == AuditBooking.EVENT_DELETED:
                    deleted_count += 1
                else:
                    # Fallback: count non-successful items as deleted for totals
                    deleted_count += 1

            # Parse price (safe) and add to potential total always
            price = it.get('service_price')
            try:
                p = float(price) if price is not None else 0.0
            except Exception:
                p = 0.0
            potential_gross += p

            # Determine whether this item contributes to earned totals:
            # earned if successful OR cancelled but non_refunded
            contributes = False
            if is_successful:
                contributes = True
            else:
                try:
                    if (it.get('event_type') or '').lower() == AuditBooking.EVENT_CANCELLED and it.get('non_refunded'):
                        contributes = True
                except Exception:
                    contributes = False

            if contributes:
                total_gross += p
                svc = it.get('service') or 'Unspecified'
                entry = per_service.get(svc) or {'count': 0, 'subtotal': 0.0}
                entry['count'] += 1
                entry['subtotal'] += p
                per_service[svc] = entry

        export_summary = {
            'count': total_count,
            'successful_count': successful_count,
            'cancelled_count': cancelled_count,
            'deleted_count': deleted_count,
            'total_gross': round(total_gross, 2),
            'potential_gross': round(potential_gross, 2),
            'per_service': {k: {'count': v['count'], 'subtotal': round(v['subtotal'], 2)} for k, v in per_service.items()}
        }
    except Exception:
        export_summary = {'count': len(export), 'successful_count': 0, 'cancelled_count': 0, 'deleted_count': 0, 'total_gross': 0.0, 'per_service': {}}

    # Try to generate a simple PDF if reportlab is installed
    try:
        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import letter
        from io import BytesIO

        packet = BytesIO()
        c = canvas.Canvas(packet, pagesize=letter)
        width, height = letter
        y = height - 40
        line_h = 14
        # Centered title
        try:
            c.setFont('Helvetica-Bold', 14)
            c.drawCentredString(width / 2.0, y, f'Audit export for {org.name}')
        except Exception:
            c.setFont('Helvetica', 11)
            c.drawString(40, y, f'Audit export for {org.name}')
        y -= (line_h * 2)
        # Draw counts and earnings summary: only successful appointments contribute to earnings
        try:
            c.setFont('Helvetica-Bold', 12)
            summary_line = (f"Selected: {export_summary.get('count', 0)}  "
                            f"Successful: {export_summary.get('successful_count', 0)}  "
                            f"Cancelled: {export_summary.get('cancelled_count', 0)}  "
                            f"Deleted: {export_summary.get('deleted_count', 0)}")
            c.drawString(40, y, summary_line)
            y -= (line_h * 1.2)
            total_line = f"Total Earned (successful + non-refunded cancellations): ${export_summary.get('total_gross', 0.0):.2f}"
            c.setFont('Helvetica', 11)
            c.drawString(40, y, total_line)
            y -= (line_h * 1.2)
            # Potential total: includes all appointments (successful, cancelled, deleted)
            try:
                potential_line = f"Potential Total (all appointments): ${export_summary.get('potential_gross', 0.0):.2f}"
                c.setFont('Helvetica', 11)
                c.drawString(40, y, potential_line)
                y -= (line_h * 1.2)
            except Exception:
                pass

            # Per-service breakdown only for successful appointments
            if export_summary.get('per_service'):
                c.setFont('Helvetica', 10)
                for svc_name, data in export_summary.get('per_service').items():
                    if y < 60:
                        c.showPage()
                        c.setFont('Helvetica', 11)
                        y = height - 40
                    c.drawString(40, y, f"{svc_name}: {data.get('count',0)} — ${data.get('subtotal',0.0):.2f}")
                    y -= line_h
            y -= (line_h * 0.5)
        except Exception:
            try:
                y -= (line_h * 0.5)
            except Exception:
                pass

        for item in export:
            if y < 60:
                c.showPage()
                c.setFont('Helvetica', 11)
                y = height - 40
            ev = item.get('display_event') or item.get('event_type', '')
            # prefer booking_ref which may be the public_ref from snapshot
            bid = item.get('booking_ref') or item.get('booking_id') or '-'
            c.drawString(40, y, f"Event: {str(ev).capitalize()}  ID: {bid}")
            y -= line_h
            c.drawString(60, y, f"Service: {item.get('service') or '-'}")
            y -= line_h
            # Business (already available)
            c.drawString(60, y, f"Business: {item.get('business') or org.name}")
            y -= line_h
            # Client
            c.drawString(60, y, f"Client: {item.get('client_name') or '-'} <{item.get('client_email') or '-'}>")
            y -= line_h
            # Charge (placed between Client and Start/End)
            price = item.get('service_price')
            if price is not None:
                try:
                    c.drawString(60, y, f"Charge: ${price:.2f}")
                except Exception:
                    c.drawString(60, y, f"Charge: {price}")
                y -= line_h
            # Indicate retained charge for non-refunded cancellations
            try:
                if (item.get('event_type') or '').lower() == AuditBooking.EVENT_CANCELLED and item.get('non_refunded'):
                    c.drawString(60, y, "Note: Cancellation charge retained (no refund)")
                    y -= line_h
            except Exception:
                pass
            # Prefer the human-readable display computed above
            start_disp = item.get('start_display') or item.get('start') or '-'
            end_disp = item.get('end_display') or item.get('end') or None
            if end_disp:
                c.drawString(60, y, f"Start: {start_disp}  —  End: {end_disp}")
                y -= line_h
            else:
                c.drawString(60, y, f"Start: {start_disp}")
                y -= line_h
            y -= (line_h * 1.5)
        c.save()
        packet.seek(0)
        resp = HttpResponse(packet.read(), content_type='application/pdf')
        resp['Content-Disposition'] = 'attachment; filename="audit_export.pdf"'
        return resp
    except Exception as e:
        # If reportlab isn't installed, fall back to JSON export.
        # For other errors during PDF generation, log the exception and
        # return a 500 during DEBUG so it's visible while developing.
        import logging
        logger = logging.getLogger(__name__)
        from django.conf import settings
        # Module import errors indicate reportlab isn't available
        if isinstance(e, (ImportError, ModuleNotFoundError)):
            resp = JsonResponse({'items': export, 'summary': export_summary})
            resp['Content-Disposition'] = 'attachment; filename="audit_export.json"'
            return resp
        # Log the PDF generation failure
        logger.exception('Error generating PDF audit export')
        if getattr(settings, 'DEBUG', False):
            import traceback
            tb = traceback.format_exc()
            return HttpResponse(tb, status=500, content_type='text/plain')
        return HttpResponse('PDF generation failed', status=500)


@login_required
@require_roles(['owner', 'admin', 'manager'])
@require_http_methods(['POST'])
def bookings_audit_delete(request, org_slug):
    """Permanently delete selected audit entries. Accepts POST with {'ids': [1,2,3]}"""
    org = request.organization
    try:
        payload = json.loads(request.body.decode('utf-8'))
        ids = payload.get('ids', [])
    except Exception:
        return HttpResponseBadRequest('Invalid JSON')

    qs = AuditBooking.objects.filter(organization=org, id__in=ids)
    count = qs.count()
    qs.delete()
    return JsonResponse({'status': 'ok', 'deleted': count})