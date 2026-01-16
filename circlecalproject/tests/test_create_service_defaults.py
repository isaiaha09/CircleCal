import uuid
from datetime import time

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Business, Membership
from billing.models import Plan, Subscription
from bookings.models import WeeklyAvailability


class TestCreateServiceDefaults(TestCase):
    def setUp(self):
        User = get_user_model()
        self.client = Client()

        self.owner = User.objects.create_user(username='owner2', email='owner2@example.com', password='pass')
        self.org = Business.objects.create(name='Org2', slug=f'org-{uuid.uuid4().hex[:8]}', owner=self.owner)
        Membership.objects.create(user=self.owner, organization=self.org, role='owner', is_active=True)

        plan = Plan.objects.create(name='Pro', slug='pro', description='Pro', price=0, billing_period='monthly')
        Subscription.objects.update_or_create(
            organization=self.org,
            defaults={'plan': plan, 'status': 'active', 'active': True},
        )

        # Define overall org availability (Mon-Fri 9-5) so the page has "allowed" ranges
        # available to show as constraints.
        for wd in range(0, 5):
            WeeklyAvailability.objects.create(
                organization=self.org,
                weekday=wd,
                start_time=time(9, 0),
                end_time=time(17, 0),
                is_active=True,
            )

        self.client.force_login(self.owner)

    def test_create_service_get_starts_with_blank_service_availability(self):
        url = reverse('calendar_app:create_service', args=[self.org.slug])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

        weekly_edit_rows = resp.context.get('weekly_edit_rows')
        self.assertTrue(isinstance(weekly_edit_rows, list) and len(weekly_edit_rows) == 7)

        # New services should not inherit/auto-populate a weekly schedule on the create form.
        # The constraints/allowed ranges are shown separately.
        for row in weekly_edit_rows:
            self.assertEqual((row.get('svc_ranges') or '').strip(), '')
