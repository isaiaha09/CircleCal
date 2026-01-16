import json
import uuid
from datetime import datetime, time

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone

from accounts.models import Business, Membership
from billing.models import Plan, Subscription
from bookings.models import Booking, Service


class TestPerDateOverrideResetProtection(TestCase):
    def setUp(self):
        User = get_user_model()
        self.client = Client()

        self.owner = User.objects.create_user(username='owner', email='owner@example.com', password='pass')
        self.org = Business.objects.create(name='Org', slug=f'org-{uuid.uuid4().hex[:8]}', owner=self.owner)
        Membership.objects.create(user=self.owner, organization=self.org, role='owner', is_active=True)

        plan = Plan.objects.create(name='Team', slug='team', description='Team', price=0, billing_period='monthly')
        Subscription.objects.update_or_create(
            organization=self.org,
            defaults={'plan': plan, 'status': 'active', 'active': True},
        )

        self.svc = Service.objects.create(
            organization=self.org,
            name='Svc',
            slug=f'svc-{uuid.uuid4().hex[:8]}',
            duration=60,
        )

        self.client.force_login(self.owner)

    def _mk_dt(self, y, m, d, hh, mm):
        tz = timezone.get_current_timezone()
        return timezone.make_aware(datetime(y, m, d, hh, mm, 0), tz)

    def test_reset_mode_conflicts_when_booking_exists_same_day(self):
        # Real booking on the day
        Booking.objects.create(
            organization=self.org,
            service=self.svc,
            start=self._mk_dt(2030, 1, 7, 10, 0),
            end=self._mk_dt(2030, 1, 7, 11, 0),
            client_name='A',
            client_email='a@example.com',
            is_blocking=False,
        )

        # Create a per-date override in the same scope (service-scoped)
        create_payload = {
            'dates': ['2030-01-07'],
            'start_time': '09:00',
            'end_time': '09:30',
            'target': f'svc:{self.svc.id}',
        }
        resp_create = self.client.post(
            f'/bus/{self.org.slug}/bookings/batch_create/',
            data=json.dumps(create_payload),
            content_type='application/json',
            HTTP_HOST='127.0.0.1',
        )
        self.assertEqual(resp_create.status_code, 200)

        # Reset/undo should be blocked when a booking already exists for that service+day
        delete_payload = {
            'dates': ['2030-01-07'],
            'target': f'svc:{self.svc.id}',
            'mode': 'reset',
        }
        resp_del = self.client.post(
            f'/bus/{self.org.slug}/bookings/batch_delete/',
            data=json.dumps(delete_payload),
            content_type='application/json',
            HTTP_HOST='127.0.0.1',
        )
        self.assertEqual(resp_del.status_code, 409)

        # But a non-reset deletion is still permitted (admin/manual cleanup)
        delete_payload2 = {
            'dates': ['2030-01-07'],
            'target': f'svc:{self.svc.id}',
        }
        resp_del2 = self.client.post(
            f'/bus/{self.org.slug}/bookings/batch_delete/',
            data=json.dumps(delete_payload2),
            content_type='application/json',
            HTTP_HOST='127.0.0.1',
        )
        self.assertEqual(resp_del2.status_code, 200)
        data = json.loads(resp_del2.content.decode('utf-8'))
        self.assertGreaterEqual(int(data.get('deleted') or 0), 1)

    def test_reset_mode_allows_deletion_when_no_bookings_exist(self):
        create_payload = {
            'dates': ['2030-01-08'],
            'start_time': '09:00',
            'end_time': '09:30',
            'target': f'svc:{self.svc.id}',
        }
        resp_create = self.client.post(
            f'/bus/{self.org.slug}/bookings/batch_create/',
            data=json.dumps(create_payload),
            content_type='application/json',
            HTTP_HOST='127.0.0.1',
        )
        self.assertEqual(resp_create.status_code, 200)

        delete_payload = {
            'dates': ['2030-01-08'],
            'target': f'svc:{self.svc.id}',
            'mode': 'reset',
        }
        resp_del = self.client.post(
            f'/bus/{self.org.slug}/bookings/batch_delete/',
            data=json.dumps(delete_payload),
            content_type='application/json',
            HTTP_HOST='127.0.0.1',
        )
        self.assertEqual(resp_del.status_code, 200)
        data = json.loads(resp_del.content.decode('utf-8'))
        self.assertGreaterEqual(int(data.get('deleted') or 0), 1)
