import json
from datetime import timedelta
from datetime import datetime

from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.utils import timezone

from accounts.models import Business, Membership
from bookings.models import Service, WeeklyAvailability, Booking, ServiceSettingFreeze
from bookings.views import is_within_availability
from billing.models import Subscription


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
        # Establish initial org weekly availability for the booking weekday.
        # Use weekday=0 (Mon) in UI payload which maps to model weekday=6 (Sun)?
        # We'll avoid ambiguity: create model rows directly for the booking date weekday.
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

        # Create an actual booking on that date
        start_dt = booking_day.replace(hour=10, minute=0)
        end_dt = booking_day.replace(hour=10, minute=30)
        Booking.objects.create(organization=self.org, service=self.svc, client_name='A', client_email='a@example.com', start=start_dt, end=end_dt, is_blocking=False)

        # Change org weekly availability via endpoint to a window that excludes 10:00
        # (this would normally make 10:00 invalid on that weekday)
        new_payload = {
            'availability': [
                {'day': (wd + 1) % 7, 'ranges': ['13:00-14:00'], 'unavailable': False},
            ]
        }
        resp = self.client.post(f'/bus/{self.org.slug}/availability/save/', json.dumps(new_payload), content_type='application/json', HTTP_HOST='127.0.0.1')
        self.assertEqual(resp.status_code, 200)

        # Freeze should be created (or backfilled) for the booked date
        freeze = ServiceSettingFreeze.objects.filter(service=self.svc, date=booking_day.date()).first()
        self.assertIsNotNone(freeze)
        self.assertTrue(isinstance(freeze.frozen_settings, dict))
        self.assertTrue(freeze.frozen_settings.get('weekly_windows'))
        # Should contain the original 09:00-12:00 window
        self.assertIn({'start': '09:00', 'end': '12:00'}, freeze.frozen_settings.get('weekly_windows', []))

        # Availability check should still treat 10:00 as within availability due to freeze
        self.assertTrue(is_within_availability(self.org, start_dt, end_dt, service=self.svc))
