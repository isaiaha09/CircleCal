import uuid
from datetime import time

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Business, Membership
from billing.models import Plan, Subscription
from bookings.models import Service, WeeklyAvailability


class TestTeamUnassignedServiceConstraints(TestCase):
    def setUp(self):
        User = get_user_model()
        self.client = Client()

        self.owner = User.objects.create_user(username='owner_team', email='owner_team@example.com', password='pass')
        self.org = Business.objects.create(name='OrgTeam', slug=f'org-{uuid.uuid4().hex[:8]}', owner=self.owner)
        Membership.objects.create(user=self.owner, organization=self.org, role='owner', is_active=True)

        team_plan = Plan.objects.create(name='Team', slug='team', description='Team', price=0, billing_period='monthly')
        Subscription.objects.update_or_create(
            organization=self.org,
            defaults={'plan': team_plan, 'status': 'active', 'active': True},
        )

        # Define overall org availability (Mon-Fri 9-5) so org_map is constrained,
        # but Team unassigned services should still show full 24/7 allowed ranges.
        for wd in range(0, 5):
            WeeklyAvailability.objects.create(
                organization=self.org,
                weekday=wd,
                start_time=time(9, 0),
                end_time=time(17, 0),
                is_active=True,
            )

        self.client.force_login(self.owner)

    def _allowed_for_label(self, weekly_edit_rows, label):
        for row in weekly_edit_rows:
            if row.get('label') == label:
                return (row.get('allowed_ranges') or '').strip()
        return ''

    def test_create_service_team_unassigned_is_full_week(self):
        url = reverse('calendar_app:create_service', args=[self.org.slug])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

        rows = resp.context.get('weekly_edit_rows')
        self.assertTrue(isinstance(rows, list) and len(rows) == 7)

        # Weekend should NOT be blocked when no assignees are selected.
        sun_allowed = self._allowed_for_label(rows, 'Sun')
        sat_allowed = self._allowed_for_label(rows, 'Sat')
        self.assertIn('12:00 AM', sun_allowed)
        self.assertIn('11:59 PM', sun_allowed)
        self.assertIn('12:00 AM', sat_allowed)
        self.assertIn('11:59 PM', sat_allowed)

    def test_edit_service_team_unassigned_is_full_week(self):
        svc = Service.objects.create(
            organization=self.org,
            name='Svc',
            slug=f'svc-{uuid.uuid4().hex[:6]}',
            description='',
            duration=60,
            price=0,
            buffer_before=0,
            buffer_after=0,
            min_notice_hours=0,
            max_booking_days=30,
            is_active=True,
        )

        url = reverse('calendar_app:edit_service', args=[self.org.slug, svc.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

        rows = resp.context.get('weekly_edit_rows')
        self.assertTrue(isinstance(rows, list) and len(rows) == 7)

        sun_allowed = self._allowed_for_label(rows, 'Sun')
        self.assertIn('12:00 AM', sun_allowed)
        self.assertIn('11:59 PM', sun_allowed)
