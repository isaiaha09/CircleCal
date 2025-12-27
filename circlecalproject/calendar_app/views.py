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
from bookings.models import Booking, Service, ServiceSettingFreeze, AuditBooking, FacilityResource, ServiceResource
from bookings.views import _has_overlap
from bookings.models import WeeklyAvailability, ServiceWeeklyAvailability, MemberWeeklyAvailability
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
from django.db.models import Count
from django.core.mail import send_mail
from calendar_app.forms import ContactForm


def _unique_resource_slug_for_org(org: Organization, base_slug: str, exclude_id: int = None) -> str:
    base_slug = (base_slug or '').strip() or get_random_string(8)
    slug_candidate = base_slug
    counter = 1
    qs = FacilityResource.objects.filter(organization=org)
    if exclude_id is not None:
        qs = qs.exclude(id=exclude_id)
    while qs.filter(slug=slug_candidate).exists():
        slug_candidate = f"{base_slug}-{counter}"
        counter += 1
    return slug_candidate


@login_required
@require_http_methods(['GET', 'POST'])
@require_roles(['owner'])
def resources_page(request, org_slug):
    """Owner-facing management for facility resources (cages/rooms/etc)."""
    org = request.organization
    try:
        from billing.utils import can_use_resources
        if not can_use_resources(org):
            messages.error(request, 'Resources are available on the Team plan only.')
            return redirect('calendar_app:pricing_page', org_slug=org.slug)
    except Exception:
        # If billing is unavailable, fail closed (do not expose Team-only feature)
        messages.error(request, 'Resources are available on the Team plan only.')
        return redirect('calendar_app:pricing_page', org_slug=org.slug)
    resources = list(FacilityResource.objects.filter(organization=org).order_by('name', 'id'))

    # Annotate usage so the UI can prevent deactivation when in use.
    try:
        res_ids = [r.id for r in resources]
        usage_qs = (
            ServiceResource.objects
            .filter(resource_id__in=res_ids)
            .values('resource_id')
            .annotate(ct=Count('service_id', distinct=True))
        )
        usage = {row['resource_id']: int(row.get('ct') or 0) for row in usage_qs}
    except Exception:
        usage = {}
    for r in resources:
        try:
            r.cc_service_count = int(usage.get(r.id, 0))
        except Exception:
            r.cc_service_count = 0

    # Be defensive: avoid touching new fields if migrations aren't applied yet.
    try:
        resource_field_names = [f.name for f in FacilityResource._meta.get_fields()]
    except Exception:
        resource_field_names = []
    has_max_services = 'max_services' in resource_field_names

    if request.method == 'POST':
        name = (request.POST.get('name') or '').strip()
        slug_input = (request.POST.get('slug') or '').strip()
        is_active = request.POST.get('is_active') is not None
        max_services_raw = request.POST.get('max_services')

        if not name:
            messages.error(request, 'Resource name is required.')
        else:
            base_slug = slugify(slug_input or name) or get_random_string(8)
            slug_val = _unique_resource_slug_for_org(org, base_slug)
            create_kwargs = dict(
                organization=org,
                name=name,
                slug=slug_val,
                is_active=is_active,
            )
            if has_max_services:
                try:
                    ms = int(max_services_raw) if (max_services_raw is not None and str(max_services_raw).strip() != '') else 1
                except Exception:
                    ms = 1
                if ms < 0:
                    ms = 1
                create_kwargs['max_services'] = ms
            FacilityResource.objects.create(**create_kwargs)
            messages.success(request, 'Resource created.')
            return redirect('calendar_app:resources_page', org_slug=org.slug)

    return render(request, 'calendar_app/resources.html', {
        'org': org,
        'resources': resources,
    })


@login_required
@require_http_methods(['GET', 'POST'])
@require_roles(['owner'])
def edit_resource(request, org_slug, resource_id):
    org = request.organization
    try:
        from billing.utils import can_use_resources
        if not can_use_resources(org):
            messages.error(request, 'Resources are available on the Team plan only.')
            return redirect('calendar_app:pricing_page', org_slug=org.slug)
    except Exception:
        messages.error(request, 'Resources are available on the Team plan only.')
        return redirect('calendar_app:pricing_page', org_slug=org.slug)
    resource = get_object_or_404(FacilityResource, id=resource_id, organization=org)

    # Be defensive: avoid touching new fields if migrations aren't applied yet.
    try:
        resource_field_names = [f.name for f in FacilityResource._meta.get_fields()]
    except Exception:
        resource_field_names = []
    has_max_services = 'max_services' in resource_field_names

    if request.method == 'POST':
        name = (request.POST.get('name') or '').strip()
        slug_input = (request.POST.get('slug') or '').strip()
        is_active = request.POST.get('is_active') is not None
        max_services_raw = request.POST.get('max_services')

        if not name:
            messages.error(request, 'Resource name is required.')
        else:
            # Block deactivation if this resource is linked to any services.
            if (not is_active) and bool(getattr(resource, 'is_active', True)):
                try:
                    in_use = ServiceResource.objects.filter(resource=resource).exists()
                except Exception:
                    in_use = False
                if in_use:
                    messages.error(request, 'This resource is currently linked to a service. Unlink it from all services before making it inactive.')
                    return redirect('calendar_app:edit_resource', org_slug=org.slug, resource_id=resource.id)

            base_slug = slugify(slug_input or name) or get_random_string(8)
            resource.name = name
            resource.slug = _unique_resource_slug_for_org(org, base_slug, exclude_id=resource.id)
            resource.is_active = is_active
            if has_max_services:
                try:
                    ms = int(max_services_raw) if (max_services_raw is not None and str(max_services_raw).strip() != '') else getattr(resource, 'max_services', 1)
                except Exception:
                    ms = getattr(resource, 'max_services', 1) or 1
                if ms < 0:
                    ms = getattr(resource, 'max_services', 1) or 1
                try:
                    resource.max_services = ms
                except Exception:
                    pass
            resource.save()
            messages.success(request, 'Resource updated.')
            return redirect('calendar_app:resources_page', org_slug=org.slug)

    return render(request, 'calendar_app/edit_resource.html', {
        'org': org,
        'resource': resource,
    })


@login_required
@require_http_methods(['POST'])
@require_roles(['owner'])
def toggle_resource_active(request, org_slug, resource_id):
    org = request.organization
    try:
        from billing.utils import can_use_resources
        if not can_use_resources(org):
            messages.error(request, 'Resources are available on the Team plan only.')
            return redirect('calendar_app:pricing_page', org_slug=org.slug)
    except Exception:
        messages.error(request, 'Resources are available on the Team plan only.')
        return redirect('calendar_app:pricing_page', org_slug=org.slug)
    resource = get_object_or_404(FacilityResource, id=resource_id, organization=org)
    current = bool(getattr(resource, 'is_active', True))
    next_val = (not current)
    if current and (not next_val):
        # Block deactivation if this resource is linked to any services.
        try:
            in_use = ServiceResource.objects.filter(resource=resource).exists()
        except Exception:
            in_use = False
        if in_use:
            messages.error(request, 'This resource is currently linked to a service. Unlink it from all services before making it inactive.')
            return redirect('calendar_app:resources_page', org_slug=org.slug)

    resource.is_active = next_val
    resource.save(update_fields=['is_active'])
    return redirect('calendar_app:resources_page', org_slug=org.slug)


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
            # Include any existing per-date frozen settings so the modal can show
            # which settings will be preserved for this date (if a freeze exists)
            'existing_freeze': existing_freeze.frozen_settings if (existing_freeze and getattr(existing_freeze, 'frozen_settings', None)) else None,
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

    # Validate facility resource selection (Team plan + owner only) before doing
    # any side effects (like freeze creation) or persisting service changes.
    desired_resource_ids = None
    try:
        if user_has_role(request.user, org, 'owner'):
            from billing.utils import can_use_resources
            if can_use_resources(org):
                posted = payload.get('resource_ids', [])
                if posted is None:
                    posted = []
                if not isinstance(posted, list):
                    posted = [posted]

                desired = set()
                for v in posted:
                    try:
                        rid = int(v)
                    except Exception:
                        continue
                    if FacilityResource.objects.filter(id=rid, organization=org).exists():
                        desired.add(rid)

                # Capacity validation: prevent selecting resources that are already
                # linked to too many other services.
                invalid = []
                existing_ids = set(ServiceResource.objects.filter(service=svc).values_list('resource_id', flat=True))
                # Fetch resources in bulk; be defensive if migrations missing.
                resources = list(FacilityResource.objects.filter(organization=org, id__in=list(desired)))
                res_by_id = {r.id: r for r in resources}

                for rid in desired:
                    r = res_by_id.get(rid)
                    if not r:
                        continue
                    # Default to exclusive if the field doesn't exist yet.
                    try:
                        max_services = int(getattr(r, 'max_services', 1) or 0)
                    except Exception:
                        max_services = 1
                    if max_services == 0:
                        continue

                    # Count distinct other services (excluding this svc) using this resource.
                    try:
                        other_service_count = ServiceResource.objects.filter(resource_id=rid).exclude(service=svc).values('service_id').distinct().count()
                    except Exception:
                        other_service_count = 0

                    if other_service_count >= max_services and (rid not in existing_ids):
                        invalid.append(r.name)

                if invalid:
                    msg = 'These resources are already in use by other services: ' + ', '.join(invalid) + '.'
                    return JsonResponse({'status': 'error', 'error': msg}, status=400)

                desired_resource_ids = desired
    except Exception:
        # If billing/permissions/field lookups fail, fail closed: do not block saving
        # the service, but also do not change resource wiring.
        desired_resource_ids = None

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
                    any_org_rows = WeeklyAvailability.objects.filter(organization=org, is_active=True).exists()
                    if not any_org_rows:
                        weekly_windows = [{'start': '00:00', 'end': '23:59'}]
                    else:
                        org_rows = WeeklyAvailability.objects.filter(organization=org, is_active=True, weekday=wd)
                        for rw in org_rows:
                            weekly_windows.append({'start': rw.start_time.strftime('%H:%M'), 'end': rw.end_time.strftime('%H:%M')})
            except Exception:
                weekly_windows = []

            # Prefer any existing per-date freeze values when constructing the
            # frozen snapshot. This ensures fields like `buffer_after` remain
            # preserved for dates that already have a freeze.
            try:
                existing = ServiceSettingFreeze.objects.filter(service=svc, date=d).first()
            except Exception:
                existing = None

            if existing and getattr(existing, 'frozen_settings', None):
                # Use existing frozen settings as-is (do not overwrite)
                frozen = existing.frozen_settings
            else:
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
                obj, created = ServiceSettingFreeze.objects.get_or_create(
                    service=svc, date=d, defaults={'frozen_settings': frozen}
                )
                # Do not overwrite an existing freeze; only count newly created ones.
                if created:
                    freezes_created += 1
                    frozen_dates.append(d.isoformat())
                else:
                    # preserve existing freeze; report its date but do not modify
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

    # Apply provided fields to service.
    # Note: The Edit Service page uses this endpoint for saving, so it must
    # persist the same fields the normal form POST supports.
    fields = {}

    # Text fields
    if 'name' in payload:
        try:
            fields['name'] = (payload.get('name') or '').strip()
        except Exception:
            pass
    if 'description' in payload:
        try:
            fields['description'] = (payload.get('description') or '').strip()
        except Exception:
            pass

    # Numeric fields
    if 'price' in payload:
        try:
            fields['price'] = float(payload.get('price') or 0)
        except Exception:
            pass
    if 'duration' in payload:
        try:
            fields['duration'] = int(payload.get('duration') or 0)
        except Exception:
            pass
    if 'buffer_after' in payload:
        try:
            fields['buffer_after'] = int(payload.get('buffer_after') or 0)
        except Exception:
            pass
    if 'min_notice_hours' in payload:
        try:
            fields['min_notice_hours'] = int(payload.get('min_notice_hours') or 0)
        except Exception:
            pass
    if 'max_booking_days' in payload:
        try:
            fields['max_booking_days'] = int(payload.get('max_booking_days') or 0)
        except Exception:
            pass
    if 'time_increment_minutes' in payload:
        try:
            fields['time_increment_minutes'] = int(payload.get('time_increment_minutes') or 0) or 30
        except Exception:
            fields['time_increment_minutes'] = 30
    if 'refund_cutoff_hours' in payload:
        try:
            fields['refund_cutoff_hours'] = int(payload.get('refund_cutoff_hours') or 0)
        except Exception:
            pass

    # Boolean fields
    if 'use_fixed_increment' in payload:
        try:
            fields['use_fixed_increment'] = bool(payload.get('use_fixed_increment'))
        except Exception:
            pass
    if 'allow_squished_bookings' in payload:
        try:
            fields['allow_squished_bookings'] = bool(payload.get('allow_squished_bookings'))
        except Exception:
            pass
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
    if 'refunds_allowed' in payload:
        try:
            fields['refunds_allowed'] = bool(payload.get('refunds_allowed'))
        except Exception:
            pass

    # Refund policy text
    if 'refund_policy_text' in payload:
        try:
            fields['refund_policy_text'] = (payload.get('refund_policy_text') or '').strip()
        except Exception:
            pass

    # Enforce refund cutoff behavior similar to the form POST.
    try:
        refunds_allowed = fields.get('refunds_allowed', getattr(svc, 'refunds_allowed', False))
        cutoff_val = fields.get('refund_cutoff_hours', getattr(svc, 'refund_cutoff_hours', 0) or 0)
        if refunds_allowed:
            try:
                cutoff_val = int(cutoff_val)
            except Exception:
                cutoff_val = int(getattr(svc, 'refund_cutoff_hours', 24) or 24)
            if cutoff_val < 1:
                cutoff_val = 1
            fields['refund_cutoff_hours'] = cutoff_val
        else:
            fields['refund_cutoff_hours'] = 0
    except Exception:
        pass

    # Prevent deactivating the last active service.
    try:
        if 'is_active' in fields and not fields['is_active']:
            other_active = Service.objects.filter(organization=org, is_active=True).exclude(id=svc.id).count()
            if other_active == 0:
                return JsonResponse({'status': 'error', 'error': 'At least one service must remain active.'}, status=400)
    except Exception:
        pass

    # Slug update only when there are no bookings for this service.
    try:
        if 'slug' in payload:
            try:
                has_bookings = Booking.objects.filter(service=svc).exists()
            except Exception:
                has_bookings = False
            if not has_bookings:
                new_slug_input = (payload.get('slug') or '').strip()
                if new_slug_input:
                    base_slug = slugify(new_slug_input) or slugify(fields.get('name') or svc.name) or get_random_string(6)
                    slug_candidate = base_slug
                    counter = 1
                    while Service.objects.filter(organization=org, slug=slug_candidate).exclude(id=svc.id).exists():
                        slug_candidate = f"{base_slug}-{counter}"
                        counter += 1
                    fields['slug'] = slug_candidate
    except Exception:
        pass

    for k, v in fields.items():
        try:
            setattr(svc, k, v)
        except Exception:
            # Field may not exist if migrations not applied
            continue
    svc.save()

    # Sync facility resources allowed for this service (Team plan + owner only).
    # Use `desired_resource_ids` computed above (already validated for capacity).
    try:
        if desired_resource_ids is not None:
            existing_ids = set(ServiceResource.objects.filter(service=svc).values_list('resource_id', flat=True))
            to_add = set(desired_resource_ids) - existing_ids
            to_remove = existing_ids - set(desired_resource_ids)

            for rid in to_add:
                try:
                    ServiceResource.objects.create(service=svc, resource_id=rid)
                except Exception:
                    continue
            if to_remove:
                ServiceResource.objects.filter(service=svc, resource_id__in=list(to_remove)).delete()
    except Exception:
        pass

    # Sync service assignments (assigned_members).
    try:
        from bookings.models import ServiceAssignment
        posted = payload.get('assigned_members', [])
        if posted is None:
            posted = []
        if not isinstance(posted, list):
            posted = [posted]
        desired = set()
        for v in posted:
            try:
                iv = int(v)
                if Membership.objects.filter(id=iv, organization=org, is_active=True).exists():
                    desired.add(iv)
            except Exception:
                continue

        existing_ids = set(ServiceAssignment.objects.filter(service=svc).values_list('membership_id', flat=True))
        to_add = desired - existing_ids
        to_remove = existing_ids - desired

        for mid in to_add:
            try:
                mem = Membership.objects.get(id=mid, organization=org)
                ServiceAssignment.objects.create(service=svc, membership=mem)
            except Exception:
                continue
        if to_remove:
            ServiceAssignment.objects.filter(service=svc, membership_id__in=list(to_remove)).delete()
    except Exception:
        pass

    # Handle per-service weekly availability fields (svc_avail_0..svc_avail_6).
    try:
        can_edit_svc_avail, _reason = _service_availability_applicability(org, svc)
        if can_edit_svc_avail:
            svc_windows = []
            for ui_day in range(7):
                key = f"svc_avail_{ui_day}"
                raw = payload.get(key, '') or ''
                raw = str(raw).strip()
                if not raw:
                    continue
                model_wd = ((ui_day - 1) % 7)  # UI 0=Sun..6=Sat -> model 0=Mon..6=Sun
                parts = [p.strip() for p in raw.split(',') if p.strip()]
                for part in parts:
                    try:
                        start_s, end_s = [x.strip() for x in part.split('-')]
                    except Exception:
                        continue
                    if len(start_s) != 5 or len(end_s) != 5 or start_s[2] != ':' or end_s[2] != ':':
                        continue
                    svc_windows.append((model_wd, start_s, end_s))

            if svc_windows:
                new_objs = []
                for (wd, start_s, end_s) in svc_windows:
                    try:
                        st = datetime.strptime(start_s, '%H:%M').time()
                        et = datetime.strptime(end_s, '%H:%M').time()
                    except Exception:
                        continue
                    obj = ServiceWeeklyAvailability(service=svc, weekday=wd, start_time=st, end_time=et, is_active=True)
                    try:
                        obj.full_clean()
                    except Exception:
                        continue
                    new_objs.append(obj)
                if new_objs:
                    # Enforce subset + partition overlap guardrails before persisting.
                    try:
                        mid = _get_single_assignee_membership_id(org, svc)
                        cleaned_rows = [(o.weekday, o.start_time, o.end_time) for o in new_objs]
                        if mid is not None:
                            _enforce_service_windows_within_member_availability(org, mid, cleaned_rows)
                            _enforce_no_overlap_between_mixed_signature_solo_services(org, mid, svc, cleaned_rows)
                    except ValueError as ve:
                        return JsonResponse({'status': 'error', 'error': str(ve)})
                    ServiceWeeklyAvailability.objects.filter(service=svc).delete()
                    ServiceWeeklyAvailability.objects.bulk_create(new_objs)
            else:
                # If nothing posted, remove per-service windows.
                ServiceWeeklyAvailability.objects.filter(service=svc).delete()
    except Exception:
        pass

    # Enforce activation guardrail for JSON save path:
    # if the service has no weekly availability (overrides do not count), it must be inactive.
    forced_inactive = False
    try:
        if getattr(svc, 'is_active', False) and (not _service_has_effective_weekly_availability_for_activation(org, svc)):
            svc.is_active = False
            svc.save(update_fields=['is_active'])
            forced_inactive = True
            try:
                messages.error(
                    request,
                    "This service was set to inactive because it has no weekly availability. "
                    "Add weekly availability (overrides don’t count) to activate it."
                )
            except Exception:
                pass
    except Exception:
        forced_inactive = False

    resp = {'status': 'ok', 'freezes_created': freezes_created, 'booked_dates_count': len(booked_dates), 'booked_dates': frozen_dates}
    if forced_inactive:
        resp['forced_inactive'] = True
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


