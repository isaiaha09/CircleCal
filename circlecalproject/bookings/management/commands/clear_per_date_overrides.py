import argparse
from datetime import datetime, timedelta
from typing import Optional

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_date

from accounts.models import Business
from bookings.models import Booking


class Command(BaseCommand):
    help = (
        "Delete per-date override rows (Booking with service=NULL). "
        "These are the calendar overrides (org/service/member scoped), not real bookings."
    )

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--org",
            dest="org_slug",
            help="Business slug to scope deletes to (recommended).",
        )
        parser.add_argument(
            "--date",
            dest="date_str",
            help="Delete overrides for a single date (YYYY-MM-DD, in org timezone).",
        )
        parser.add_argument(
            "--start",
            dest="start_date_str",
            help="Start date (YYYY-MM-DD) for a range delete (inclusive).",
        )
        parser.add_argument(
            "--end",
            dest="end_date_str",
            help="End date (YYYY-MM-DD) for a range delete (inclusive).",
        )
        parser.add_argument(
            "--target",
            dest="target",
            help=(
                "Optional scope filter: membership id (e.g. 12) for member-scoped overrides, "
                "or service scope marker (svc:<service_id>) for service-scoped overrides. "
                "If omitted, deletes all override scopes."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show how many rows would be deleted, but do not delete.",
        )

    def handle(self, *args, **options):
        org_slug = (options.get("org_slug") or "").strip()
        date_str = (options.get("date_str") or "").strip()
        start_date_str = (options.get("start_date_str") or "").strip()
        end_date_str = (options.get("end_date_str") or "").strip()
        target = options.get("target")
        dry_run = bool(options.get("dry_run"))

        if date_str and (start_date_str or end_date_str):
            raise CommandError("Use either --date OR (--start/--end), not both.")

        if (start_date_str and not end_date_str) or (end_date_str and not start_date_str):
            raise CommandError("When using a range, provide BOTH --start and --end.")

        org: Optional[Business] = None
        if org_slug:
            org = Business.objects.filter(slug=org_slug).first()
            if not org:
                raise CommandError(f"No Business found with slug={org_slug!r}")

        # Base: per-date overrides are stored as Booking rows with service NULL.
        qs = Booking.objects.filter(service__isnull=True)
        if org is not None:
            qs = qs.filter(organization=org)

        # Optional target scoping
        if target:
            t = str(target).strip()
            if t.startswith("svc:"):
                qs = qs.filter(client_name=t.replace("svc:", "scope:svc:", 1))
            else:
                # Member scope: assigned_user != null (best-effort id parse)
                try:
                    mid = int(t)
                except Exception as e:
                    raise CommandError(f"Invalid --target {t!r}. Expected membership id or svc:<id>.") from e
                # Membership -> user id mapping (member-scoped overrides are stored on assigned_user)
                from accounts.models import Membership

                mem = Membership.objects.select_related("user").filter(id=mid).first()
                if not mem or not getattr(mem, "user_id", None):
                    raise CommandError(f"No Membership/user found for id={mid}")
                qs = qs.filter(assigned_user_id=mem.user_id)

        # Optional date / date range filtering (in org timezone). We store timestamps,
        # so we filter by overlaps against day start/end.
        if date_str:
            d = parse_date(date_str)
            if not d:
                raise CommandError("--date must be YYYY-MM-DD")
            qs = self._filter_day_overlap(qs, org, d)
        elif start_date_str and end_date_str:
            sd = parse_date(start_date_str)
            ed = parse_date(end_date_str)
            if not sd or not ed:
                raise CommandError("--start/--end must be YYYY-MM-DD")
            if ed < sd:
                raise CommandError("--end must be >= --start")
            # Expand into an inclusive day window range
            qs = self._filter_range_overlap(qs, org, sd, ed)

        count = qs.count()
        label = "would delete" if dry_run else "deleting"
        scope = f"org={org_slug}" if org_slug else "org=<ALL>"
        self.stdout.write(f"Found {count} per-date override row(s) ({label}, {scope}).")
        if dry_run:
            return

        deleted = qs.delete()
        # deleted is (num_deleted, {model_label: num, ...})
        self.stdout.write(f"Deleted {deleted[0]} row(s).")

    def _org_tz(self, org: Optional[Business]):
        if org is None:
            return timezone.get_current_timezone()
        try:
            # org.timezone is stored as a TZ name; Django can use it via zoneinfo
            from zoneinfo import ZoneInfo

            return ZoneInfo(getattr(org, "timezone", "UTC") or "UTC")
        except Exception:
            return timezone.get_current_timezone()

    def _filter_day_overlap(self, qs, org: Optional[Business], d):
        tz = self._org_tz(org)
        day_start = datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=tz)
        day_end = day_start + timedelta(days=1)
        return qs.filter(start__lt=day_end, end__gt=day_start)

    def _filter_range_overlap(self, qs, org: Optional[Business], sd, ed):
        tz = self._org_tz(org)
        start_dt = datetime(sd.year, sd.month, sd.day, 0, 0, 0, tzinfo=tz)
        end_dt = datetime(ed.year, ed.month, ed.day, 0, 0, 0, tzinfo=tz) + timedelta(days=1)
        return qs.filter(start__lt=end_dt, end__gt=start_dt)
