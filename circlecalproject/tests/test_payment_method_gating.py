import json
import uuid

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Business, Membership
from billing.models import Plan, Subscription
from bookings.models import OrgSettings, Service


class TestPaymentMethodGating(TestCase):
    def setUp(self):
        User = get_user_model()
        self.client = Client()

        self.owner = User.objects.create_user(
            username=f'owner-{uuid.uuid4().hex[:8]}',
            email='owner-pay@example.com',
            password='pass',
        )
        self.org = Business.objects.create(
            name='Pay Org',
            slug=f'org-{uuid.uuid4().hex[:10]}',
            owner=self.owner,
            timezone='UTC',
        )
        Membership.objects.create(user=self.owner, organization=self.org, role='owner', is_active=True)

        # OrgSettings is used by apply_service_update for offline method details.
        OrgSettings.objects.get_or_create(organization=self.org)

        self.service = Service.objects.create(
            organization=self.org,
            name='Paid Lesson',
            slug=f'svc-{uuid.uuid4().hex[:10]}',
            duration=30,
            price=10,
            min_notice_hours=0,
            max_booking_days=60,
            show_on_public_calendar=True,
            is_active=True,
        )

        self.client.force_login(self.owner)

    def _set_plan(self, slug: str, status: str = 'active'):
        plan = Plan.objects.create(name=slug.title(), slug=slug, description=slug.title(), price=0, billing_period='monthly')
        Subscription.objects.update_or_create(
            organization=self.org,
            defaults={'plan': plan, 'status': status, 'active': True},
        )

    def test_paid_service_must_allow_stripe_or_offline(self):
        self._set_plan('pro', status='active')

        url = reverse('calendar_app:apply_service_update', args=[self.org.slug, self.service.id])
        payload = {
            'confirm': True,
            'price': 10,
            'allow_stripe_payments': False,
            'offline_methods': [],
        }
        resp = self.client.post(url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body.get('status'), 'error')
        self.assertIn('Paid services must allow Stripe payments', body.get('error', ''))

    def test_paid_service_venmo_requires_org_venmo_info(self):
        self._set_plan('pro', status='active')

        # Ensure org has no Venmo configured.
        settings = self.org.settings
        settings.offline_venmo = ''
        settings.save(update_fields=['offline_venmo'])

        url = reverse('calendar_app:apply_service_update', args=[self.org.slug, self.service.id])
        payload = {
            'confirm': True,
            'price': 10,
            'allow_stripe_payments': True,
            'offline_methods': ['venmo'],
        }
        resp = self.client.post(url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body.get('status'), 'error')
        self.assertIn('enable Venmo', body.get('error', ''))

    def test_trialing_treated_like_basic_for_offline_methods(self):
        # Even on a Pro plan, trialing disables offline payment methods.
        self._set_plan('pro', status='trialing')

        url = reverse('calendar_app:apply_service_update', args=[self.org.slug, self.service.id])
        payload = {
            'confirm': True,
            'price': 10,
            'allow_stripe_payments': False,
            'offline_methods': ['cash'],
        }
        resp = self.client.post(url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body.get('status'), 'error')
        self.assertIn('Paid services must allow Stripe payments', body.get('error', ''))
