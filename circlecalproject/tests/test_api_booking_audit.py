from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import Business, Membership
from bookings.models import AuditBooking, Service


User = get_user_model()


class ApiBookingAuditTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='u1', email='u1@example.com', password='pw')
        self.other = User.objects.create_user(username='u2', email='u2@example.com', password='pw')

        self.org = Business.objects.create(name='Org', slug='org-1', owner=self.user)
        Membership.objects.update_or_create(
            user=self.user,
            organization=self.org,
            defaults={'role': 'owner', 'is_active': True},
        )

        self.svc = Service.objects.create(organization=self.org, name='Svc', duration=60, price=10)
        AuditBooking.objects.create(
            organization=self.org,
            booking_id=123,
            event_type=AuditBooking.EVENT_CANCELLED,
            booking_snapshot={'public_ref': 'ABC123', 'client_name': 'Jane'},
            service=self.svc,
            client_name='Jane',
            client_email='jane@example.com',
        )

    def test_requires_auth(self):
        resp = self.client.get(f'/api/v1/bookings/audit/?org={self.org.slug}')
        self.assertEqual(resp.status_code, 401)

    def test_requires_org_membership(self):
        self.client.force_login(self.other)
        resp = self.client.get(f'/api/v1/bookings/audit/?org={self.org.slug}')
        self.assertEqual(resp.status_code, 400)

    def test_returns_items(self):
        self.client.force_login(self.user)
        resp = self.client.get(f'/api/v1/bookings/audit/?org={self.org.slug}&include_snapshot=1')
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn('items', body)
        self.assertEqual(len(body['items']), 1)
        item = body['items'][0]
        self.assertEqual(item['booking_id'], 123)
        self.assertEqual(item['public_ref'], 'ABC123')
        self.assertEqual(item['event_type'], AuditBooking.EVENT_CANCELLED)
        self.assertIn('snapshot', item)
