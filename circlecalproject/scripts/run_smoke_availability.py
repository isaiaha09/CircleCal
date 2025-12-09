import os
import sys
import json
from datetime import datetime, timedelta, timezone

# Bootstrap Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'circlecalproject.settings')
import django
# Add the Django project directory (parent of the inner `circlecalproject` package)
DJANGO_PROJECT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if DJANGO_PROJECT_DIR not in sys.path:
    sys.path.insert(0, DJANGO_PROJECT_DIR)

print("DJANGO_PROJECT_DIR=", DJANGO_PROJECT_DIR)
print("sys.path[0]=", sys.path[0])
print("sys.path contains inner circlecalproject package?", any(p and os.path.exists(os.path.join(p, 'circlecalproject')) for p in sys.path))

django.setup()

from django.test import Client
from bookings.models import Service

client = Client()

services = list(Service.objects.select_related('organization').all())
if not services:
    print("No services found in DB. Aborting smoke tests.")
    sys.exit(0)

# Build a set of date windows: start dates = today, today+7, today+14; lengths = 1,3,7 days
today_utc = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
start_dates = [today_utc, today_utc + timedelta(days=7), today_utc + timedelta(days=14)]
lengths = [1, 3, 7]

test_incs = [None, 15]
edge_opts = [False, True]

results = []

def parse_iso(dt_str):
    try:
        return datetime.fromisoformat(dt_str)
    except Exception:
        return None

for svc in services:
    org = svc.organization
    print(f"\n=== Service: {svc.slug} (org={org.slug}) ===")
    for start_dt in start_dates:
        for length in lengths:
            end_dt = start_dt + timedelta(days=length)
            for inc in test_incs:
                for edge in edge_opts:
                    test_name = f"start_{start_dt.date()}_len{length}_inc{inc or 'default'}_edge{int(edge)}"
                    params = {"start": start_dt.isoformat(), "end": end_dt.isoformat()}
                    if inc is not None:
                        params['inc'] = str(inc)
                    if edge:
                        params['edge_buffers'] = '1'

                    url = f"/bus/{org.slug}/services/{svc.slug}/availability/"
                    try:
                        resp = client.get(url, params, HTTP_HOST='127.0.0.1')
                        status = resp.status_code
                        if status != 200:
                            print(f"{test_name}: HTTP {status}")
                            err_snip = resp.content.decode('utf-8', 'ignore')[:400]
                            results.append({"service": svc.slug, "org": org.slug, "test": test_name, "status": status, "error_snippet": err_snip})
                            continue
                        try:
                            payload = json.loads(resp.content)
                        except Exception as e:
                            print(f"{test_name}: invalid JSON response ({e})")
                            results.append({"service": svc.slug, "org": org.slug, "test": test_name, "status": status, "error": str(e)})
                            continue

                        # Heuristic checks / assertions
                        failures = []
                        warnings = []

                        # Determine expected increment (minutes)
                        if inc is not None:
                            expected_inc = inc
                        else:
                            try:
                                if getattr(svc, 'use_fixed_increment', False):
                                    expected_inc = svc.duration + (svc.buffer_after or 0)
                                else:
                                    expected_inc = getattr(svc, 'time_increment_minutes', svc.duration)
                            except Exception:
                                expected_inc = svc.duration

                        # Build mapping of weekly availability windows for quick lookup
                        # key: weekday -> list of (start_time, end_time)
                        weekly_map = {}
                        weekday_list = [0,1,2,3,4,5,6]
                        for wd in weekday_list:
                            rows = svc.weekly_availability.filter(is_active=True, weekday=wd)
                            if rows.exists():
                                weekly_map[wd] = [(r.start_time, r.end_time) for r in rows.order_by('start_time')]
                            else:
                                # fallback to org-level weekly availability
                                from bookings.models import WeeklyAvailability
                                org_rows = WeeklyAvailability.objects.filter(organization=org, is_active=True, weekday=wd)
                                weekly_map[wd] = [(r.start_time, r.end_time) for r in org_rows.order_by('start_time')]

                        for item in payload:
                            s = parse_iso(item.get('start'))
                            e = parse_iso(item.get('end'))
                            if not s or not e:
                                failures.append(f"invalid ISO in slot: {item}")
                                continue

                            # increment alignment check
                            minutes_since_midnight = s.hour * 60 + s.minute
                            if expected_inc and expected_inc > 0:
                                if minutes_since_midnight % expected_inc != 0:
                                    warnings.append(f"start {s.isoformat()} not aligned to inc {expected_inc}")

                            # allow_ends_after_availability check
                            wd = s.weekday()
                            windows = weekly_map.get(wd, [])
                            if windows:
                                # pick the window that contains the slot start, or the closest
                                match_window = None
                                for st_time, end_time in windows:
                                    win_start = s.replace(hour=st_time.hour, minute=st_time.minute, second=0, microsecond=0)
                                    win_end = s.replace(hour=end_time.hour, minute=end_time.minute, second=0, microsecond=0)
                                    if win_start <= s < win_end:
                                        match_window = (win_start, win_end)
                                        break
                                if match_window:
                                    win_start, win_end = match_window
                                    if e > win_end and not getattr(svc, 'allow_ends_after_availability', False):
                                        failures.append(f"slot ends after availability ({e.isoformat()} > {win_end.isoformat()}) but service disallows it")
                                else:
                                    # No matching weekly window found for this start — could be override or edge case
                                    warnings.append(f"no weekly window matched for start {s.isoformat()}")
                            else:
                                warnings.append(f"no weekly windows defined for weekday {wd}")

                        summary = {
                            "service": svc.slug,
                            "org": org.slug,
                            "test": test_name,
                            "status": status,
                            "count": len(payload),
                            "failures": failures,
                            "warnings": warnings,
                            "sample": payload[:5]
                        }
                        print(f"{test_name}: {len(payload)} slots; failures={len(failures)} warnings={len(warnings)}")
                        results.append(summary)
                    except Exception as e:
                        print(f"{test_name}: EXCEPTION {e}")
                        results.append({"service": svc.slug, "org": org.slug, "test": test_name, "status": 'EX', "error": str(e)})

# Print JSON results
print("\n=== Summary JSON ===")
print(json.dumps(results, indent=2, default=str))

# Exit success
sys.exit(0)
