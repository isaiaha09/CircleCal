from datetime import time
import json
import uuid

from django.test import TestCase, Client
from django.contrib.auth import get_user_model

from accounts.models import Business, Membership
from bookings.models import Service, ServiceAssignment, MemberWeeklyAvailability, ServiceWeeklyAvailability
from billing.models import Plan, Subscription


class TestPerDateOverrideGuardrails(TestCase):
    def setUp(self):
        User = get_user_model()
        self.client = Client()
        self.user = User.objects.create_user(username='owner', email='owner@example.com', password='pass')
        self.org = Business.objects.create(name='Org', slug=f'org-{uuid.uuid4().hex[:8]}', owner=self.user)
        self.mem = Membership.objects.create(user=self.user, organization=self.org, role='owner', is_active=True)

        # Team plan
        plan = Plan.objects.create(name='Team', slug='team', description='Team', price=0, billing_period='monthly')
        Subscription.objects.update_or_create(organization=self.org, defaults={'plan': plan, 'status': 'active', 'active': True})

        self.client.force_login(self.user)

        # Member overall weekly availability: Monday 09:00-17:00 (weekday=0)
        MemberWeeklyAvailability.objects.create(membership=self.mem, weekday=0, start_time=time(9, 0), end_time=time(17, 0), is_active=True)

        # Two solo services
        self.svc_a = Service.objects.create(organization=self.org, name='A', slug=f'a-{uuid.uuid4().hex[:6]}', duration=60)
        self.svc_b = Service.objects.create(organization=self.org, name='B', slug=f'b-{uuid.uuid4().hex[:6]}', duration=60)
        ServiceAssignment.objects.create(service=self.svc_a, membership=self.mem)
        ServiceAssignment.objects.create(service=self.svc_b, membership=self.mem)

        # Weekly partitions: A has 09:00-12:00, B has 12:00-17:00
        ServiceWeeklyAvailability.objects.create(service=self.svc_a, weekday=0, start_time=time(9, 0), end_time=time(12, 0), is_active=True)
        ServiceWeeklyAvailability.objects.create(service=self.svc_b, weekday=0, start_time=time(12, 0), end_time=time(17, 0), is_active=True)

    def test_service_cannot_make_available_overlapping_other_service(self):
        # Attempt to make B available 10:00-11:00 on a Monday -> overlaps A's weekly partition
        payload = {
            'dates': ['2030-01-07'],  # Monday
            'start_time': '10:00',
            'end_time': '11:00',
            'target': f'svc:{self.svc_b.id}',
        }
        resp = self.client.post(
            f'/bus/{self.org.slug}/bookings/batch_create/',
            data=json.dumps(payload),
            content_type='application/json',
            HTTP_HOST='127.0.0.1',
        )
        self.assertEqual(resp.status_code, 400)

    def test_blocking_other_service_frees_day_for_available_override(self):
        # Block A for the whole day
        block_payload = {
            'dates': ['2030-01-07'],
            'start_time': '00:00',
            'end_time': '23:59',
            'is_blocking': True,
            'target': f'svc:{self.svc_a.id}',
        }
        resp = self.client.post(
            f'/bus/{self.org.slug}/bookings/batch_create/',
            data=json.dumps(block_payload),
            content_type='application/json',
            HTTP_HOST='127.0.0.1',
        )
        self.assertEqual(resp.status_code, 200)

        # Now B can add availability during A's former window, but it must still be within B's weekly.
        # To test the "freed" behavior, temporarily add B's weekly to include 10:00-11:00.
        ServiceWeeklyAvailability.objects.filter(service=self.svc_b).delete()
        ServiceWeeklyAvailability.objects.create(service=self.svc_b, weekday=0, start_time=time(9, 0), end_time=time(17, 0), is_active=True)

        avail_payload = {
            'dates': ['2030-01-07'],
            'start_time': '10:00',
            'end_time': '11:00',
            'target': f'svc:{self.svc_b.id}',
        }
        resp2 = self.client.post(
            f'/bus/{self.org.slug}/bookings/batch_create/',
            data=json.dumps(avail_payload),
            content_type='application/json',
            HTTP_HOST='127.0.0.1',
        )
        self.assertEqual(resp2.status_code, 200)

    def test_service_cannot_expand_beyond_its_weekly_partition(self):
        # B weekly starts at 12:00; per-date availability at 11:00 should be rejected even if not overlapping
        payload = {
            'dates': ['2030-01-07'],
            'start_time': '11:00',
            'end_time': '11:30',
            'target': f'svc:{self.svc_b.id}',
        }
        resp = self.client.post(
            f'/bus/{self.org.slug}/bookings/batch_create/',
            data=json.dumps(payload),
            content_type='application/json',
            HTTP_HOST='127.0.0.1',
        )
        self.assertEqual(resp.status_code, 400)
