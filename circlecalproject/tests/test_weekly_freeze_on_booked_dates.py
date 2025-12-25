import json
from datetime import datetime, timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone

from accounts.models import Business, Membership
from billing.models import Subscription
from bookings.models import Booking, Service, ServiceSettingFreeze, WeeklyAvailability
from bookings.views import is_within_availability


User = get_user_model()


class WeeklyFreezeBookedDatesTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='freeze_user', password='pass', email='freeze@example.com')
        self.org = Business.objects.create(name='Freeze Org', slug='freeze-org', owner=self.user)
        Membership.objects.create(user=self.user, organization=self.org, role='owner', is_active=True)

        # Ensure weekly availability edits are allowed by billing gates
        Subscription.objects.update_or_create(
            organization=self.org,
            defaults={
                'status': 'trialing',
                'trial_end': timezone.now() + timezone.timedelta(days=7),
                'active': False,
            }
        )

        self.svc = Service.objects.create(organization=self.org, name='Freeze Service', slug='freeze-service', duration=30)
        self.client.force_login(self.user)

    def test_org_weekly_edit_creates_freeze_for_booked_date_and_availability_uses_it(self):
        org_tz = timezone.get_current_timezone()
        booking_day = (timezone.now().astimezone(org_tz) + timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
        wd = booking_day.weekday()  # model weekday

        WeeklyAvailability.objects.create(
            organization=self.org,
            weekday=wd,
            start_time=datetime.strptime('09:00', '%H:%M').time(),
            end_time=datetime.strptime('12:00', '%H:%M').time(),
            is_active=True,
        )

        start_dt = booking_day.replace(hour=10, minute=0)
        end_dt = booking_day.replace(hour=10, minute=30)
        Booking.objects.create(
            organization=self.org,
            service=self.svc,
            client_name='A',
            client_email='a@example.com',
            start=start_dt,
            end=end_dt,
            is_blocking=False,
        )

        # Update org weekly availability to exclude 10:00; booked date should be frozen.
        ui_day = (wd + 1) % 7  # model weekday 0=Mon..6=Sun -> UI weekday 0=Sun..6=Sat
        payload = {
            'availability': [
                {'day': ui_day, 'ranges': ['13:00-14:00'], 'unavailable': False},
            ]
        }
        resp = self.client.post(
            f'/bus/{self.org.slug}/availability/save/',
            json.dumps(payload),
            content_type='application/json',
            HTTP_HOST='127.0.0.1',
        )
        self.assertEqual(resp.status_code, 200)

        freeze = ServiceSettingFreeze.objects.filter(service=self.svc, date=booking_day.date()).first()
        self.assertIsNotNone(freeze)
        self.assertTrue(isinstance(freeze.frozen_settings, dict))
        self.assertIn({'start': '09:00', 'end': '12:00'}, freeze.frozen_settings.get('weekly_windows', []))

        # Availability check should still allow the booked time window.
        self.assertTrue(is_within_availability(self.org, start_dt, end_dt, service=self.svc))
