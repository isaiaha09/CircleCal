import json
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from accounts.models import Business, Membership
from billing.models import Plan, Subscription
from bookings.models import Booking, Service


class OverrideBatchEndpointTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.client = Client()

        self.owner = User.objects.create_user(username='owner', email='owner@example.com', password='pass')
        self.org = Business.objects.create(name='Org', slug=f'org-{uuid.uuid4().hex[:8]}', owner=self.owner)
        self.owner_mem = Membership.objects.create(user=self.owner, organization=self.org, role='owner', is_active=True)

        plan = Plan.objects.create(name='Pro', slug='pro', description='Pro', price=0, billing_period='monthly')
        Subscription.objects.update_or_create(
            organization=self.org,
            defaults={'plan': plan, 'status': 'active', 'active': True},
        )

        self.staff_user = User.objects.create_user(username='staff', email='staff@example.com', password='pass')
        self.staff_mem = Membership.objects.create(user=self.staff_user, organization=self.org, role='staff', is_active=True)
        self.service = Service.objects.create(
            organization=self.org,
            name='Hitting',
            slug=f'hitting-{uuid.uuid4().hex[:6]}',
            duration=60,
            max_booking_days=5000,
        )

        self.client.force_login(self.owner)
        self.tz = ZoneInfo(getattr(self.org, 'timezone', getattr(settings, 'TIME_ZONE', 'UTC')))

    def _dt(self, hour: int, minute: int = 0):
        start = datetime(2030, 3, 5, hour, minute, tzinfo=self.tz)
        return start, start + timedelta(hours=1)

    def test_batch_delete_org_target_only_removes_org_scoped_overrides(self):
        start, end = self._dt(9)
        Booking.objects.create(
            organization=self.org,
            start=start,
            end=end,
            service=None,
            is_blocking=False,
        )
        Booking.objects.create(
            organization=self.org,
            start=start,
            end=end,
            service=None,
            is_blocking=False,
            client_name=f'scope:svc:{self.service.id}',
        )
        Booking.objects.create(
            organization=self.org,
            start=start,
            end=end,
            service=None,
            is_blocking=False,
            assigned_user=self.staff_user,
        )

        resp = self.client.post(
            f'/bus/{self.org.slug}/bookings/batch_delete/',
            data=json.dumps({'dates': ['2030-03-05'], 'target': '__org__'}),
            content_type='application/json',
            HTTP_HOST='127.0.0.1',
        )

        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content.decode('utf-8'))
        self.assertEqual(data['deleted'], 1)
        self.assertEqual(Booking.objects.filter(organization=self.org, service__isnull=True).count(), 2)
        self.assertTrue(Booking.objects.filter(organization=self.org, client_name=f'scope:svc:{self.service.id}').exists())
        self.assertTrue(Booking.objects.filter(organization=self.org, assigned_user=self.staff_user).exists())

    @patch('bookings.views.Booking.objects.create', side_effect=RuntimeError('db create failed'))
    def test_batch_create_returns_error_when_persistence_fails(self, _mock_create):
        resp = self.client.post(
            f'/bus/{self.org.slug}/bookings/batch_create/',
            data=json.dumps({
                'dates': ['2030-03-05'],
                'start_time': '09:00',
                'end_time': '10:00',
                'target': f'svc:{self.service.id}',
            }),
            content_type='application/json',
            HTTP_HOST='127.0.0.1',
        )

        self.assertEqual(resp.status_code, 500)
        data = json.loads(resp.content.decode('utf-8'))
        self.assertIn('error', data)
        self.assertEqual(data['created'], [])
        self.assertEqual(len(data['failures']), 1)
        self.assertIn('db create failed', data['failures'][0]['reason'])