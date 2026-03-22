import json
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Business, Membership
from billing.models import Plan, Subscription
from bookings.models import Booking, Service


User = get_user_model()


class ApiCrossOrgSecurityTests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(username='owner-a', email='a@example.com', password='pw')
        self.user_b = User.objects.create_user(username='owner-b', email='b@example.com', password='pw')

        self.org_a = Business.objects.create(name='Org A', slug=f'org-a-{uuid.uuid4().hex[:6]}', owner=self.user_a)
        self.org_b = Business.objects.create(name='Org B', slug=f'org-b-{uuid.uuid4().hex[:6]}', owner=self.user_b)

        Membership.objects.create(user=self.user_a, organization=self.org_a, role='owner', is_active=True)
        Membership.objects.create(user=self.user_b, organization=self.org_b, role='owner', is_active=True)

        plan = Plan.objects.create(name='Team', slug=f'team-{uuid.uuid4().hex[:6]}', description='Team', price=0, billing_period='monthly')
        Subscription.objects.update_or_create(organization=self.org_a, defaults={'plan': plan, 'status': 'active', 'active': True})
        Subscription.objects.update_or_create(organization=self.org_b, defaults={'plan': plan, 'status': 'active', 'active': True})

        self.service_b = Service.objects.create(
            organization=self.org_b,
            name='Hidden Service',
            slug=f'hidden-{uuid.uuid4().hex[:6]}',
            duration=30,
            price=10,
            is_active=True,
            show_on_public_calendar=True,
        )
        Booking.objects.create(
            organization=self.org_b,
            service=self.service_b,
            title='Hidden Booking',
            start=timezone.now() + timedelta(days=1),
            end=timezone.now() + timedelta(days=1, minutes=30),
            client_name='Hidden Client',
            client_email='hidden@example.com',
        )

        self.client.force_login(self.user_a)

    def test_api_endpoints_reject_cross_org_queries(self):
        urls = [
            f'/api/v1/bookings/?org={self.org_b.slug}',
            f'/api/v1/services/?org={self.org_b.slug}',
            f'/api/v1/resources/?org={self.org_b.slug}',
            f'/api/v1/team/members/?org={self.org_b.slug}',
            f'/api/v1/billing/summary/?org={self.org_b.slug}',
        ]

        for url in urls:
            with self.subTest(url=url):
                resp = self.client.get(url)
                self.assertEqual(resp.status_code, 400)
                self.assertIn('detail', resp.json())

    def test_owner_cannot_mutate_other_org_calendar(self):
        resp = self.client.post(
            reverse('bookings:batch_create', kwargs={'org_slug': self.org_b.slug}),
            data=json.dumps({}),
            content_type='application/json',
        )

        self.assertEqual(resp.status_code, 403)
