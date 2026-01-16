import uuid

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Business, Membership
from billing.models import Plan, Subscription


class TestCalendarAccessPlanGating(TestCase):
    def setUp(self):
        User = get_user_model()
        self.client = Client()

        self.owner = User.objects.create_user(username='owner', email='owner@example.com', password='pass')
        self.staff = User.objects.create_user(username='staff', email='staff@example.com', password='pass')

        self.org = Business.objects.create(name='Org', slug=f'org-{uuid.uuid4().hex[:8]}', owner=self.owner)
        Membership.objects.create(user=self.owner, organization=self.org, role='owner', is_active=True)
        Membership.objects.create(user=self.staff, organization=self.org, role='staff', is_active=True)

    def _set_plan(self, slug: str, *, status: str = 'active'):
        plan, _ = Plan.objects.get_or_create(
            slug=slug,
            defaults={'name': slug.title(), 'description': slug.title(), 'price': 0, 'billing_period': 'monthly'},
        )
        Subscription.objects.update_or_create(
            organization=self.org,
            defaults={'plan': plan, 'status': status, 'active': (status == 'active')},
        )

    def test_staff_redirected_on_non_team_plans(self):
        # Pro plan (non-team): calendar is owner-only.
        self._set_plan('pro', status='active')

        self.client.force_login(self.staff)
        url = reverse('calendar_app:calendar', args=[self.org.slug])
        resp = self.client.get(url, follow=False, HTTP_HOST='127.0.0.1')

        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse('calendar_app:dashboard', args=[self.org.slug]), resp['Location'])

    def test_staff_allowed_on_team_plan(self):
        self._set_plan('team', status='active')

        self.client.force_login(self.staff)
        url = reverse('calendar_app:calendar', args=[self.org.slug])
        resp = self.client.get(url, HTTP_HOST='127.0.0.1')
        self.assertEqual(resp.status_code, 200)

    def test_owner_always_allowed(self):
        self._set_plan('basic', status='active')

        self.client.force_login(self.owner)
        url = reverse('calendar_app:calendar', args=[self.org.slug])
        resp = self.client.get(url, HTTP_HOST='127.0.0.1')
        self.assertEqual(resp.status_code, 200)