def _service_schedule_signature(service):
    """Fields that affect slot generation/scheduling compatibility."""
    try:
        return (
            int(getattr(service, 'duration', 0) or 0),
            int(getattr(service, 'buffer_after', 0) or 0),
            int(getattr(service, 'time_increment_minutes', 30) or 30),
            bool(getattr(service, 'use_fixed_increment', False)),
            bool(getattr(service, 'allow_squished_bookings', False)),
            bool(getattr(service, 'allow_ends_after_availability', False)),
            int(getattr(service, 'min_notice_hours', 0) or 0),
            int(getattr(service, 'max_booking_days', 0) or 0),
        )
    except Exception:
        # Defensive fallback
        return (0, 0, 30, False, False, False, 0, 0)


def _effective_member_weekly_map(org, membership_id):
    """Return the member's effective weekly map (member-specific if present, else org defaults)."""
    try:
        mid = int(membership_id)
    except Exception:
        return _build_org_weekly_map(org)

    try:
        has_member_rows = MemberWeeklyAvailability.objects.filter(membership_id=mid, is_active=True).exists()
    except Exception:
        has_member_rows = False

    if has_member_rows:
        return _build_member_weekly_map(mid)
    return _build_org_weekly_map(org)


def _solo_services_signature_mode(org, membership_id):
    """Return 'all_same' or 'mixed' for the member's solo services.

    'solo service' = a service assigned to exactly this one member.

    If there are 0-1 solo services, treat as all_same.
    """
    try:
        from bookings.models import ServiceAssignment
    except Exception:
        return 'all_same'

    try:
        mid = int(membership_id)
    except Exception:
        return 'all_same'

    try:
        service_ids = list(
            ServiceAssignment.objects.filter(membership_id=mid, service__organization=org, service__is_active=True)
            .values_list('service_id', flat=True)
        )
    except Exception:
        service_ids = []

    if not service_ids:
        return 'all_same'

    try:
        counts = (
            ServiceAssignment.objects.filter(service_id__in=service_ids)
            .values('service_id')
            .annotate(c=Count('id'))
        )
        solo_ids = [row['service_id'] for row in counts if int(row.get('c') or 0) == 1]
    except Exception:
        solo_ids = []

    if len(solo_ids) <= 1:
        return 'all_same'

    try:
        sigs = {
            _service_schedule_signature(s)
            for s in Service.objects.filter(organization=org, is_active=True, id__in=solo_ids)
        }
    except Exception:
        sigs = set()

    return 'mixed' if len(sigs) > 1 else 'all_same'


def _solo_services_count(org, membership_id):
    """Return count of solo services (services assigned to exactly this member)."""
    try:
        from bookings.models import ServiceAssignment
    except Exception:
        return 0

    try:
        mid = int(membership_id)
    except Exception:
        return 0

    try:
        service_ids = list(
            ServiceAssignment.objects.filter(membership_id=mid, service__organization=org, service__is_active=True)
            .values_list('service_id', flat=True)
        )
    except Exception:
        service_ids = []

    if not service_ids:
        return 0

    try:
        counts = (
            ServiceAssignment.objects.filter(service_id__in=service_ids)
            .values('service_id')
            .annotate(c=Count('id'))
        )
        solo_ids = [row['service_id'] for row in counts if int(row.get('c') or 0) == 1]
        return len(solo_ids)
    except Exception:
        return 0


def _build_service_weekly_map(service):
    """Return a UI weekly map for a service.

    Behavior:
    - If the service has explicit ServiceWeeklyAvailability rows, return them.
    - If the service is assigned to exactly one member:
        - When that member's solo services all share the same scheduling settings,
          treat the service as inheriting the member's availability.
        - When the member has multiple solo services with mixed scheduling settings,
          do NOT assume inheritance (availability must be explicitly partitioned).
    """
    # Trial onboarding rule: when the org has only one active service, the service
    # schedule should follow Calendar (org weekly availability) rather than
    # per-service weekly rows.
    try:
        from billing.utils import get_subscription
        subscription = get_subscription(service.organization)
        if subscription and getattr(subscription, 'status', '') == 'trialing':
            try:
                active_ct = Service.objects.filter(organization=service.organization, is_active=True).count()
            except Exception:
                active_ct = 0
            if active_ct <= 1:
                return _build_org_weekly_map(service.organization)
    except Exception:
        pass

    rows = service.weekly_availability.filter(is_active=True).order_by('weekday', 'start_time')
    svc_map = [[] for _ in range(7)]
    has_rows = False
    for row in rows:
        has_rows = True
        ui_idx = (row.weekday + 1) % 7
        svc_map[ui_idx].append(f"{row.start_time.strftime('%H:%M')}-{row.end_time.strftime('%H:%M')}")
    if has_rows:
        return svc_map

    # No explicit service windows; for single-assignee services we may inherit.
    try:
        from bookings.models import ServiceAssignment
    except Exception:
        return svc_map

    try:
        assigned_ids = list(
            ServiceAssignment.objects.filter(service=service)
            .values_list('membership_id', flat=True)
            .distinct()
        )
    except Exception:
        assigned_ids = []

    if len(assigned_ids) != 1:
        return svc_map

    mid = assigned_ids[0]
    # Rule: if the member has ONLY ONE solo service, that service inherits the member's availability.
    # If the member has multiple solo services, they must be explicitly partitioned per service.
    try:
        solo_count = _solo_services_count(service.organization, mid)
    except Exception:
        solo_count = 0

    if solo_count <= 1:
        return _effective_member_weekly_map(service.organization, mid)

    # Partition mode: service must have explicit service-weekly rows.
    return svc_map


def _service_availability_applicability(org, service):
    """Return (enabled, reason).

        Enabled when:
        - The service has 0 assigned team members (unassigned service schedule), OR
        - The service has 2+ assigned team members (shared service schedule), OR
        - The service has exactly one assigned team member AND that member has multiple
            solo services (so per-service partitioning is required).
    """
    # Trial onboarding rule: when only one active service exists, per-service
    # availability is disabled and availability follows the Calendar (org weekly).
    try:
        from billing.utils import get_subscription
        subscription = get_subscription(org)
        if subscription and getattr(subscription, 'status', '') == 'trialing':
            try:
                active_ct = Service.objects.filter(organization=org, is_active=True).count()
            except Exception:
                active_ct = 0
            if active_ct <= 1:
                return False, "With only one active service, availability follows your Calendar availability. Create a second active service to enable per-service availability."
    except Exception:
        pass

    try:
        from bookings.models import ServiceAssignment
    except Exception:
        return False, "Service availability is not available (assignments missing)."

    try:
        assigned_ids = list(ServiceAssignment.objects.filter(service=service).values_list('membership_id', flat=True))
    except Exception:
        assigned_ids = []

    # Unassigned services and shared services always have their own schedule.
    if len(assigned_ids) == 0:
        return True, ""
    if len(assigned_ids) >= 2:
        return True, ""

    # Single assignee: only enable when partitioning is needed.
    if len(assigned_ids) != 1:
        return False, "Service availability is not applicable for this service."

    mid = assigned_ids[0]

    try:
        service_ids = list(
            ServiceAssignment.objects.filter(membership_id=mid, service__organization=org)
            .values_list('service_id', flat=True)
        )
    except Exception:
        service_ids = []

    if not service_ids:
        return False, "Service availability applies only when the assigned member has multiple solo services."

    try:
        counts = (
            ServiceAssignment.objects.filter(service_id__in=service_ids)
            .values('service_id')
            .annotate(c=Count('id'))
        )
        solo_ids = {row['service_id'] for row in counts if int(row.get('c') or 0) == 1}
    except Exception:
        solo_ids = set()

    # Must have at least two solo services (including the current one).
    if len(solo_ids) < 2:
        return False, "Service availability applies only when this team member has multiple solo services."

    if service.id not in solo_ids:
        # Defensive: should not happen if the current service has exactly one assignee.
        return False, "Service availability is not applicable for this service."

    return True, ""


def _intervals_overlap(a_start, a_end, b_start, b_end):
    return (a_start < b_end) and (b_start < a_end)


