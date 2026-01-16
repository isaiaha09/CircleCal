import json
import uuid
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone

from accounts.models import Business, Membership
from billing.models import Plan, Subscription
from bookings.models import Booking, MemberWeeklyAvailability, Service, ServiceAssignment, ServiceWeeklyAvailability, WeeklyAvailability


class TestServiceAvailabilityCalendarInvariants(TestCase):
    def setUp(self):
        User = get_user_model()
        self.client = Client()

        self.owner = User.objects.create_user(username='owner', email='owner@example.com', password='pass')
        self.org = Business.objects.create(name='Org', slug=f'org-{uuid.uuid4().hex[:8]}', owner=self.owner)
        self.owner_mem = Membership.objects.create(user=self.owner, organization=self.org, role='owner', is_active=True)

        # Default to a non-trial, paid-plan subscription so the availability endpoint isn't
        # capped by trial_end.
        plan = Plan.objects.create(name='Team', slug='team', description='Team', price=0, billing_period='monthly')
        Subscription.objects.update_or_create(
            organization=self.org,
            defaults={'plan': plan, 'status': 'active', 'active': True},
        )

        self.staff_user = User.objects.create_user(username='staff', email='staff@example.com', password='pass')
        self.staff_mem = Membership.objects.create(user=self.staff_user, organization=self.org, role='staff', is_active=True)

        self.client.force_login(self.owner)

    def _org_tz(self):
        try:
            return ZoneInfo(getattr(self.org, 'timezone', getattr(settings, 'TIME_ZONE', 'UTC')))
        except Exception:
            return ZoneInfo(getattr(settings, 'TIME_ZONE', 'UTC'))

    def _set_plan(self, slug: str):
        plan, _ = Plan.objects.get_or_create(
            slug=slug,
            defaults={'name': slug.title(), 'description': slug.title(), 'price': 0, 'billing_period': 'monthly'},
        )
        Subscription.objects.update_or_create(
            organization=self.org,
            defaults={'plan': plan, 'status': 'active', 'active': True},
        )

    def _next_monday_date_str(self, *, min_days_ahead: int = 7) -> str:
        """Return an ISO date string for the next Monday at least N days ahead."""
        org_tz = self._org_tz()
        d = timezone.now().astimezone(org_tz).date() + timedelta(days=int(min_days_ahead))
        while d.weekday() != 0:
            d = d + timedelta(days=1)
        return d.isoformat()

    def _us_dst_start_date(self, year: int):
        """Second Sunday in March (US DST start date for America/New_York)."""
        d = datetime(year, 3, 1).date()
        while d.weekday() != 6:  # Sunday
            d = d + timedelta(days=1)
        return d + timedelta(days=7)

    def _mk_inherited_solo_service(
        self,
        *,
        name: str,
        duration: int,
        inc: int,
        use_fixed: bool,
        buffer_after: int = 0,
        min_notice_hours: int = 0,
        max_booking_days: int = 5000,
    ):
        svc = Service.objects.create(
            organization=self.org,
            name=name,
            slug=f"{name.lower()}-{uuid.uuid4().hex[:8]}",
            description='Test',
            duration=duration,
            price=0,
            buffer_before=0,
            buffer_after=buffer_after,
            time_increment_minutes=inc,
            use_fixed_increment=use_fixed,
            allow_squished_bookings=False,
            allow_ends_after_availability=False,
            min_notice_hours=min_notice_hours,
            max_booking_days=max_booking_days,
            is_active=True,
        )
        ServiceAssignment.objects.create(service=svc, membership=self.staff_mem)
        return svc

    def _availability(self, service: Service, date_str: str, *, inc: int | None = None, allow_empty: bool = False):
        url = f'/bus/{self.org.slug}/services/{service.slug}/availability/?start={date_str}T00:00:00&end={date_str}T23:59:59'
        if inc is not None:
            url += f'&inc={int(inc)}'
        resp = self.client.get(url, HTTP_HOST='127.0.0.1')
        self.assertEqual(resp.status_code, 200, resp.content.decode('utf-8'))
        data = json.loads(resp.content.decode('utf-8'))
        self.assertTrue(isinstance(data, list), f"Expected list JSON, got: {data}")

        if not data and allow_empty:
            return data

        if not data:
            dbg_url = url + '&debug_avail=1'
            dbg_resp = self.client.get(dbg_url, HTTP_HOST='127.0.0.1')
            try:
                dbg_data = json.loads(dbg_resp.content.decode('utf-8'))
            except Exception:
                dbg_data = dbg_resp.content.decode('utf-8')
            self.fail(f"Empty availability; debug_avail response: {dbg_data}")

        return data

    def test_availability_slot_spacing_respects_time_increment(self):
        # Monday
        date_str = self._next_monday_date_str()

        # Provide org weekly windows so the public calendar has a base schedule even
        # in trial-onboarding mode.
        WeeklyAvailability.objects.create(
            organization=self.org,
            weekday=0,
            start_time=time(9, 0),
            end_time=time(12, 0),
            is_active=True,
        )

        MemberWeeklyAvailability.objects.create(
            membership=self.staff_mem,
            weekday=0,
            start_time=time(9, 0),
            end_time=time(12, 0),
            is_active=True,
        )

        svc = self._mk_inherited_solo_service(name='Inc30', duration=60, inc=30, use_fixed=False)

        # Under current product rules, a service must have explicit weekly windows
        # to be bookable (it should not auto-inherit newly freed org/member time).
        ServiceWeeklyAvailability.objects.create(
            service=svc,
            weekday=0,
            start_time=time(9, 0),
            end_time=time(12, 0),
            is_active=True,
        )

        slots = self._availability(svc, date_str)
        self.assertGreaterEqual(len(slots), 2)

        starts = [datetime.fromisoformat(s['start']) for s in slots]
        # ensure monotonic
        self.assertEqual(starts, sorted(starts))

        # consecutive starts should be 30 minutes apart (UI stepping)
        deltas = [(starts[i + 1] - starts[i]) for i in range(min(len(starts) - 1, 4))]
        self.assertTrue(all(d == timedelta(minutes=30) for d in deltas))

        # each slot end should be exactly +duration
        for s in slots[:5]:
            st = datetime.fromisoformat(s['start'])
            en = datetime.fromisoformat(s['end'])
            self.assertEqual(en - st, timedelta(minutes=60))

    def test_availability_slot_spacing_respects_fixed_increment(self):
        # Monday
        date_str = self._next_monday_date_str()

        WeeklyAvailability.objects.create(
            organization=self.org,
            weekday=0,
            start_time=time(9, 0),
            end_time=time(12, 0),
            is_active=True,
        )

        MemberWeeklyAvailability.objects.create(
            membership=self.staff_mem,
            weekday=0,
            start_time=time(9, 0),
            end_time=time(12, 0),
            is_active=True,
        )

        svc = self._mk_inherited_solo_service(
            name='Fixed',
            duration=60,
            inc=10,  # ignored for fixed increment
            use_fixed=True,
            buffer_after=15,
        )

        ServiceWeeklyAvailability.objects.create(
            service=svc,
            weekday=0,
            start_time=time(9, 0),
            end_time=time(12, 0),
            is_active=True,
        )

        slots = self._availability(svc, date_str)
        self.assertGreaterEqual(len(slots), 1)

        starts = [datetime.fromisoformat(s['start']) for s in slots]
        # 60 + 15 = 75 min stepping
        if len(starts) >= 2:
            self.assertEqual(starts[1] - starts[0], timedelta(minutes=75))

        # slot length stays duration
        st0 = datetime.fromisoformat(slots[0]['start'])
        en0 = datetime.fromisoformat(slots[0]['end'])
        self.assertEqual(en0 - st0, timedelta(minutes=60))

    def test_member_block_then_availability_override_reopens_solo_service(self):
        # This is the key precedence invariant for "member availability priority".
        date_str = self._next_monday_date_str()  # Monday

        WeeklyAvailability.objects.create(
            organization=self.org,
            weekday=0,
            start_time=time(9, 0),
            end_time=time(17, 0),
            is_active=True,
        )

        MemberWeeklyAvailability.objects.create(
            membership=self.staff_mem,
            weekday=0,
            start_time=time(9, 0),
            end_time=time(17, 0),
            is_active=True,
        )

        svc = self._mk_inherited_solo_service(name='Solo', duration=60, inc=30, use_fixed=False)

        ServiceWeeklyAvailability.objects.create(
            service=svc,
            weekday=0,
            start_time=time(9, 0),
            end_time=time(17, 0),
            is_active=True,
        )
        org_tz = self._org_tz()

        # Create a member-scoped full-day block override.
        y, m, d = (int(x) for x in date_str.split('-'))
        day = datetime(y, m, d, 0, 0, 0, tzinfo=org_tz)
        Booking.objects.create(
            organization=self.org,
            service=None,
            start=day,
            end=day.replace(hour=23, minute=59, second=0, microsecond=0),
            is_blocking=True,
            assigned_user=self.staff_user,
            title='Unavailable',
        )

        slots1 = self._availability(svc, date_str, allow_empty=True)
        self.assertEqual(slots1, [])

        # Now explicitly reopen the day via an availability override.
        Booking.objects.create(
            organization=self.org,
            service=None,
            start=day.replace(hour=9, minute=0, second=0, microsecond=0),
            end=day.replace(hour=17, minute=0, second=0, microsecond=0),
            is_blocking=False,
            assigned_user=self.staff_user,
            title='Available',
        )

        slots2 = self._availability(svc, date_str)
        self.assertGreater(len(slots2), 0)

        first_start = datetime.fromisoformat(slots2[0]['start']).astimezone(org_tz)
        self.assertEqual((first_start.hour, first_start.minute), (9, 0))

    def test_availability_endpoint_smoke_across_plan_slugs(self):
        # This doesn’t attempt to prove plan semantics, but it does ensure the endpoint
        # remains stable (200 + parseable JSON) across plan configurations.
        date_str = self._next_monday_date_str()  # Monday

        WeeklyAvailability.objects.create(
            organization=self.org,
            weekday=0,
            start_time=time(9, 0),
            end_time=time(10, 0),
            is_active=True,
        )

        MemberWeeklyAvailability.objects.create(
            membership=self.staff_mem,
            weekday=0,
            start_time=time(9, 0),
            end_time=time(10, 0),
            is_active=True,
        )
        svc = self._mk_inherited_solo_service(name='PlanMatrix', duration=30, inc=15, use_fixed=False)

        ServiceWeeklyAvailability.objects.create(
            service=svc,
            weekday=0,
            start_time=time(9, 0),
            end_time=time(10, 0),
            is_active=True,
        )

        for slug in ('basic', 'pro', 'team'):
            with self.subTest(plan=slug):
                self._set_plan(slug)
                slots = self._availability(svc, date_str)
                # For the given window there should be at least one slot.
                self.assertGreaterEqual(len(slots), 1)

    def test_dst_smoke_does_not_error(self):
        # DST boundary smoke test: ensure the endpoint returns 200 and parseable JSON.
        # Note: this is not a full DST correctness proof.
        self.org.timezone = 'America/New_York'
        self.org.save(update_fields=['timezone'])

        org_tz = self._org_tz()
        today = timezone.now().astimezone(org_tz).date()
        year = today.year
        dst_start = self._us_dst_start_date(year)
        if dst_start <= today:
            dst_start = self._us_dst_start_date(year + 1)
        date_str = dst_start.isoformat()

        WeeklyAvailability.objects.create(
            organization=self.org,
            weekday=dst_start.weekday(),
            start_time=time(0, 0),
            end_time=time(4, 0),
            is_active=True,
        )

        MemberWeeklyAvailability.objects.create(
            membership=self.staff_mem,
            weekday=dst_start.weekday(),
            start_time=time(0, 0),
            end_time=time(4, 0),
            is_active=True,
        )

        # Ensure the booking horizon includes the chosen DST date.
        days_out = max((dst_start - today).days + 7, 60)
        svc = self._mk_inherited_solo_service(name='DST', duration=30, inc=30, use_fixed=False, max_booking_days=days_out)

        ServiceWeeklyAvailability.objects.create(
            service=svc,
            weekday=dst_start.weekday(),
            start_time=time(0, 0),
            end_time=time(4, 0),
            is_active=True,
        )
        slots = self._availability(svc, date_str)

        # Only assert that returned rows are parseable and ordered.
        starts = [datetime.fromisoformat(s['start']) for s in slots]
        ends = [datetime.fromisoformat(s['end']) for s in slots]
        self.assertEqual(starts, sorted(starts))
        self.assertTrue(all(e > s for s, e in zip(starts, ends)))
