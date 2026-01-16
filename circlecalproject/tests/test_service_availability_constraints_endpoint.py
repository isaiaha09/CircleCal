import json
import uuid
from datetime import time

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Business, Membership
from billing.models import Plan, Subscription
from bookings.models import MemberWeeklyAvailability, WeeklyAvailability


class TestServiceAvailabilityConstraintsEndpoint(TestCase):
    def setUp(self):
        User = get_user_model()
        self.client = Client()

        self.owner = User.objects.create_user(username='owner', email='owner@example.com', password='pass')
        self.org = Business.objects.create(name='Org', slug=f'org-{uuid.uuid4().hex[:8]}', owner=self.owner)
        Membership.objects.create(user=self.owner, organization=self.org, role='owner', is_active=True)

        self.m1_user = User.objects.create_user(username='m1', email='m1@example.com', password='pass')
        self.m2_user = User.objects.create_user(username='m2', email='m2@example.com', password='pass')
        self.m1 = Membership.objects.create(user=self.m1_user, organization=self.org, role='staff', is_active=True)
        self.m2 = Membership.objects.create(user=self.m2_user, organization=self.org, role='staff', is_active=True)

        plan = Plan.objects.create(name='Team', slug='team', description='Team', price=0, billing_period='monthly')
        Subscription.objects.update_or_create(
            organization=self.org,
            defaults={'plan': plan, 'status': 'active', 'active': True},
        )

        # Provide an org weekly schedule; constraints endpoint should ignore this on Team
        # when no members are selected, but it should still work when members are selected.
        WeeklyAvailability.objects.create(
            organization=self.org,
            weekday=0,  # Monday
            start_time=time(9, 0),
            end_time=time(17, 0),
            is_active=True,
        )

        self.client.force_login(self.owner)

    def test_team_no_members_selected_is_unconstrained(self):
        url = reverse('calendar_app:service_availability_constraints', args=[self.org.slug])
        resp = self.client.get(url, HTTP_HOST='127.0.0.1')
        self.assertEqual(resp.status_code, 200)

        data = json.loads(resp.content.decode('utf-8'))
        self.assertIn('days', data)
        self.assertEqual(len(data['days']), 7)

        # UI weekday index: (model_weekday + 1) % 7 => Monday(0) -> 1
        monday = data['days'][1]
        self.assertEqual(monday.get('remaining'), [{'start': 0, 'end': 1439}])
        self.assertFalse(bool(monday.get('allowed_empty')))

    def test_team_member_selection_intersects_member_overall_availability(self):
        # Member 1: Mon 10-16
        MemberWeeklyAvailability.objects.create(
            membership=self.m1,
            weekday=0,
            start_time=time(10, 0),
            end_time=time(16, 0),
            is_active=True,
        )
        # Member 2: Mon 12-18
        MemberWeeklyAvailability.objects.create(
            membership=self.m2,
            weekday=0,
            start_time=time(12, 0),
            end_time=time(18, 0),
            is_active=True,
        )

        url = reverse('calendar_app:service_availability_constraints', args=[self.org.slug])
        resp = self.client.get(
            url,
            data={'member_ids': [str(self.m1.id), str(self.m2.id)]},
            HTTP_HOST='127.0.0.1',
        )
        self.assertEqual(resp.status_code, 200)

        data = json.loads(resp.content.decode('utf-8'))
        monday = data['days'][1]

        # Intersection: 12:00-16:00 => minutes 720-960
        self.assertEqual(monday.get('remaining'), [{'start': 720, 'end': 960}])

        # Text is presentation-level; just sanity check it references the right endpoints.
        txt = (monday.get('allowed_ranges_text') or '')
        self.assertIn('12:00', txt)
        self.assertTrue(('4:00' in txt) or ('16:00' in txt))