def _hm_to_minutes(hm):
    try:
        s = str(hm).strip()
        hh = int(s[:2])
        mm = int(s[3:5])
        return hh * 60 + mm
    except Exception:
        return None


def _time_to_minutes(t):
    """Convert a time-like value to minutes since midnight.

    Accepts datetime.time, 'HH:MM' or 'HH:MM:SS' strings.
    """
    try:
        if hasattr(t, 'hour') and hasattr(t, 'minute'):
            return (int(t.hour) * 60) + int(t.minute)
    except Exception:
        pass
    return _hm_to_minutes(t)


def _get_single_assignee_membership_id(org, service):
    """Return membership_id if the service has exactly one assignee, else None."""
    try:
        from bookings.models import ServiceAssignment
    except Exception:
        return None

    try:
        ids = list(
            ServiceAssignment.objects.filter(service=service)
            .values_list('membership_id', flat=True)
            .distinct()
        )
    except Exception:
        ids = []

    if len(ids) != 1:
        return None
    try:
        return int(ids[0])
    except Exception:
        return None


def _service_requires_explicit_weekly(org, service):
    """Return True when a service should be treated as explicitly scoped to service-weekly rows.

    Explicitly scoped services must have ServiceWeeklyAvailability rows to be bookable.
    This matches the public booking semantics:
    - Unassigned services (0 assignees)
    - Shared services (2+ assignees)
    - Single-assignee services when that assignee has multiple solo services (partitioning)
    """
    if not service:
        return False

    # Trial onboarding rule: when the org has only one active service, availability
    # follows Calendar (org weekly availability) and we do NOT require explicit
    # per-service weekly windows.
    try:
        from billing.utils import get_subscription
        subscription = get_subscription(org)
        if subscription and getattr(subscription, 'status', '') == 'trialing':
            try:
                active_ct = Service.objects.filter(organization=org, is_active=True).count()
            except Exception:
                active_ct = 0
            if active_ct <= 1:
                return False
    except Exception:
        pass
    try:
        from bookings.models import ServiceAssignment
    except Exception:
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


def _service_has_effective_weekly_availability_for_activation(org, service):
    """Return True if a service has any weekly availability that makes it bookable.

    Important: per-date overrides do NOT count.
    """
    if not service:
        return False

    # Trial/Basic onboarding: do not block activation due to weekly-availability setup.
    # Requirement: new trial users should have their first service active by default.
    try:
        from billing.utils import get_plan_slug, BASIC_SLUG, get_subscription
        plan_slug = get_plan_slug(org)
        sub = get_subscription(org)
        is_trialing = bool(sub and getattr(sub, 'status', '') == 'trialing')
        if plan_slug == BASIC_SLUG or is_trialing:
            return True
    except Exception:
        pass

    # If the service is explicitly scoped, it must have service-weekly rows.
    try:
        svc_has_any = service.weekly_availability.filter(is_active=True).exists()
    except Exception:
        svc_has_any = False

    try:
        requires_explicit = _service_requires_explicit_weekly(org, service)
    except Exception:
        requires_explicit = False

    if requires_explicit or svc_has_any:
        return bool(svc_has_any)

    # Otherwise, it can inherit member weekly availability (single assignee) or org weekly.
    mid = _get_single_assignee_membership_id(org, service)
    if mid is not None:
        try:
            from bookings.models import MemberWeeklyAvailability
            if MemberWeeklyAvailability.objects.filter(membership_id=mid, is_active=True).exists():
                return True
        except Exception:
            pass

    # Fall back to org weekly. If org has no weekly rows at all, preserve legacy "open" behavior.
    try:
        from bookings.models import WeeklyAvailability
        if not WeeklyAvailability.objects.filter(organization=org, is_active=True).exists():
            return True
        return WeeklyAvailability.objects.filter(organization=org, is_active=True).exists()
    except Exception:
        return True


def _enforce_service_windows_within_member_availability(org, membership_id, proposed_cleaned_rows):
    """Raise ValueError if any proposed service window falls outside member availability."""
    try:
        mid = int(membership_id)
    except Exception:
        return

    # Build model-weekday -> [(start_min, end_min), ...] from effective member map.
    eff_ui_map = _effective_member_weekly_map(org, mid)
    allowed = {i: [] for i in range(7)}
    for ui_day in range(7):
        model_wd = ((ui_day - 1) % 7)  # UI 0=Sun..6=Sat -> model 0=Mon..6=Sun
        for r in (eff_ui_map[ui_day] or []):
            try:
                start_s, end_s = [x.strip() for x in str(r).split('-', 1)]
            except Exception:
                continue
            sm = _hm_to_minutes(start_s)
            em = _hm_to_minutes(end_s)
            if sm is None or em is None:
                continue
            allowed[model_wd].append((sm, em))

    model_labels = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    for (wd, start, end) in (proposed_cleaned_rows or []):
        try:
            wdi = int(wd)
        except Exception:
            continue
        sm = _time_to_minutes(start)
        em = _time_to_minutes(end)
        if sm is None or em is None:
            continue

        ok = False
        for (am, bm) in allowed.get(wdi, []):
            if am <= sm and bm >= em:
                ok = True
                break

        if not ok:
            try:
                st_s = start.strftime('%H:%M') if hasattr(start, 'strftime') else str(start)
                et_s = end.strftime('%H:%M') if hasattr(end, 'strftime') else str(end)
            except Exception:
                st_s, et_s = str(start), str(end)
            day = model_labels[wdi] if 0 <= wdi <= 6 else str(wd)
            raise ValueError(
                f"Service availability must be within the assigned member's weekly availability. "
                f"Invalid window: {day} {st_s}-{et_s}."
            )


def _enforce_no_overlap_between_mixed_signature_solo_services(org, membership_id, service, proposed_cleaned_rows):
    """Raise ValueError if proposed windows overlap other solo services with different signatures.

    This is a server-side guardrail for the business rule:
    - If a member has multiple solo services and their scheduling settings differ,
      those services must be offered on separate days/times.
    """
    try:
        from bookings.models import ServiceAssignment
    except Exception:
        return

    try:
        mid = int(membership_id)
    except Exception:
        return

    my_sig = _service_schedule_signature(service)

    def _member_allowed_by_wd(mid_local):
        """Return model-weekday -> list of (start_min, end_min) for member effective availability."""
        eff_ui_map = _effective_member_weekly_map(org, mid_local)
        allowed_local = {i: [] for i in range(7)}
        for ui_day in range(7):
            model_wd = ((ui_day - 1) % 7)
            for r in (eff_ui_map[ui_day] or []):
                try:
                    start_s, end_s = [x.strip() for x in str(r).split('-', 1)]
                except Exception:
                    continue
                sm = _hm_to_minutes(start_s)
                em = _hm_to_minutes(end_s)
                if sm is None or em is None:
                    continue
                allowed_local[model_wd].append((sm, em))
        return allowed_local

    member_allowed = _member_allowed_by_wd(mid)

    def _effective_service_intervals(other_service):
        """Return model-weekday -> list of (start_min, end_min) for another service.

        If the service has explicit service-weekly windows, use them.
        Otherwise, treat it as inheriting the member's overall availability.

        This is critical to prevent "leaking" availability into other services
        that haven't been explicitly partitioned yet.
        """
        out = {i: [] for i in range(7)}
        try:
            other_rows = list(
                other_service.weekly_availability.filter(is_active=True)
                .values_list('weekday', 'start_time', 'end_time')
            )
        except Exception:
            other_rows = []

        if other_rows:
            for (wd, st, et) in other_rows:
                osm = _time_to_minutes(st)
                oem = _time_to_minutes(et)
                if osm is None or oem is None:
                    continue
                out[int(wd)].append((osm, oem))
            return out

        # No explicit rows: inherit member availability.
        for wd, intervals in member_allowed.items():
            out[int(wd)] = list(intervals or [])
        return out

    # Identify solo services for the member.
    try:
        service_ids = list(
            ServiceAssignment.objects.filter(membership_id=mid, service__organization=org, service__is_active=True)
            .values_list('service_id', flat=True)
        )
    except Exception:
        service_ids = []

    if not service_ids:
        return

    try:
        counts = (
            ServiceAssignment.objects.filter(service_id__in=service_ids)
            .values('service_id')
            .annotate(c=Count('id'))
        )
        solo_ids = [row['service_id'] for row in counts if int(row.get('c') or 0) == 1]
    except Exception:
        solo_ids = []

    if len(solo_ids) <= 1:
        return

    proposed_by_wd = {}
    for (wd, start, end) in proposed_cleaned_rows:
        sm = _hm_to_minutes(start)
        em = _hm_to_minutes(end)
        if sm is None or em is None:
            continue
        proposed_by_wd.setdefault(int(wd), []).append((sm, em))

    # Compare against other solo services with different signature.
    others = Service.objects.filter(organization=org, is_active=True, id__in=solo_ids).exclude(id=service.id)
    for other in others:
        if _service_schedule_signature(other) == my_sig:
            continue

        other_by_wd = _effective_service_intervals(other)
        for wd, other_intervals in other_by_wd.items():
            for (osm, oem) in (other_intervals or []):
                for (psm, pem) in proposed_by_wd.get(int(wd), []):
                    if _intervals_overlap(psm, pem, osm, oem):
                        raise ValueError(
                            f"Availability overlaps another solo service ('{other.name}') with different scheduling settings. "
                            "Services with different settings must be offered on separate days/times."
                        )


def _enforce_service_windows_within_allowed_rows(allowed_cleaned_rows, service, err_prefix=None):
    """Raise ValueError if this service has explicit service-weekly rows outside allowed rows.

    `allowed_cleaned_rows` are model-weekday tuples: (weekday, start, end) with start/end as
    'HH:MM' strings or time objects.

    This is used to ensure a member/org overall availability isn't shrunk below existing
    service availability.
    """
    allowed = {i: [] for i in range(7)}
    for (wd, start, end) in (allowed_cleaned_rows or []):
        try:
            wdi = int(wd)
        except Exception:
            continue
        sm = _time_to_minutes(start)
        em = _time_to_minutes(end)
        if sm is None or em is None:
            continue
        allowed[wdi].append((sm, em))

    rows = list(service.weekly_availability.filter(is_active=True).values_list('weekday', 'start_time', 'end_time'))
    if not rows:
        return

    model_labels = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    for (wd, st, et) in rows:
        sm = _time_to_minutes(st)
        em = _time_to_minutes(et)
        if sm is None or em is None:
            continue
        ok = False
        for (am, bm) in allowed.get(int(wd), []):
            if am <= sm and bm >= em:
                ok = True
                break
        if not ok:
            try:
                st_s = st.strftime('%H:%M') if hasattr(st, 'strftime') else str(st)
                et_s = et.strftime('%H:%M') if hasattr(et, 'strftime') else str(et)
            except Exception:
                st_s, et_s = str(st), str(et)
            day = model_labels[int(wd)] if 0 <= int(wd) <= 6 else str(wd)
            prefix = (str(err_prefix).strip() + ' ') if err_prefix else ''
            raise ValueError(
                f"{prefix}Overall availability cannot exclude existing service availability. "
                f"Service '{getattr(service, 'name', 'Service')}' has {day} {st_s}-{et_s} outside the overall availability."
            )


def _iter_member_solo_services(org, membership_id):
    """Yield active solo services for a membership (services assigned to exactly this one member)."""
    try:
        from bookings.models import ServiceAssignment
    except Exception:
        return []

    try:
        mid = int(membership_id)
    except Exception:
        return []

    try:
        service_ids = list(
            ServiceAssignment.objects.filter(membership_id=mid, service__organization=org, service__is_active=True)
            .values_list('service_id', flat=True)
        )
    except Exception:
        service_ids = []
    if not service_ids:
        return []

    try:
        counts = (
            ServiceAssignment.objects.filter(service_id__in=service_ids)
            .values('service_id')
            .annotate(c=Count('id'))
        )
        solo_ids = [row['service_id'] for row in counts if int(row.get('c') or 0) == 1]
    except Exception:
        solo_ids = []
    if not solo_ids:
        return []
    return Service.objects.filter(organization=org, is_active=True, id__in=solo_ids)


def _get_single_assignee_display_name(org, service):
    """Return display name/email for a service's single assignee, else ''."""
    try:
        from bookings.models import ServiceAssignment
    except Exception:
        return ""

    try:
        ids = list(
            ServiceAssignment.objects.filter(service=service)
            .values_list('membership_id', flat=True)
            .distinct()
        )
    except Exception:
        ids = []

    if len(ids) != 1:
        return ""

    try:
        mid = int(ids[0])
    except Exception:
        return ""

    # Prefer following the FK chain (membership -> user) when possible.
    try:
        mem = Membership.objects.filter(id=mid, organization=org).select_related('user').first()
        if mem and mem.user:
            full = (f"{(mem.user.first_name or '').strip()} {(mem.user.last_name or '').strip()}").strip()
            return full or (mem.user.email or f"Member #{mid}")
    except Exception:
        pass

    return f"Member #{mid}"


def _build_member_weekly_map(membership):
    rows = MemberWeeklyAvailability.objects.filter(membership=membership, is_active=True).order_by('weekday', 'start_time')
    mem_map = [[] for _ in range(7)]
    for row in rows:
        ui_idx = (row.weekday + 1) % 7
        mem_map[ui_idx].append(f"{row.start_time.strftime('%H:%M')}-{row.end_time.strftime('%H:%M')}")
    return mem_map


def _format_ranges_12h(ranges):
    """Convert ['HH:MM-HH:MM', ...] into 'h:MM AM - h:MM PM, ...'."""
    def _fmt_time(hhmm):
        try:
            s = str(hhmm).strip()
            if len(s) < 4:
                return ''
            hh = int(s[:2])
            mm = int(s[3:5])
            ampm = 'AM' if hh < 12 else 'PM'
            h12 = hh % 12
            if h12 == 0:
                h12 = 12
            return f"{h12}:{mm:02d} {ampm}"
        except Exception:
            return ''

    out = []
    for r in (ranges or []):
        try:
            a, b = [x.strip() for x in str(r).split('-', 1)]
        except Exception:
            continue
        sa = _fmt_time(a)
        sb = _fmt_time(b)
        if sa and sb:
            out.append(f"{sa} - {sb}")
    return ', '.join(out)

