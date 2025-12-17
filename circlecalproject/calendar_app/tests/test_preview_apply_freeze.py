from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth import get_user_model
import json
from datetime import timedelta

from accounts.models import Business, Membership
from bookings.models import Service, Booking, ServiceSettingFreeze


class TestServicePreviewApply(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='owner', email='owner@example.com', password='pass')
        self.org = Business.objects.create(name='Org', slug='org-slug', owner=self.user)
        Membership.objects.create(user=self.user, organization=self.org, role='owner', is_active=True)

        # Create a service
        self.service = Service.objects.create(
            organization=self.org,
            name='Consult',
            slug='consult',
            description='Test',
            duration=60,
            price=50,
            buffer_before=0,
            buffer_after=15,
            time_increment_minutes=30,
            use_fixed_increment=False,
            allow_squished_bookings=False,
            min_notice_hours=1,
            max_booking_days=30,
        )

        self.client = Client()
        self.client.force_login(self.user)

    def _preview_url(self):
        return reverse('calendar_app:preview_service_update', kwargs={'org_slug': self.org.slug, 'service_id': self.service.id})

    def _apply_url(self):
        return reverse('calendar_app:apply_service_update', kwargs={'org_slug': self.org.slug, 'service_id': self.service.id})

    def test_preview_includes_existing_freeze(self):
        # Create a booking two days out
        start = timezone.now() + timedelta(days=2)
        end = start + timedelta(minutes=self.service.duration)
        b = Booking.objects.create(organization=self.org, start=start, end=end, client_name='Alice', client_email='a@x.com', service=self.service)

        # Create an existing freeze for that date with a custom buffer
        freeze_payload = {
            'duration': self.service.duration,
            'buffer_after': 999,
            'time_increment_minutes': self.service.time_increment_minutes,
            'use_fixed_increment': False,
            'allow_ends_after_availability': False,
            'allow_squished_bookings': False,
        }
        ServiceSettingFreeze.objects.create(service=self.service, date=start.date(), frozen_settings=freeze_payload)

        # Propose a change that would normally create a freeze (change buffer_after)
        payload = {'buffer_after': self.service.buffer_after + 5}
        res = self.client.post(self._preview_url(), data=json.dumps(payload), content_type='application/json')
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data.get('status'), 'ok')
        conflicts = data.get('conflicts')
        self.assertTrue(conflicts)
        # Find booking date key
        day = start.astimezone(timezone.get_current_timezone()).date().isoformat()
        self.assertIn(day, conflicts)
        first = conflicts[day][0]
        self.assertIn('existing_freeze', first)
        self.assertEqual(first['existing_freeze']['buffer_after'], 999)

    def test_apply_preserves_existing_freeze(self):
        # Create a booking in 3 days and an existing freeze
        start = timezone.now() + timedelta(days=3)
        end = start + timedelta(minutes=self.service.duration)
        b = Booking.objects.create(organization=self.org, start=start, end=end, client_name='Bob', client_email='b@x.com', service=self.service)

        freeze_payload = {
            'duration': self.service.duration,
            'buffer_after': 888,
            'time_increment_minutes': self.service.time_increment_minutes,
            'use_fixed_increment': False,
            'allow_ends_after_availability': False,
            'allow_squished_bookings': False,
        }
        ServiceSettingFreeze.objects.create(service=self.service, date=start.date(), frozen_settings=freeze_payload)

        # Apply new settings (change buffer_after)
        payload = {'confirm': True, 'buffer_after': self.service.buffer_after + 20}
        res = self.client.post(self._apply_url(), data=json.dumps(payload), content_type='application/json')
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data.get('status'), 'ok')

        # Service should be updated
        self.service.refresh_from_db()
        self.assertEqual(self.service.buffer_after, payload['buffer_after'])

        # Freeze should still contain the original frozen buffer_after (preserved)
        f = ServiceSettingFreeze.objects.get(service=self.service, date=start.date())
        self.assertEqual(f.frozen_settings.get('buffer_after'), 888)

    def test_apply_no_bookings_updates_service_and_creates_no_freeze(self):
        # Ensure no bookings exist
        Booking.objects.filter(service=self.service).delete()

        payload = {'confirm': True, 'buffer_after': 1, 'duration': 45}
        res = self.client.post(self._apply_url(), data=json.dumps(payload), content_type='application/json')
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data.get('status'), 'ok')
        # No freezes created
        self.assertEqual(data.get('freezes_created'), 0)
        # Service updated
        self.service.refresh_from_db()
        self.assertEqual(self.service.buffer_after, 1)
        self.assertEqual(self.service.duration, 45)
