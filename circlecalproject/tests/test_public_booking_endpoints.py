import uuid
from datetime import datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Business, Membership
from billing.models import Plan, Subscription
from bookings.models import Booking, Service, ServiceWeeklyAvailability, WeeklyAvailability


class TestPublicBookingEndpoints(TestCase):
    def setUp(self):
        User = get_user_model()
        self.client = Client()

        self.owner = User.objects.create_user(
            username=f'owner-{uuid.uuid4().hex[:8]}',
            email='owner@example.com',
            password='pass',
        )
        self.org = Business.objects.create(
            name='Public Org',
            slug=f'org-{uuid.uuid4().hex[:10]}',
            owner=self.owner,
            timezone='UTC',
        )
        Membership.objects.create(user=self.owner, organization=self.org, role='owner', is_active=True)

        plan = Plan.objects.create(name='Pro', slug='pro', description='Pro', price=0, billing_period='monthly')
        Subscription.objects.update_or_create(
            organization=self.org,
            defaults={'plan': plan, 'status': 'active', 'active': True},
        )

        self.service = Service.objects.create(
            organization=self.org,
            name='Batting Lesson',
            slug=f'svc-{uuid.uuid4().hex[:10]}',
            duration=30,
            price=0,
            buffer_before=0,
            buffer_after=0,
            min_notice_hours=0,
            max_booking_days=60,
            time_increment_minutes=30,
            show_on_public_calendar=True,
            is_active=True,
        )

        # Use tomorrow (UTC) to avoid min-notice and past-window issues.
        now_utc = timezone.now().astimezone(timezone.get_fixed_timezone(0))
        self.day = (now_utc.date() + timedelta(days=1))
        self.weekday = self.day.weekday()

        WeeklyAvailability.objects.create(
            organization=self.org,
            weekday=self.weekday,
            start_time=time(9, 0),
            end_time=time(12, 0),
            is_active=True,
        )
        ServiceWeeklyAvailability.objects.create(
            service=self.service,
            weekday=self.weekday,
            start_time=time(9, 0),
            end_time=time(12, 0),
            is_active=True,
        )

    def _iso_z(self, dt):
        # Ensure stable Z formatting for endpoints that accept ISO datetimes.
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_fixed_timezone(0))
        dt = dt.astimezone(timezone.get_fixed_timezone(0))
        return dt.isoformat().replace('+00:00', 'Z')

    def test_service_availability_returns_slots_and_excludes_busy(self):
        day_start = datetime(self.day.year, self.day.month, self.day.day, 0, 0, 0)
        day_end = day_start + timedelta(days=1)

        url = reverse('bookings:service_availability', args=[self.org.slug, self.service.slug])
        resp = self.client.get(url, {'start': self._iso_z(day_start), 'end': self._iso_z(day_end)})
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(isinstance(payload, list))
        self.assertTrue(len(payload) > 0)

        expected_slot_prefix = f"{self.day.isoformat()}T09:00"
        self.assertTrue(any((s.get('start') or '').startswith(expected_slot_prefix) for s in payload))

        # Add an actual booking at 09:00-09:30 and ensure that slot disappears.
        b_start = timezone.make_aware(datetime(self.day.year, self.day.month, self.day.day, 9, 0, 0), timezone.get_fixed_timezone(0))
        b_end = b_start + timedelta(minutes=30)
        Booking.objects.create(
            organization=self.org,
            service=self.service,
            title=self.service.name,
            start=b_start,
            end=b_end,
            is_blocking=False,
        )

        resp2 = self.client.get(url, {'start': self._iso_z(day_start), 'end': self._iso_z(day_end)})
        self.assertEqual(resp2.status_code, 200)
        payload2 = resp2.json()
        self.assertFalse(any((s.get('start') or '').startswith(expected_slot_prefix) for s in payload2))

    def test_batch_availability_summary_is_scoped_by_service_weekly_rows(self):
        day_start = datetime(self.day.year, self.day.month, self.day.day, 0, 0, 0)
        range_end = day_start + timedelta(days=3)

        url = reverse('bookings:batch_availability_summary', args=[self.org.slug, self.service.slug])
        resp = self.client.get(url, {'start': self._iso_z(day_start), 'end': self._iso_z(range_end)})
        self.assertEqual(resp.status_code, 200)
        summary = resp.json()
        self.assertTrue(isinstance(summary, dict))

        day1 = self.day.isoformat()
        day2 = (self.day + timedelta(days=1)).isoformat()
        day3 = (self.day + timedelta(days=2)).isoformat()

        self.assertIn(day1, summary)
        self.assertIn(day2, summary)
        self.assertIn(day3, summary)

        self.assertEqual(summary[day1], True)
        # Service has explicit weekly rows => other weekdays without service rows are unavailable.
        self.assertEqual(summary[day2], False)
        self.assertEqual(summary[day3], False)

    def test_public_busy_includes_bookings_in_range(self):
        b_start = timezone.make_aware(datetime(self.day.year, self.day.month, self.day.day, 10, 0, 0), timezone.get_fixed_timezone(0))
        b_end = b_start + timedelta(minutes=30)
        Booking.objects.create(
            organization=self.org,
            service=self.service,
            title=self.service.name,
            start=b_start,
            end=b_end,
            is_blocking=False,
        )

        url = reverse('bookings:public_busy', args=[self.org.slug])
        day_start = datetime(self.day.year, self.day.month, self.day.day, 0, 0, 0)
        day_end = day_start + timedelta(days=1)
        resp = self.client.get(url, {'start': self._iso_z(day_start), 'end': self._iso_z(day_end)})
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(isinstance(payload, list))
        self.assertTrue(any((it.get('start') or '').startswith(f"{self.day.isoformat()}T10:00") for it in payload))

    def test_booking_success_page_renders(self):
        b_start = timezone.make_aware(datetime(self.day.year, self.day.month, self.day.day, 11, 0, 0), timezone.get_fixed_timezone(0))
        b_end = b_start + timedelta(minutes=30)
        booking = Booking.objects.create(
            organization=self.org,
            service=self.service,
            title=self.service.name,
            start=b_start,
            end=b_end,
            is_blocking=False,
            payment_method='none',
            payment_status='not_required',
        )

        url = reverse('bookings:booking_success', args=[self.org.slug, self.service.slug, booking.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(self.service.name, resp.content.decode('utf-8', errors='ignore'))