def home(request):
    try:
        from billing.models import Plan
        plans = Plan.objects.filter(is_active=True).order_by('price')
    except Exception:
        plans = []
    return render(request, "calendar_app/index.html", {"plans": plans})


def plan_detail(request, plan_slug):
    from billing.models import Plan
    plan = get_object_or_404(Plan, slug=plan_slug, is_active=True)
    return render(request, "calendar_app/plan_detail.html", {"plan": plan})


def contact(request):
    if request.method == 'POST':
        form = ContactForm(request.POST)
        if form.is_valid():
            business_name = form.cleaned_data['business_name']
            name = form.cleaned_data['name']
            email = form.cleaned_data['email']
            subject_key = form.cleaned_data['subject']
            other_subject = (form.cleaned_data.get('other_subject') or '').strip()
            message = form.cleaned_data['message']

            # Resolve the subject label for readability
            try:
                subject_map = dict(getattr(ContactForm, 'SUBJECT_CHOICES', []))
                subject_label = subject_map.get(subject_key, subject_key)
            except Exception:
                subject_label = subject_key

            final_subject = other_subject if subject_key == 'other' else subject_label

            body = (
                f"New contact form submission\n\n"
                f"Business: {business_name}\n"
                f"Name: {name}\n"
                f"Email: {email}\n"
                f"Subject: {final_subject}\n"
                f"Category: {subject_label}\n\n"
                f"Message:\n{message}\n"
            )

            # If email is configured, attempt to send. If not, still accept the
            # submission to avoid breaking UX in local/dev environments.
            try:
                to_addr = getattr(settings, 'DEFAULT_FROM_EMAIL', None) or getattr(settings, 'SERVER_EMAIL', None) or None
                if to_addr:
                    send_mail(
                        subject=f"[CircleCal Contact] {final_subject}",
                        message=body,
                        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None) or None,
                        recipient_list=[to_addr],
                        fail_silently=False,
                    )
            except Exception:
                pass

            messages.success(request, "Thanks — your message has been sent.")
            return redirect('calendar_app:contact')
    else:
        form = ContactForm()

    return render(request, 'calendar_app/contact.html', {'form': form})

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


@login_required
@require_roles(['owner', 'admin', 'manager', 'staff'])
def calendar_view(request, org_slug):
    org = request.organization
    if not org:
        # handle no organization (redirect to signup or choose business)
        return redirect('calendar_app:choose_business')

    # Trial/Basic/Pro: calendar is owner-only. Team plan enables staff access.
    is_team = False
    try:
        from billing.utils import can_add_staff
        is_team = bool(can_add_staff(org))
    except Exception:
        is_team = False

    # Per-date overrides are available on Pro/Team only (not Trial/Basic).
    can_use_overrides = False
    try:
        from billing.utils import get_plan_slug, PRO_SLUG, TEAM_SLUG, get_subscription
        plan_slug = get_plan_slug(org)
        sub = get_subscription(org)
        is_trialing = bool(sub and getattr(sub, 'status', '') == 'trialing')
        can_use_overrides = (plan_slug in {PRO_SLUG, TEAM_SLUG}) and (not is_trialing)
    except Exception:
        can_use_overrides = False

    if (not is_team) and (not user_has_role(request.user, org, ['owner'])):
        messages.error(request, 'Calendar access is available to the business owner only on your current plan.')
        return redirect('calendar_app:dashboard', org_slug=org.slug)

    # Per-date overrides are Pro/Team only (not Trial/Basic).
    can_use_overrides = False
    try:
        from billing.utils import get_plan_slug, PRO_SLUG, TEAM_SLUG, get_subscription
        plan_slug = get_plan_slug(org)
        subscription = get_subscription(org)
        is_trialing = bool(subscription and getattr(subscription, 'status', '') == 'trialing')
        can_use_overrides = (not is_trialing) and (plan_slug in {PRO_SLUG, TEAM_SLUG})
    except Exception:
        # Fail closed if billing is unavailable
        can_use_overrides = False
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
    # Internal calendar is a management view: include inactive services so owners
    # can see/edit them (public pages still filter to active-only).
    services_qs = Service.objects.filter(organization=org).order_by('name')
    trial_single_service_mode = False
    try:
        from billing.utils import get_subscription
        sub = get_subscription(org)
        if sub and getattr(sub, 'status', '') == 'trialing':
            try:
                trial_single_service_mode = (Service.objects.filter(organization=org, is_active=True).count() <= 1)
            except Exception:
                trial_single_service_mode = False
    except Exception:
        trial_single_service_mode = False
    services = []
    for s in services_qs:
        assigned_members = []
        if is_team:
            try:
                # ServiceAssignment has related_name='assignments'
                assigned_members = list(s.assignments.all().values_list('membership_id', flat=True))
            except Exception:
                # If migrations not applied / table missing, fail closed (treat as unassigned)
                assigned_members = []
        services.append({
            'id': s.id,
            'name': s.name,
            'slug': s.slug,
            'is_active': bool(getattr(s, 'is_active', True)),
            'duration': s.duration,
            'time_increment_minutes': getattr(s, 'time_increment_minutes', 30),
            'use_fixed_increment': bool(getattr(s, 'use_fixed_increment', False)),
            'allow_squished_bookings': bool(getattr(s, 'allow_squished_bookings', False)),
            'allow_ends_after_availability': bool(getattr(s, 'allow_ends_after_availability', False)),
            # Provide a simple weekly availability map for the client to compute next-available dates
            'weekly_map': _build_service_weekly_map(s),
            'has_service_weekly_windows': (False if trial_single_service_mode else bool(s.weekly_availability.filter(is_active=True).exists())),
            # assigned_members: list of membership ids allowed to deliver this service
            'assigned_members': assigned_members,
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
        # Per-membership availability map: membership_id -> availability payload (build from MemberWeeklyAvailability when present)
            # Build real per-membership weekly maps (use MemberWeeklyAvailability when present)
            'member_availability_map': json.dumps({
                str(mid): _build_member_weekly_map(mid)
                for mid in list(Membership.objects.filter(organization=org, is_active=True).values_list('id', flat=True))
            }),
        'org_timezone': org.timezone,  # Pass organization's timezone to template
        'services': services_qs,
        'services_json': services_json,
        'members_list': list(Membership.objects.filter(organization=org, is_active=True).values('id','user__first_name','user__last_name','user__email')),
        'is_team_plan': is_team,
        'can_use_overrides': can_use_overrides,
        # Default member id for selector: prefer membership row for organization owner, otherwise first active membership id
        'default_member_id': (lambda org_obj: (lambda owner_mem: owner_mem if owner_mem is not None else (Membership.objects.filter(organization=org_obj, is_active=True).values_list('id', flat=True).first()))(Membership.objects.filter(organization=org_obj, is_active=True, user=getattr(org_obj, 'owner', None)).values_list('id', flat=True).first()))(org),
        'auto_open_service': auto_open_service,
        'auto_open_date': auto_open_date,
        'audit_entries': AuditBooking.objects.filter(organization=org).order_by('-created_at')[:10],
    })

def demo_calendar_view(request):
    return render(request, "calendar_app/demo_calendar.html")


def _snapshot_weekly_windows_for_service_date(org, service, date_obj):
    """Return a list of {'start': 'HH:MM', 'end': 'HH:MM'} for the service/date.

    Snapshot is based on the weekly windows currently in effect for that service:
    - Prefer explicit ServiceWeeklyAvailability for that weekday.
    - Else fall back to org WeeklyAvailability.
    - If org has no weekly rows at all, treat as fully available (legacy).
    """
    try:
        wd = date_obj.weekday()  # model weekday 0=Mon..6=Sun
    except Exception:
        return []

    # Prefer explicit service weekly windows if present for that weekday.
    # Trial onboarding rule: when the org has only one active service, treat the
    # service schedule as org-scoped (calendar) regardless of service rows.
    skip_service_weekly = False
    try:
        from billing.utils import get_subscription
        subscription = get_subscription(org)
        if subscription and getattr(subscription, 'status', '') == 'trialing':
            try:
                active_ct = Service.objects.filter(organization=org, is_active=True).count()
            except Exception:
                active_ct = 0
            if active_ct <= 1:
                skip_service_weekly = True
    except Exception:
        skip_service_weekly = False

    if not skip_service_weekly:
        try:
            svc_rows = service.weekly_availability.filter(is_active=True, weekday=wd).order_by('start_time')
            if svc_rows.exists():
                return [{'start': r.start_time.strftime('%H:%M'), 'end': r.end_time.strftime('%H:%M')} for r in svc_rows]
        except Exception:
            pass

    # Org defaults
    try:
        any_org_rows = WeeklyAvailability.objects.filter(organization=org, is_active=True).exists()
    except Exception:
        any_org_rows = False
    if not any_org_rows:
        return [{'start': '00:00', 'end': '23:59'}]

    try:
        org_rows = WeeklyAvailability.objects.filter(organization=org, is_active=True, weekday=wd).order_by('start_time')
        return [{'start': r.start_time.strftime('%H:%M'), 'end': r.end_time.strftime('%H:%M')} for r in org_rows]
    except Exception:
        return []


def _service_settings_snapshot(service, weekly_windows=None):
    try:
        snap = {
            'duration': int(getattr(service, 'duration', 0) or 0),
            'buffer_after': int(getattr(service, 'buffer_after', 0) or 0),
            'time_increment_minutes': int(getattr(service, 'time_increment_minutes', 30) or 30),
            'use_fixed_increment': bool(getattr(service, 'use_fixed_increment', False)),
            'allow_ends_after_availability': bool(getattr(service, 'allow_ends_after_availability', False)),
            'allow_squished_bookings': bool(getattr(service, 'allow_squished_bookings', False)),
        }
    except Exception:
        snap = {}
    if weekly_windows is not None:
        snap['weekly_windows'] = weekly_windows
    return snap


def _ensure_weekly_freezes_for_booked_dates(org, services, org_tz, horizon):
    """Ensure ServiceSettingFreeze rows contain weekly window snapshots for booked dates."""
    if not services:
        return

    try:
        today_org = timezone.now().astimezone(org_tz).replace(hour=0, minute=0, second=0, microsecond=0)
    except Exception:
        today_org = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)

    try:
        from bookings.models import ServiceSettingFreeze
    except Exception:
        return

    # Only real bookings (exclude per-date override rows)
    try:
        bookings = Booking.objects.filter(
            organization=org,
            service__in=list(services),
            service__isnull=False,
            start__gte=today_org,
            start__lte=horizon,
        ).select_related('service')
    except Exception:
        return

    # Deduplicate by (service_id, local_date)
    pairs = set()
    for b in bookings:
        try:
            d = b.start.astimezone(org_tz).date()
        except Exception:
            d = b.start.date()
        if b.service_id:
            pairs.add((b.service_id, d))

    if not pairs:
        return

    svc_by_id = {s.id: s for s in services}
    for (sid, d) in pairs:
        svc = svc_by_id.get(sid)
        if not svc:
            continue
        weekly_windows = _snapshot_weekly_windows_for_service_date(org, svc, d)
        frozen_settings = _service_settings_snapshot(svc, weekly_windows=weekly_windows)

        try:
            existing = ServiceSettingFreeze.objects.filter(service=svc, date=d).first()
        except Exception:
            existing = None

        if existing:
            # Do not overwrite an existing freeze; only backfill weekly_windows if missing/empty.
            try:
                if isinstance(existing.frozen_settings, dict):
                    if not existing.frozen_settings.get('weekly_windows'):
                        new_settings = dict(existing.frozen_settings)
                        new_settings['weekly_windows'] = weekly_windows
                        existing.frozen_settings = new_settings
                        existing.save(update_fields=['frozen_settings'])
                else:
                    existing.frozen_settings = frozen_settings
                    existing.save(update_fields=['frozen_settings'])
            except Exception:
                continue
        else:
            try:
                ServiceSettingFreeze.objects.create(service=svc, date=d, frozen_settings=frozen_settings)
            except Exception:
                continue


@require_http_methods(['POST'])
def save_availability(request, slug):
    """Simple endpoint to accept weekly availability payload from the calendar UI for a given slug.

    This implementation is intentionally lightweight: it validates JSON and
    returns success. You can extend it to persist availability per-resource later.
    """
    org = request.organization
    # Reject anonymous requests early to avoid passing SimpleLazyObject into ORM lookups
    if not getattr(request.user, 'is_authenticated', False):
        return HttpResponseForbidden('Authentication required')
    # Permission: owners/admins may save any availability. Managers/staff may
    # save membership-specific availability for themselves only. We enforce
    # this below after parsing payload.target because the decorator would
    # otherwise block staff before we can inspect the target.
    # Enforce plan restriction: Basic cannot modify weekly availability
    try:
        from billing.utils import enforce_weekly_availability
        ok, msg = enforce_weekly_availability(org)
        if not ok:
            return HttpResponseForbidden(msg or "Upgrade required for weekly availability edits.")
    except Exception:
        # Fail open if billing module unavailable
        pass

    # Trial/Basic/Pro: calendar (and its availability editor) is owner-only.
    try:
        from billing.utils import can_add_staff
        if (not can_add_staff(org)) and (not user_has_role(request.user, org, ['owner'])):
            return HttpResponseForbidden('Calendar access is available to the business owner only on your current plan.')
    except Exception:
        # If billing evaluation fails, do not block.
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

    # ----- New: support bulk maps for members and services -----
    member_map = payload.get('member_map')
    service_map = payload.get('service_map')

    def _parse_availability_array(av_arr):
        """Parse an availability array (day/ranges/unavailable) into cleaned tuples."""
        out = []
        if not isinstance(av_arr, list):
            raise ValueError('availability must be a list')
        for row in av_arr:
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
                raise ValueError('Invalid day value')
            if wd < 0 or wd > 6:
                raise ValueError('weekday out of range')
            if unavailable:
                continue
            if not isinstance(ranges, list):
                raise ValueError('ranges must be a list')
            for r in ranges:
                try:
                    parts = str(r).split('-')
                    start = parts[0].strip()
                    end = parts[1].strip()
                except Exception:
                    raise ValueError('Invalid range entry')
                if not (len(start) == 5 and len(end) == 5 and start[2] == ':' and end[2] == ':'):
                    raise ValueError('Time must be HH:MM')
                if start >= end:
                    raise ValueError('Start must be before end')
                model_wd = ((wd - 1) % 7)
                out.append((model_wd, start, end))
        return out

    # Determine current membership early (used for permission checks below)
    current_membership = Membership.objects.filter(user=request.user, organization=org, is_active=True).first()

    # If client provided bulk maps, and user has permissions, persist them and return
    if member_map and isinstance(member_map, dict):
        # Only owner/admin may set arbitrary member maps; non-owner may only write their own membership
        if not (user_has_role(request.user, org, ('owner',)) or user_has_role(request.user, org, ('admin',))):
            # allow only if member_map contains solely the current_membership id
            allowed_id = str(current_membership.id) if current_membership else None
            for mid in member_map.keys():
                if str(mid) != str(allowed_id):
                    return HttpResponseForbidden('Insufficient permissions to set member availability')
        # Persist each membership's availability
        created_count = 0
        with transaction.atomic():
            for mid, av in member_map.items():
                try:
                    mem_id = int(mid)
                except Exception:
                    continue
                membership = Membership.objects.filter(id=mem_id, organization=org, is_active=True).first()
                if not membership:
                    continue
                try:
                    cleaned_rows = _parse_availability_array(av)
                except ValueError:
                    continue
                MemberWeeklyAvailability.objects.filter(membership=membership).delete()
                MemberWeeklyAvailability.objects.bulk_create([
                    MemberWeeklyAvailability(membership=membership, weekday=wd, start_time=start, end_time=end, is_active=True) for (wd, start, end) in cleaned_rows
                ])
                created_count += len(cleaned_rows)
        return JsonResponse({'success': True, 'member_count': created_count})

    if service_map and isinstance(service_map, dict):
        if not (user_has_role(request.user, org, ('owner',)) or user_has_role(request.user, org, ('admin',))):
            return HttpResponseForbidden('Insufficient permissions to set service availability')
        created_count = 0
        # Freeze weekly windows for booked dates before changing service weekly availability.
        try:
            org_tz = ZoneInfo(getattr(org, 'timezone', getattr(settings, 'TIME_ZONE', 'UTC')))
        except Exception:
            org_tz = timezone.get_current_timezone()
        try:
            max_days = 0
            for v in Service.objects.filter(organization=org).values_list('max_booking_days', flat=True):
                try:
                    max_days = max(max_days, int(v or 0))
                except Exception:
                    continue
        except Exception:
            max_days = 0
        try:
            horizon = timezone.now().astimezone(org_tz).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=max(max_days, 365))
        except Exception:
            horizon = timezone.now() + timedelta(days=365)
        with transaction.atomic():
            for sid, av in service_map.items():
                try:
                    svc_id = int(sid)
                except Exception:
                    continue
                svc = Service.objects.filter(id=svc_id, organization=org).first()
                if not svc:
                    continue
                try:
                    _ensure_weekly_freezes_for_booked_dates(org, [svc], org_tz, horizon)
                except Exception:
                    pass
                try:
                    cleaned_rows = _parse_availability_array(av)
                except ValueError:
                    continue
                ServiceWeeklyAvailability.objects.filter(service=svc).delete()
                ServiceWeeklyAvailability.objects.bulk_create([
                    ServiceWeeklyAvailability(service=svc, weekday=wd, start_time=start, end_time=end, is_active=True) for (wd, start, end) in cleaned_rows
                ])
                # New rule: a service with no weekly availability must be inactive.
                # Per-date overrides do not count.
                if not cleaned_rows:
                    try:
                        if getattr(svc, 'is_active', False):
                            svc.is_active = False
                            svc.save(update_fields=['is_active'])
                    except Exception:
                        pass
                created_count += len(cleaned_rows)
        return JsonResponse({'success': True, 'service_count': created_count})

    # Determine target: optional 'target' may indicate 'svc:<id>' or 'mem:<id>' or membership id
    target = None
    try:
        target = payload.get('target') if isinstance(payload, dict) else None
    except Exception:
        target = None

    # Persist to appropriate model depending on target
    # Permission check: allow owner/admin to save anything; allow a staff/manager
    # to save only their own membership target.
    current_membership = Membership.objects.filter(user=request.user, organization=org, is_active=True).first()

    if target:
        t = str(target)
        # Service-specific
        if t.startswith('svc:'):
            try:
                svc_id = int(t.split(':', 1)[1])
            except Exception:
                return HttpResponseBadRequest('Invalid service target')
            svc = Service.objects.filter(id=svc_id, organization=org).first()
            if not svc:
                return HttpResponseBadRequest('Service not found')
            # Only owner/admin may save service-level availability
            if not request.user_has_role('owner', org) and not request.user_has_role('admin', org):
                return HttpResponseForbidden('Insufficient permissions to set service availability')

            # If this is a single-assignee service, only allow service availability edits when applicable.
            try:
                from bookings.models import ServiceAssignment
                assigned_ids = list(
                    ServiceAssignment.objects.filter(service=svc)
                    .values_list('membership_id', flat=True)
                    .distinct()
                )
            except Exception:
                assigned_ids = []

            if len(assigned_ids) == 1:
                enabled, reason = _service_availability_applicability(org, svc)
                if not enabled:
                    return JsonResponse({'success': False, 'error': (reason or 'Service availability is not applicable for this service.')}, status=403)
                # Enforce subset: service windows must be within the member's effective availability.
                try:
                    _enforce_service_windows_within_member_availability(org, assigned_ids[0], cleaned)
                except ValueError as ve:
                    return JsonResponse({'success': False, 'error': str(ve)}, status=400)
                # Enforce partitioning: when a member has multiple solo services, service windows must not overlap.
                try:
                    _enforce_no_overlap_between_mixed_signature_solo_services(org, assigned_ids[0], svc, cleaned)
                except ValueError as ve:
                    return JsonResponse({'success': False, 'error': str(ve)}, status=400)

            # Freeze weekly windows for dates that already have bookings for this service
            # so later weekly edits don't change those booked days.
            try:
                try:
                    org_tz = ZoneInfo(getattr(org, 'timezone', getattr(settings, 'TIME_ZONE', 'UTC')))
                except Exception:
                    org_tz = timezone.get_current_timezone()
                today_org = timezone.now().astimezone(org_tz).replace(hour=0, minute=0, second=0, microsecond=0)
                try:
                    horizon = today_org + timedelta(days=max(int(getattr(svc, 'max_booking_days', 0) or 0), 365))
                except Exception:
                    horizon = today_org + timedelta(days=365)
                _ensure_weekly_freezes_for_booked_dates(org, [svc], org_tz, horizon)
            except Exception:
                pass

            with transaction.atomic():
                ServiceWeeklyAvailability.objects.filter(service=svc).delete()
                ServiceWeeklyAvailability.objects.bulk_create([
                    ServiceWeeklyAvailability(
                        service=svc,
                        weekday=wd,
                        start_time=start,
                        end_time=end,
                        is_active=True,
                    )
                    for (wd, start, end) in cleaned
                ])

                # New rule: a service with no weekly availability must be inactive.
                # Per-date overrides do not count.
                if not cleaned:
                    try:
                        if getattr(svc, 'is_active', False):
                            svc.is_active = False
                            svc.save(update_fields=['is_active'])
                    except Exception:
                        pass
            return JsonResponse({'success': True, 'count': len(cleaned), 'target': t})

        # Membership-specific (accept 'mem:<id>' or plain numeric id)
        mem_id = None
        if t.startswith('mem:'):
            try:
                mem_id = int(t.split(':', 1)[1])
            except Exception:
                mem_id = None
        elif t.isdigit():
            mem_id = int(t)

        if mem_id is not None:
            membership = Membership.objects.filter(id=mem_id, organization=org, is_active=True).first()
            if not membership:
                return HttpResponseBadRequest('Membership not found')
            # Allow if owner/admin, or if current user represents this membership
            if not (request.user_has_role('owner', org) or request.user_has_role('admin', org)):
                if not current_membership or current_membership.id != membership.id:
                    return HttpResponseForbidden('Insufficient permissions to set this member availability')

            # Guardrail: do not allow shrinking a member's overall availability below existing
            # explicit service availability for that member's solo services.
            try:
                for svc in _iter_member_solo_services(org, membership.id):
                    _enforce_service_windows_within_allowed_rows(cleaned, svc, err_prefix="Member availability update blocked:")
            except ValueError as ve:
                return JsonResponse({'success': False, 'error': str(ve)}, status=400)

            with transaction.atomic():
                MemberWeeklyAvailability.objects.filter(membership=membership).delete()
                MemberWeeklyAvailability.objects.bulk_create([
                    MemberWeeklyAvailability(
                        membership=membership,
                        weekday=wd,
                        start_time=start,
                        end_time=end,
                        is_active=True,
                    )
                    for (wd, start, end) in cleaned
                ])
            return JsonResponse({'success': True, 'count': len(cleaned), 'target': f'mem:{membership.id}'})

    # Default: organization-level weekly availability (existing behavior)
    # Guardrail: do not allow shrinking org defaults below explicit service availability for
    # solo services whose assignees inherit org defaults (no member override rows).
    try:
        # Identify memberships that currently inherit org defaults.
        inheriting_ids = list(
            Membership.objects.filter(organization=org, is_active=True)
            .exclude(id__in=MemberWeeklyAvailability.objects.filter(is_active=True).values_list('membership_id', flat=True))
            .values_list('id', flat=True)
        )
    except Exception:
        inheriting_ids = []
    if inheriting_ids:
        try:
            for mid in inheriting_ids:
                for svc in _iter_member_solo_services(org, mid):
                    _enforce_service_windows_within_allowed_rows(cleaned, svc, err_prefix="Org availability update blocked:")
        except ValueError as ve:
            return JsonResponse({'success': False, 'error': str(ve)}, status=400)

    # Freeze org-default weekly windows for booked dates of services that inherit org defaults
    # (i.e., no explicit service-weekly rows).
    try:
        try:
            org_tz = ZoneInfo(getattr(org, 'timezone', getattr(settings, 'TIME_ZONE', 'UTC')))
        except Exception:
            org_tz = timezone.get_current_timezone()
        today_org = timezone.now().astimezone(org_tz).replace(hour=0, minute=0, second=0, microsecond=0)
        try:
            max_days = 0
            for v in Service.objects.filter(organization=org).values_list('max_booking_days', flat=True):
                try:
                    max_days = max(max_days, int(v or 0))
                except Exception:
                    continue
        except Exception:
            max_days = 0
        horizon = today_org + timedelta(days=max(max_days, 365))

        svc_ids_with_explicit = set(ServiceWeeklyAvailability.objects.filter(is_active=True).values_list('service_id', flat=True))
        inheriting_svcs = list(Service.objects.filter(organization=org).exclude(id__in=list(svc_ids_with_explicit)))
        _ensure_weekly_freezes_for_booked_dates(org, inheriting_svcs, org_tz, horizon)
    except Exception:
        pass

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
            # If the user is being forwarded to the Django admin root, send
            # them to the Owner-branded login page instead so they see the
            # business-owner styled login (preserve the original next).
            try:
                from django.urls import reverse
                from urllib.parse import quote
                # Treat any admin-prefixed path as admin area (e.g. /admin/)
                if next_url and (next_url == '/admin/' or next_url.startswith('/admin')):
                    owner_login = reverse('accounts:login_owner')
                    return redirect(f"{owner_login}?next={quote(next_url)}")
            except Exception:
                # Fall back to original behavior on any error
                pass
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
    from billing.utils import get_subscription, get_plan_slug
    subscription = get_subscription(org)
    plan_slug = get_plan_slug(org)
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
        "plan_slug": plan_slug,
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
    try:
        from billing.utils import can_add_staff
        is_team_plan = bool(can_add_staff(org))
    except Exception:
        is_team_plan = False
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
        # Make new services active by default during onboarding.
        # (This view is retained for legacy routes; the main /bus/ flow already sets is_active=True.)
        try:
            svc_kwargs['is_active'] = True
        except Exception:
            pass
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

        # Persist assigned members only on Team plan
        if is_team_plan:
            try:
                from bookings.models import ServiceAssignment
                from accounts.models import Membership
                posted = request.POST.getlist('assigned_members') or []
                desired = set()
                for v in posted:
                    try:
                        iv = int(v)
                        if Membership.objects.filter(id=iv, organization=org).exists():
                            desired.add(iv)
                    except Exception:
                        continue
                for mid in desired:
                    try:
                        mem = Membership.objects.get(id=mid, organization=org)
                        ServiceAssignment.objects.create(service=svc, membership=mem)
                    except Exception:
                        continue
            except Exception:
                pass

        messages.success(request, "Service created.")
        return redirect("calendar_app:dashboard", org_slug=org.slug)

    return render(request, "calendar_app/create_service.html", { "org": org, "is_team_plan": is_team_plan })


@require_http_methods(["GET", "POST"])
@require_roles(["owner", "admin"])
def edit_service(request, org_slug, service_id):
    org = get_object_or_404(Organization, slug=org_slug)
    service = get_object_or_404(Service, id=service_id, organization=org)
    try:
        from billing.utils import can_add_staff
        is_team_plan = bool(can_add_staff(org))
    except Exception:
        is_team_plan = False
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
        # Update service assignments only on Team plan
        if is_team_plan:
            try:
                from bookings.models import ServiceAssignment
                # Accept either select-posted values or multiple same-name inputs
                posted = request.POST.getlist('assigned_members') or []
                # Normalize to ints, ignore invalid
                desired = set()
                from accounts.models import Membership
                for v in posted:
                    try:
                        iv = int(v)
                        # ensure membership belongs to this org
                        if Membership.objects.filter(id=iv, organization=org).exists():
                            desired.add(iv)
                    except Exception:
                        continue

                existing_qs = ServiceAssignment.objects.filter(service=service)
                existing_ids = set(existing_qs.values_list('membership_id', flat=True))

                to_add = desired - existing_ids
                to_remove = existing_ids - desired

                for mid in to_add:
                    try:
                        mem = Membership.objects.get(id=mid, organization=org)
                        ServiceAssignment.objects.create(service=service, membership=mem)
                    except Exception:
                        continue

                if to_remove:
                    ServiceAssignment.objects.filter(service=service, membership_id__in=list(to_remove)).delete()
            except Exception:
                # If migrations not applied or model missing, fail silently
                pass
        service.refresh_from_db()
        messages.success(request, "Service updated.")
        # Post-Redirect-Get: redirect so the saved state is authoritative and URL/query params propagate
        return redirect("calendar_app:edit_service", org_slug=org.slug, service_id=service.id)

    # Determine whether the slug may be edited: allow edits only when the service
    # has no real bookings (to avoid breaking existing public booking links).
    try:
        now = timezone.now()
        has_bookings = Booking.objects.filter(service=service, is_blocking=False, end__gte=now).exists()
    except Exception:
        has_bookings = False
    can_edit_slug = not has_bookings

    # Determine assigned member ids for template pre-selection (Team plan only)
    assigned_member_ids = []
    if is_team_plan:
        try:
            from bookings.models import ServiceAssignment
            assigned_member_ids = list(ServiceAssignment.objects.filter(service=service).values_list('membership_id', flat=True))
            assigned_member_ids = [str(x) for x in assigned_member_ids]
        except Exception:
            assigned_member_ids = []

    return render(request, "calendar_app/edit_service.html", {
        "org": org,
        "service": service,
        'needs_migration': not field_present,
        'can_edit_slug': can_edit_slug,
        'assigned_member_ids': assigned_member_ids,
        'is_team_plan': is_team_plan,
    })



def team_dashboard(request, org_slug):
    org = request.organization

    # Team plan required
    try:
        from billing.utils import can_add_staff
        if not can_add_staff(org):
            messages.error(request, 'Staff portal is available on the Team plan only.')
            return redirect('calendar_app:pricing_page', org_slug=org.slug)
    except Exception:
        messages.error(request, 'Staff portal is available on the Team plan only.')
        return redirect('calendar_app:pricing_page', org_slug=org.slug)

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

    # Team plan required
    try:
        from billing.utils import can_add_staff
        if not can_add_staff(org):
            messages.error(request, 'Staff portal is available on the Team plan only.')
            return redirect('calendar_app:pricing_page', org_slug=org.slug)
    except Exception:
        messages.error(request, 'Staff portal is available on the Team plan only.')
        return redirect('calendar_app:pricing_page', org_slug=org.slug)

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
                return redirect("calendar_app:team_dashboard", org_slug=org.slug)
        except Exception:
            pass

        inv = Invite.objects.create(
            organization=org,
            email=email,
            role=role,
            token=token
        )

        # Build accept URL and attempt to send email. Fall back to printing link.
        try:
            from django.urls import reverse
            from django.template.loader import render_to_string
            from django.core.mail import EmailMultiAlternatives
            from django.conf import settings

            accept_path = reverse('calendar_app:accept_invite', kwargs={'token': token})
            accept_url = request.build_absolute_uri(accept_path)

            context = {
                'org': org,
                'email': email,
                'role': role,
                'accept_url': accept_url,
                'site_url': getattr(settings, 'SITE_URL', request.build_absolute_uri('/')),
                'recipient_name': '',
            }

            subject = f"{org.name} invited you to join"
            text_content = render_to_string('calendar_app/emails/invite_email.txt', context)
            html_content = render_to_string('calendar_app/emails/invite_email.html', context)

            msg = EmailMultiAlternatives(subject, text_content, settings.DEFAULT_FROM_EMAIL, [email])
            msg.attach_alternative(html_content, "text/html")
            msg.send()

            messages.success(request, f"Invitation sent to {email}.")
            try:
                print("Invite link:", accept_url)
            except Exception:
                pass
        except Exception as e:
            try:
                print("Failed to send invite email:", e)
            except Exception:
                pass
            messages.error(request, f"Failed to send invitation to {email}. Invite was saved.")

        return redirect("calendar_app:team_dashboard", org_slug=org.slug)

    return HttpResponseForbidden("Invalid request")


def remove_member(request, org_slug, member_id):
    org = request.organization

    # Team plan required
    try:
        from billing.utils import can_add_staff
        if not can_add_staff(org):
            messages.error(request, 'Staff portal is available on the Team plan only.')
            return redirect('calendar_app:pricing_page', org_slug=org.slug)
    except Exception:
        messages.error(request, 'Staff portal is available on the Team plan only.')
        return redirect('calendar_app:pricing_page', org_slug=org.slug)

    if not user_has_role(request.user, org, ["owner", "admin"]):
        return HttpResponseForbidden("No permission.")

    member = get_object_or_404(Membership, id=member_id, organization=org)

    # Owner cannot remove themselves
    if member.user == org.owner:
        return HttpResponseForbidden("Cannot remove organization owner.")

    member.delete()
    return redirect("calendar_app:team_dashboard", org_slug=org.slug)

def update_member_role(request, org_slug, member_id):
    org = request.organization

    # Team plan required
    try:
        from billing.utils import can_add_staff
        if not can_add_staff(org):
            messages.error(request, 'Staff portal is available on the Team plan only.')
            return redirect('calendar_app:pricing_page', org_slug=org.slug)
    except Exception:
        messages.error(request, 'Staff portal is available on the Team plan only.')
        return redirect('calendar_app:pricing_page', org_slug=org.slug)

    if not user_has_role(request.user, org, ["owner", "admin"]):
        return HttpResponseForbidden("Not allowed.")

    member = get_object_or_404(Membership, id=member_id, organization=org)

    new_role = request.GET.get("role")
    if new_role not in ["owner", "admin", "manager", "staff"]:
        return HttpResponseForbidden("Invalid role.")

    member.role = new_role
    member.save()

    return redirect("calendar_app:team_dashboard", org_slug=org.slug)


def accept_invite(request, token):
    invite = get_object_or_404(Invite, token=token)

    # Team plan required
    try:
        from billing.utils import can_add_staff
        if not can_add_staff(invite.organization):
            messages.error(request, 'This business is not on the Team plan. Staff invites are disabled.')
            return redirect('calendar_app:pricing_page', org_slug=invite.organization.slug)
    except Exception:
        messages.error(request, 'This business is not on the Team plan. Staff invites are disabled.')
        return redirect('calendar_app:pricing_page', org_slug=invite.organization.slug)

    # If user is already authenticated, just create the membership and redirect
    if request.user.is_authenticated:
        user = request.user
        org = invite.organization
        Membership.objects.get_or_create(
            user=user,
            organization=org,
            defaults={"role": invite.role}
        )
        invite.accepted = True
        invite.save()
        return redirect(f"/bus/{org.slug}/calendar/")

    # Not authenticated: render a staff-only signup form tied to this invite.
    from calendar_app.forms import InviteSignupForm
    if request.method == 'POST':
        form = InviteSignupForm(request.POST)
        if form.is_valid():
            # Ensure the email matches the invite address
            email = form.cleaned_data.get('email')
            if email and email.lower() != invite.email.lower():
                form.add_error('email', 'Email must match the invited address.')
            else:
                user = form.save(commit=True)
                try:
                    user.is_active = True
                    user.save()
                except Exception:
                    pass
                # After creating the account, immediately attach the membership so
                # the user will be routed to the dashboard when they sign in.
                try:
                    Membership.objects.get_or_create(
                        user=user,
                        organization=invite.organization,
                        defaults={'role': invite.role}
                    )
                    invite.accepted = True
                    invite.save()
                except Exception:
                    # If membership creation fails, fall back to pending_invite so
                    # the login flow can attach it after authentication.
                    request.session['pending_invite'] = token
                messages.success(request, 'Account created. Please sign in to continue.')
                return redirect('accounts:login_staff')
    else:
        # Prefill email and make it read-only in the template
        form = InviteSignupForm(initial={'email': invite.email})

    # Detect if a user with this email already exists so we can offer a sign-in
    try:
        from django.contrib.auth import get_user_model
        User = get_user_model()
        existing_user = User.objects.filter(email__iexact=invite.email).exists()
    except Exception:
        existing_user = False

    return render(request, 'registration/invite_signup.html', {
        'form': form,
        'invite_email': invite.email,
        'role': invite.role,
        'existing_user': existing_user,
        'invite_token': token,
        'org': invite.organization,
    })





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
    from django.contrib.auth import logout as auth_logout
    auth_logout(request)
    return redirect('/')







@login_required
@require_roles(['owner', 'admin', 'manager', 'staff'])
def services_page(request, org_slug):
    """
    List all services for this organization (internal management page).
    """
    org = request.organization
    services = list(Service.objects.filter(organization=org).order_by('name'))

    can_add_more_services = True
    try:
        from billing.utils import can_add_service
        can_add_more_services = bool(can_add_service(org))
    except Exception:
        can_add_more_services = True

    upgrade_prompt = None
    try:
        upgrade_prompt = request.session.pop('cc_upgrade_prompt', None)
    except Exception:
        upgrade_prompt = None

    # Attach assigned member display names to each service for template rendering
    try:
        from bookings.models import ServiceAssignment
        assigned = ServiceAssignment.objects.filter(service__in=services).select_related('membership__user')
        assign_map = {}
        for a in assigned:
            try:
                user = getattr(a.membership, 'user', None)
                if not user:
                    continue
                # Prefer profile.display_name when present, fall back to full name or email
                display = None
                try:
                    display = getattr(user, 'profile').display_name
                except Exception:
                    display = None
                if not display:
                    fn = (getattr(user, 'first_name', '') or '').strip()
                    ln = (getattr(user, 'last_name', '') or '').strip()
                    if fn or ln:
                        display = f"{fn} {ln}".strip()
                    else:
                        display = getattr(user, 'email', '')
                assign_map.setdefault(a.service_id, []).append(display)
            except Exception:
                continue
        for s in services:
            s.assigned_names = assign_map.get(s.id, [])
    except Exception:
        for s in services:
            s.assigned_names = []

    return render(request, "calendar_app/services.html", {
        "org": org,
        "services": services,
        "can_add_more_services": can_add_more_services,
        "show_upgrade_modal": bool(upgrade_prompt == 'service_limit'),
        "upgrade_prompt_reason": upgrade_prompt,
    })


@login_required
@require_http_methods(['GET', 'POST'])
@require_roles(['owner', 'admin', 'manager'])
def create_service(request, org_slug):
    """
    Simple create-service form for coaches with refund fields.
    """
    org = request.organization

    # Pro/Team feature gate: advanced service settings + editable refund policy fields.
    is_team_plan = False
    can_use_pro_team = False
    try:
        from billing.utils import can_add_staff, get_plan_slug, PRO_SLUG, TEAM_SLUG, get_subscription
        is_team_plan = bool(can_add_staff(org))
        sub = get_subscription(org)
        is_trialing = bool(sub and getattr(sub, 'status', '') == 'trialing')
        plan_slug = get_plan_slug(org)
        can_use_pro_team = (plan_slug in {PRO_SLUG, TEAM_SLUG}) and (not is_trialing)
    except Exception:
        is_team_plan = False
        can_use_pro_team = False

    if request.method == "POST":
        # Plan enforcement: Basic only allows 1 active service
        try:
            from billing.utils import enforce_service_limit
            ok, msg = enforce_service_limit(org)
            if not ok:
                try:
                    request.session['cc_upgrade_prompt'] = 'service_limit'
                except Exception:
                    pass
                return redirect("calendar_app:services_page", org_slug=org.slug)
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
        min_notice_hours_raw = request.POST.get("min_notice_hours") or "24"
        max_booking_days_raw = request.POST.get("max_booking_days") or "30"

        if not name:
            messages.error(request, "Name is required.")
        else:
            # Require at-least-one assigned member for every service
            try:
                from accounts.models import Membership
                posted_assigned = request.POST.getlist('assigned_members') or []
                valid_found = False
                for v in posted_assigned:
                    try:
                        iv = int(v)
                        if Membership.objects.filter(id=iv, organization=org, is_active=True).exists():
                            valid_found = True
                            break
                    except Exception:
                        continue
                # Allow services to be created without assigned members (unassigned services are valid)
            except Exception:
                # Fail open if membership lookup not available
                pass
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
                # Trial/Basic: lock min notice + max advance.
                if not can_use_pro_team:
                    min_notice_hours = 24
                    max_booking_days = 30
                svc = Service.objects.create(
                    organization=org,
                    name=name,
                    slug=slug,
                    description=description,
                    duration=duration,
                    price=price,
                    buffer_before=buffer_before,
                    buffer_after=(buffer_after if can_use_pro_team else 0),
                    min_notice_hours=min_notice_hours,
                    max_booking_days=max_booking_days,
                    is_active=True,
                )
                # Per-service client slot settings
                try:
                    svc.time_increment_minutes = int(request.POST.get('time_increment_minutes', svc.time_increment_minutes if hasattr(svc, 'time_increment_minutes') else 30))
                except Exception:
                    svc.time_increment_minutes = 30
                # Advanced settings: Pro/Team only
                svc.use_fixed_increment = (request.POST.get('use_fixed_increment') is not None) if can_use_pro_team else False
                svc.allow_squished_bookings = (request.POST.get('allow_squished_bookings') is not None) if can_use_pro_team else False
                try:
                    if hasattr(svc, 'allow_ends_after_availability'):
                        svc.allow_ends_after_availability = (request.POST.get('allow_ends_after_availability') is not None) if can_use_pro_team else False
                except Exception:
                    pass

                # Refund policy: always on; cutoff/text editable only on Pro/Team.
                try:
                    svc.refunds_allowed = True
                except Exception:
                    pass
                if can_use_pro_team:
                    try:
                        svc.refund_cutoff_hours = int(request.POST.get("refund_cutoff_hours", svc.refund_cutoff_hours))
                    except Exception:
                        pass
                    svc.refund_policy_text = (request.POST.get("refund_policy_text") or "").strip()
                else:
                    try:
                        svc.refund_cutoff_hours = 24
                    except Exception:
                        pass
                    try:
                        svc.refund_policy_text = ""
                    except Exception:
                        pass
                svc.save()

                # Persist service assignments (which memberships can deliver this service)
                try:
                    from bookings.models import ServiceAssignment
                    from accounts.models import Membership
                    posted = request.POST.getlist('assigned_members') or []
                    desired = set()
                    for v in posted:
                        try:
                            iv = int(v)
                            if Membership.objects.filter(id=iv, organization=org, is_active=True).exists():
                                desired.add(iv)
                        except Exception:
                            continue
                    for mid in desired:
                        try:
                            mem = Membership.objects.get(id=mid, organization=org)
                            ServiceAssignment.objects.create(service=svc, membership=mem)
                        except Exception:
                            continue
                except Exception:
                    # Fail open if model/migration missing
                    pass

                messages.success(request, "Service created.")
                return redirect("calendar_app:services_page", org_slug=org.slug)

    # GET or form error → show empty/default form
    return render(request, "calendar_app/create_service.html", {
        "org": org,
        "is_team_plan": is_team_plan,
        "can_use_pro_team": can_use_pro_team,
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

    # Pro/Team feature gate: advanced service settings + editable refund policy fields.
    is_team_plan = False
    can_use_pro_team = False
    try:
        from billing.utils import can_add_staff, get_plan_slug, PRO_SLUG, TEAM_SLUG, get_subscription
        is_team_plan = bool(can_add_staff(org))
        sub = get_subscription(org)
        is_trialing = bool(sub and getattr(sub, 'status', '') == 'trialing')
        plan_slug = get_plan_slug(org)
        can_use_pro_team = (plan_slug in {PRO_SLUG, TEAM_SLUG}) and (not is_trialing)
    except Exception:
        is_team_plan = False
        can_use_pro_team = False

    # Refund policy is always-on for all plans.
    try:
        if hasattr(service, 'refunds_allowed') and (not bool(getattr(service, 'refunds_allowed', False))):
            service.refunds_allowed = True
            update_fields = ['refunds_allowed']
            try:
                if hasattr(service, 'refund_cutoff_hours') and int(getattr(service, 'refund_cutoff_hours', 0) or 0) < 1:
                    service.refund_cutoff_hours = 24
                    update_fields.append('refund_cutoff_hours')
            except Exception:
                pass
            service.save(update_fields=update_fields)
    except Exception:
        pass

    # Enforce locked values for non-Pro/Team so runtime behavior matches the UI.
    if not can_use_pro_team:
        update_fields = []
        try:
            if getattr(service, 'use_fixed_increment', False):
                service.use_fixed_increment = False
                update_fields.append('use_fixed_increment')
        except Exception:
            pass
        try:
            if getattr(service, 'allow_squished_bookings', False):
                service.allow_squished_bookings = False
                update_fields.append('allow_squished_bookings')
        except Exception:
            pass
        try:
            if hasattr(service, 'allow_ends_after_availability') and getattr(service, 'allow_ends_after_availability', False):
                service.allow_ends_after_availability = False
                update_fields.append('allow_ends_after_availability')
        except Exception:
            pass
        try:
            if int(getattr(service, 'buffer_after', 0) or 0) != 0:
                service.buffer_after = 0
                update_fields.append('buffer_after')
        except Exception:
            pass
        try:
            if hasattr(service, 'min_notice_hours') and int(getattr(service, 'min_notice_hours', 0) or 0) != 24:
                service.min_notice_hours = 24
                update_fields.append('min_notice_hours')
        except Exception:
            pass
        try:
            if hasattr(service, 'max_booking_days') and int(getattr(service, 'max_booking_days', 0) or 0) != 30:
                service.max_booking_days = 30
                update_fields.append('max_booking_days')
        except Exception:
            pass
        try:
            if hasattr(service, 'refunds_allowed') and (not bool(getattr(service, 'refunds_allowed', False))):
                service.refunds_allowed = True
                update_fields.append('refunds_allowed')
        except Exception:
            pass
        try:
            if hasattr(service, 'refund_cutoff_hours') and int(getattr(service, 'refund_cutoff_hours', 0) or 0) != 24:
                service.refund_cutoff_hours = 24
                update_fields.append('refund_cutoff_hours')
        except Exception:
            pass
        try:
            if hasattr(service, 'refund_policy_text') and (getattr(service, 'refund_policy_text', '') or '') != '':
                service.refund_policy_text = ''
                update_fields.append('refund_policy_text')
        except Exception:
            pass
        if update_fields:
            try:
                service.save(update_fields=list(dict.fromkeys(update_fields)))
            except Exception:
                pass

    # Trial/Basic guardrail: these orgs effectively operate in a single-service mode.
    # Ensure the org cannot end up with zero active services (which breaks the public page).
    # If this service is currently inactive and there are no other active services, force it active.
    try:
        from billing.utils import get_plan_slug, BASIC_SLUG, get_subscription
        plan_slug = get_plan_slug(org)
        sub = get_subscription(org)
        is_trialing = bool(sub and getattr(sub, 'status', '') == 'trialing')
        if plan_slug == BASIC_SLUG or is_trialing:
            active_ct = Service.objects.filter(organization=org, is_active=True).count()
            if active_ct == 0 and not bool(getattr(service, 'is_active', False)):
                service.is_active = True
                service.save(update_fields=['is_active'])
                try:
                    messages.info(request, 'Your service was set active to keep your public booking page available.')
                except Exception:
                    pass
    except Exception:
        pass

    # Team-only feature gate: facility resources
    try:
        from billing.utils import can_use_resources
        can_use_facility_resources = bool(can_use_resources(org))
    except Exception:
        can_use_facility_resources = False

    # Keep UI truthful: if a service has no weekly availability (overrides don't count), it cannot remain active.
    try:
        if getattr(service, 'is_active', False) and (not _service_has_effective_weekly_availability_for_activation(org, service)):
            service.is_active = False
            service.save(update_fields=['is_active'])
            # Ensure the in-memory object reflects the saved value
            try:
                service.refresh_from_db(fields=['is_active'])
            except Exception:
                pass
            try:
                messages.error(
                    request,
                    "This service was set to inactive because it has no weekly availability. "
                    "Add weekly availability (overrides don’t count) to activate it."
                )
            except Exception:
                pass
    except Exception:
        pass

    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        description = (request.POST.get("description") or "").strip()

        duration_raw = request.POST.get("duration") or "60"
        price_raw = request.POST.get("price") or "0"
        buffer_before_raw = request.POST.get("buffer_before") or "0"
        buffer_after_raw = request.POST.get("buffer_after") or "0"
        min_notice_hours_raw = request.POST.get("min_notice_hours") or "1"
        max_booking_days_raw = request.POST.get("max_booking_days") or "30"
        requested_active = request.POST.get("is_active") == "on"

        # Snapshot current service settings so we can freeze them for booked dates
        try:
            current_settings_snapshot = _service_settings_snapshot(service)
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
                service.buffer_after = buffer_after if can_use_pro_team else 0
                # Trial/Basic: lock min notice + max advance.
                service.min_notice_hours = min_notice_hours if can_use_pro_team else 24
                service.max_booking_days = max_booking_days if can_use_pro_team else 30
                service.is_active = requested_active

                # Per-service slot settings
                try:
                    service.time_increment_minutes = int(request.POST.get('time_increment_minutes', service.time_increment_minutes if hasattr(service, 'time_increment_minutes') else 30))
                except Exception:
                    service.time_increment_minutes = 30
                service.use_fixed_increment = (request.POST.get('use_fixed_increment') is not None) if can_use_pro_team else False
                service.allow_squished_bookings = (request.POST.get('allow_squished_bookings') is not None) if can_use_pro_team else False
                try:
                    if hasattr(service, 'allow_ends_after_availability'):
                        service.allow_ends_after_availability = (request.POST.get('allow_ends_after_availability') is not None) if can_use_pro_team else False
                except Exception:
                    pass

                # Refund policy: always on; cutoff/text editable only on Pro/Team.
                try:
                    service.refunds_allowed = True
                except Exception:
                    pass
                if can_use_pro_team:
                    cutoff_raw = request.POST.get("refund_cutoff_hours")
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
                    service.refund_policy_text = (request.POST.get("refund_policy_text") or "").strip()
                else:
                    try:
                        service.refund_cutoff_hours = 24
                    except Exception:
                        pass
                    try:
                        service.refund_policy_text = ""
                    except Exception:
                        pass

                # New rule: block activating a service unless it has weekly availability.
                # Per-date overrides do not count.
                if requested_active:
                    try:
                        if not _service_has_effective_weekly_availability_for_activation(org, service):
                            service.is_active = False
                            messages.error(
                                request,
                                "This service can’t be activated because it has no weekly availability. "
                                "Add weekly availability in the Service availability section or via the calendar first."
                            )
                    except Exception:
                        # If checks fail, fail open to avoid locking out editing.
                        pass

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
                new_use_fixed = (request.POST.get('use_fixed_increment') is not None) if can_use_pro_team else False
                new_allow_squished = (request.POST.get('allow_squished_bookings') is not None) if can_use_pro_team else False

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
                        org_ranges = _format_ranges_12h(org_map[ui]) if org_map and org_map[ui] else ''
                        svc_ranges = ', '.join(svc_map[ui]) if svc_map and svc_map[ui] else ''
                        weekly_edit_rows.append({'ui': ui, 'label': weekday_labels[ui], 'org_ranges': org_ranges, 'svc_ranges': svc_ranges})

                    # Provide the same flags the GET render uses so the template
                    # can correctly enable/disable sections.
                    try:
                        now = timezone.now()
                        has_bookings = Booking.objects.filter(service=service, is_blocking=False, end__gte=now).exists()
                    except Exception:
                        has_bookings = False
                    can_edit_slug = not has_bookings

                    try:
                        from bookings.models import ServiceAssignment
                        assigned_member_ids = [str(x) for x in ServiceAssignment.objects.filter(service=service).values_list('membership_id', flat=True).distinct()]
                    except Exception:
                        assigned_member_ids = []

                    can_edit_service_availability, service_availability_disabled_reason = _service_availability_applicability(org, service)

                    allowed_map = org_map
                    # For single-assignee partitioned services, constrain service windows
                    # to the assignee's weekly availability.
                    if can_edit_service_availability:
                        try:
                            mid = _get_single_assignee_membership_id(org, service)
                            if mid is not None:
                                allowed_map = _effective_member_weekly_map(org, mid)
                        except Exception:
                            allowed_map = org_map

                    for r in weekly_edit_rows:
                        try:
                            ui = int(r.get('ui'))
                        except Exception:
                            ui = None
                        if ui is None or ui < 0 or ui > 6:
                            r['allowed_ranges'] = ''
                        else:
                            try:
                                r['allowed_ranges'] = _format_ranges_12h(allowed_map[ui]) if allowed_map and allowed_map[ui] else ''
                            except Exception:
                                r['allowed_ranges'] = ''

                    service_availability_member_name = ""
                    try:
                        mid = _get_single_assignee_membership_id(org, service)
                    except Exception:
                        mid = None
                    if mid is not None and can_edit_service_availability:
                        service_availability_member_name = _get_single_assignee_display_name(org, service)

                    # Keep the same activation flags as the normal GET render so
                    # the template stays stable on this "conflicts" re-render.
                    try:
                        other_active = Service.objects.filter(organization=org, is_active=True).exclude(id=service.id).count()
                    except Exception:
                        other_active = 0
                    is_only_active_service = bool(getattr(service, 'is_active', False)) and (other_active == 0)
                    try:
                        can_activate_service = bool(_service_has_effective_weekly_availability_for_activation(org, service))
                    except Exception:
                        can_activate_service = True
                    activation_locked = (not can_activate_service) and (not getattr(service, 'is_active', False))
                    activation_lock_reason = "Add weekly availability (overrides don’t count) to activate this service." if activation_locked else ""
                    try:
                        activation_requires_service_weekly = bool(_service_requires_explicit_weekly(org, service))
                    except Exception:
                        activation_requires_service_weekly = False

                    # Facility resources context (Team-only). Use posted values
                    # so checkbox selections don't disappear on re-render.
                    if can_use_facility_resources:
                        try:
                            facility_resources = list(FacilityResource.objects.filter(organization=org).order_by('-is_active', 'name', 'id'))
                        except Exception:
                            facility_resources = []

                        if user_has_role(request.user, org, 'owner'):
                            posted_ids = request.POST.getlist('resource_ids') or []
                            selected_resource_ids = []
                            for v in posted_ids:
                                try:
                                    selected_resource_ids.append(int(v))
                                except Exception:
                                    continue
                        else:
                            try:
                                selected_resource_ids = list(ServiceResource.objects.filter(service=service).values_list('resource_id', flat=True))
                            except Exception:
                                selected_resource_ids = []

                        # Annotate each resource with capacity/disabled flags for the UI.
                        try:
                            res_ids = [r.id for r in facility_resources]
                            counts_qs = (
                                ServiceResource.objects
                                .filter(resource_id__in=res_ids)
                                .values('resource_id')
                                .annotate(ct=Count('service_id', distinct=True))
                            )
                            counts = {row['resource_id']: int(row.get('ct') or 0) for row in counts_qs}
                        except Exception:
                            counts = {}

                        for r in facility_resources:
                            rid = getattr(r, 'id', None)
                            used = int(counts.get(rid, 0))
                            try:
                                max_services = int(getattr(r, 'max_services', 1) or 0)
                            except Exception:
                                max_services = 1
                            is_selected = (rid in selected_resource_ids)
                            at_capacity = (max_services != 0) and (used >= max_services)
                            r.cc_max_services = max_services
                            r.cc_used_services = used
                            r.cc_disabled = bool(at_capacity and (not is_selected))
                    else:
                        facility_resources = []
                        selected_resource_ids = []

                    return render(request, "calendar_app/edit_service.html", {
                        "org": org,
                        "service": service,
                        "weekly_edit_rows": weekly_edit_rows,
                        "conflict_services": conflict_services,
                        "can_edit_slug": can_edit_slug,
                        'assigned_member_ids': assigned_member_ids,
                        'can_edit_service_availability': can_edit_service_availability,
                        'service_availability_disabled_reason': service_availability_disabled_reason,
                        'service_availability_member_name': service_availability_member_name,
                        'facility_resources': facility_resources,
                        'selected_resource_ids': selected_resource_ids,
                        'can_use_facility_resources': can_use_facility_resources,
                        "is_only_active_service": is_only_active_service,
                        "activation_locked": activation_locked,
                        "activation_lock_reason": activation_lock_reason,
                        "activation_requires_service_weekly": activation_requires_service_weekly,
                        'is_team_plan': is_team_plan,
                        'can_use_pro_team': can_use_pro_team,
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

                        # Create freezes preserving the current settings AND weekly windows for these dates
                        from django.db.utils import OperationalError
                        for d in booked_dates:
                            try:
                                frozen = dict(current_settings_snapshot or {})
                                frozen['weekly_windows'] = _snapshot_weekly_windows_for_service_date(org, service, d)
                                obj, created = ServiceSettingFreeze.objects.get_or_create(service=service, date=d, defaults={'frozen_settings': frozen})
                                # Do not overwrite existing freezes; leave prior frozen settings intact
                            except OperationalError:
                                # If migrations missing or DB error, skip freezes but continue
                                break
                except Exception:
                    # Defensive: don't block saving if freeze creation fails
                    pass

                # Prevent deactivating the last active service for the org.
                # Important: if activation was requested but blocked due to missing weekly availability,
                # we allow the service to remain inactive (do not enforce this guard).
                try:
                    if not requested_active:
                        other_active = Service.objects.filter(organization=org, is_active=True).exclude(id=service.id).count()
                        if other_active == 0:
                            messages.error(request, "At least one service must remain active for your public booking page. Activate another service first.")
                            return redirect("calendar_app:edit_service", org_slug=org.slug, service_id=service.id)
                except Exception:
                    pass

                # No team-plan assignment enforcement here; services may be unassigned

                service.save()

                # After changing scheduling-affecting fields, ensure existing service-weekly
                # windows still satisfy the solo-service overlap rule and remain within the
                # assigned member's overall weekly availability.
                try:
                    mid = _get_single_assignee_membership_id(org, service)
                except Exception:
                    mid = None
                if mid is not None:
                    try:
                        existing_rows = list(service.weekly_availability.filter(is_active=True).values_list('weekday', 'start_time', 'end_time'))
                        if existing_rows:
                            _enforce_service_windows_within_member_availability(org, mid, existing_rows)
                            _enforce_no_overlap_between_mixed_signature_solo_services(org, mid, service, existing_rows)
                    except ValueError as ve:
                        messages.error(request, str(ve))
                        return redirect("calendar_app:edit_service", org_slug=org.slug, service_id=service.id)
                # Sync service assignments (which memberships can deliver this service)
                try:
                    from bookings.models import ServiceAssignment
                    from accounts.models import Membership
                    posted = request.POST.getlist('assigned_members') or []
                    desired = set()
                    for v in posted:
                        try:
                            iv = int(v)
                            if Membership.objects.filter(id=iv, organization=org, is_active=True).exists():
                                desired.add(iv)
                        except Exception:
                            continue
                    # Allow empty assignment set (service may be unassigned)

                    existing_qs = ServiceAssignment.objects.filter(service=service)
                    existing_ids = set(existing_qs.values_list('membership_id', flat=True))
                    to_add = desired - existing_ids
                    to_remove = existing_ids - desired
                    for mid in to_add:
                        try:
                            mem = Membership.objects.get(id=mid, organization=org)
                            ServiceAssignment.objects.create(service=service, membership=mem)
                        except Exception:
                            continue
                    if to_remove:
                        ServiceAssignment.objects.filter(service=service, membership_id__in=list(to_remove)).delete()
                except Exception:
                    # Fail open if model/migration missing
                    pass

                # Sync facility resources allowed for this service
                try:
                    # Only the business owner can change facility resource wiring.
                    if not user_has_role(request.user, org, 'owner'):
                        raise Exception('Not permitted')
                    if not can_use_facility_resources:
                        raise Exception('Resources not available on this plan')
                    posted = request.POST.getlist('resource_ids') or []
                    desired = set()
                    for v in posted:
                        try:
                            rid = int(v)
                        except Exception:
                            continue
                        if FacilityResource.objects.filter(id=rid, organization=org).exists():
                            desired.add(rid)

                    # Capacity validation (max_services) — do not allow selecting a resource
                    # that is already linked to too many other services.
                    invalid = []
                    existing_ids = set(ServiceResource.objects.filter(service=service).values_list('resource_id', flat=True))
                    resources = list(FacilityResource.objects.filter(organization=org, id__in=list(desired)))
                    res_by_id = {r.id: r for r in resources}
                    for rid in desired:
                        r = res_by_id.get(rid)
                        if not r:
                            continue
                        try:
                            max_services = int(getattr(r, 'max_services', 1) or 0)
                        except Exception:
                            max_services = 1
                        if max_services == 0:
                            continue
                        try:
                            other_service_count = ServiceResource.objects.filter(resource_id=rid).exclude(service=service).values('service_id').distinct().count()
                        except Exception:
                            other_service_count = 0
                        if other_service_count >= max_services and (rid not in existing_ids):
                            invalid.append(r.name)
                    if invalid:
                        messages.error(request, 'These resources are already in use by other services: ' + ', '.join(invalid) + '.')
                        raise Exception('capacity violation')

                    existing_qs = ServiceResource.objects.filter(service=service)
                    existing_ids = set(existing_qs.values_list('resource_id', flat=True))
                    to_add = desired - existing_ids
                    to_remove = existing_ids - desired
                    for rid in to_add:
                        try:
                            ServiceResource.objects.create(service=service, resource_id=rid)
                        except Exception:
                            continue
                    if to_remove:
                        ServiceResource.objects.filter(service=service, resource_id__in=list(to_remove)).delete()
                except Exception:
                    pass

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
                                    try:
                                        if hasattr(other, 'allow_ends_after_availability'):
                                            other.allow_ends_after_availability = getattr(service, 'allow_ends_after_availability', False)
                                    except Exception:
                                        pass
                                    other.save()
                                    break
                # Handle per-service weekly availability fields.
                # Expect form fields named `svc_avail_0` .. `svc_avail_6` representing UI weekday 0=Sunday..6=Saturday
                # Each field may contain comma-separated ranges like "09:00-12:00,13:00-17:00" or be empty.
                can_edit_svc_avail, _reason = _service_availability_applicability(org, service)
                if can_edit_svc_avail:
                    svc_avail_had_error = False
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
                                svc_avail_had_error = True
                                continue
                            # Basic sanity check
                            if len(start_s) != 5 or len(end_s) != 5 or start_s[2] != ':' or end_s[2] != ':':
                                messages.error(request, f"Invalid time format for {key}: {part}")
                                svc_avail_had_error = True
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
                                svc_avail_had_error = True
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
                                svc_avail_had_error = True
                            else:
                                new_objs.append(obj)

                        if new_objs:
                            # Enforce subset + partition overlap guardrails before persisting.
                            try:
                                mid = _get_single_assignee_membership_id(org, service)
                                cleaned_rows = [(o.weekday, o.start_time, o.end_time) for o in new_objs]
                                if mid is not None:
                                    _enforce_service_windows_within_member_availability(org, mid, cleaned_rows)
                                    _enforce_no_overlap_between_mixed_signature_solo_services(org, mid, service, cleaned_rows)
                            except ValueError as ve:
                                messages.error(request, str(ve))
                                svc_avail_had_error = True
                            else:
                                # Replace existing windows
                                ServiceWeeklyAvailability.objects.filter(service=service).delete()
                                ServiceWeeklyAvailability.objects.bulk_create(new_objs)
                    else:
                        # If no posted windows present, remove any existing per-service windows
                        ServiceWeeklyAvailability.objects.filter(service=service).delete()

                    # If there were service-availability errors (including overlap/subset violations),
                    # do not show a success message.
                    if svc_avail_had_error:
                        return redirect("calendar_app:edit_service", org_slug=org.slug, service_id=service.id)
                
                messages.success(request, "Service updated.")
                # Return to edit page to reflect saved values immediately
                return redirect("calendar_app:edit_service", org_slug=org.slug, service_id=service.id)

    # Prepare rows for editing: label, org defaults, and service-specific defaults (string joined)
    org_map = _build_org_weekly_map(org)
    svc_map = _build_service_weekly_map(service)
    weekday_labels = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat']
    weekly_edit_rows = []
    for ui in range(7):
        org_ranges = _format_ranges_12h(org_map[ui]) if org_map and org_map[ui] else ''
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
    is_only_active_service = bool(getattr(service, 'is_active', False)) and (other_active == 0)

    # Activation lock: disable toggling on when the service has no weekly availability.
    # Overrides do not count.
    try:
        can_activate_service = bool(_service_has_effective_weekly_availability_for_activation(org, service))
    except Exception:
        can_activate_service = True
    activation_locked = (not can_activate_service) and (not getattr(service, 'is_active', False))
    activation_lock_reason = "Add weekly availability (overrides don’t count) to activate this service." if activation_locked else ""
    try:
        activation_requires_service_weekly = bool(_service_requires_explicit_weekly(org, service))
    except Exception:
        activation_requires_service_weekly = False

    # Safely compute assigned_member_ids (bookings.models may be unavailable if migrations missing)
    try:
        from bookings.models import ServiceAssignment
        assigned_member_ids = [str(x) for x in ServiceAssignment.objects.filter(service=service).values_list('membership_id', flat=True).distinct()]
    except Exception:
        assigned_member_ids = []

    can_edit_service_availability, service_availability_disabled_reason = _service_availability_applicability(org, service)

    allowed_map = org_map
    # For single-assignee partitioned services, constrain service windows to that member.
    if can_edit_service_availability:
        try:
            mid = _get_single_assignee_membership_id(org, service)
            if mid is not None:
                allowed_map = _effective_member_weekly_map(org, mid)
        except Exception:
            allowed_map = org_map

    for r in weekly_edit_rows:
        try:
            ui = int(r.get('ui'))
        except Exception:
            ui = None
        if ui is None or ui < 0 or ui > 6:
            r['allowed_ranges'] = ''
        else:
            try:
                r['allowed_ranges'] = _format_ranges_12h(allowed_map[ui]) if allowed_map and allowed_map[ui] else ''
            except Exception:
                r['allowed_ranges'] = ''

    service_availability_member_name = ""
    try:
        mid = _get_single_assignee_membership_id(org, service)
    except Exception:
        mid = None
    if mid is not None and can_edit_service_availability:
        service_availability_member_name = _get_single_assignee_display_name(org, service)

    # Facility resources selection context (Team-only)
    if can_use_facility_resources:
        try:
            facility_resources = list(FacilityResource.objects.filter(organization=org).order_by('-is_active', 'name', 'id'))
        except Exception:
            facility_resources = []
        try:
            selected_resource_ids = list(ServiceResource.objects.filter(service=service).values_list('resource_id', flat=True))
        except Exception:
            selected_resource_ids = []

        # Annotate each resource with capacity/disabled flags for the UI.
        try:
            res_ids = [r.id for r in facility_resources]
            counts_qs = (
                ServiceResource.objects
                .filter(resource_id__in=res_ids)
                .values('resource_id')
                .annotate(ct=Count('service_id', distinct=True))
            )
            counts = {row['resource_id']: int(row.get('ct') or 0) for row in counts_qs}
        except Exception:
            counts = {}

        for r in facility_resources:
            rid = getattr(r, 'id', None)
            used = int(counts.get(rid, 0))
            try:
                max_services = int(getattr(r, 'max_services', 1) or 0)
            except Exception:
                max_services = 1
            is_selected = (rid in selected_resource_ids)
            at_capacity = (max_services != 0) and (used >= max_services)
            # Disable only if at capacity and not currently selected by this service.
            r.cc_max_services = max_services
            r.cc_used_services = used
            r.cc_disabled = bool(at_capacity and (not is_selected))
    else:
        facility_resources = []
        selected_resource_ids = []

    return render(request, "calendar_app/edit_service.html", {
        "org": org,
        "service": service,
        "weekly_edit_rows": weekly_edit_rows,
        "can_edit_slug": can_edit_slug,
        "is_only_active_service": is_only_active_service,
        "activation_locked": activation_locked,
        "activation_lock_reason": activation_lock_reason,
        "activation_requires_service_weekly": activation_requires_service_weekly,
        'assigned_member_ids': assigned_member_ids,
        'can_edit_service_availability': can_edit_service_availability,
        'service_availability_disabled_reason': service_availability_disabled_reason,
        'service_availability_member_name': service_availability_member_name,
        'facility_resources': facility_resources,
        'selected_resource_ids': selected_resource_ids,
        'can_use_facility_resources': can_use_facility_resources,
        'is_team_plan': is_team_plan,
        'can_use_pro_team': can_use_pro_team,
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