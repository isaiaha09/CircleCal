from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings

from accounts.models import Business, Membership
from billing.models import Plan


User = get_user_model()


class ApiBillingSecurityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='billing-api-user', email='billing-api@example.com', password='pw')
        self.org = Business.objects.create(name='Billing API Org', slug='billing-api-org', owner=self.user)
        Membership.objects.create(user=self.user, organization=self.org, role='owner', is_active=True)
        self.plan = Plan.objects.create(
            name='Billing Plan',
            slug='billing-plan-api',
            description='Billing plan',
            price=10,
            billing_period='monthly',
            stripe_price_id='price_api_test',
            is_active=True,
        )
        self.client.force_login(self.user)

    @override_settings(
        STRIPE_SECRET_KEY='sk_test_123',
        REST_FRAMEWORK={'DEFAULT_THROTTLE_RATES': {'billing_write_burst': '1/minute', 'billing_write_sustained': '10/hour'}},
    )
    @patch('circlecalproject.api_billing.stripe.checkout.Session.create')
    @patch('circlecalproject.api_billing.stripe.Customer.create')
    def test_billing_checkout_is_throttled(self, mock_customer_create, mock_session_create):
        cache.clear()

        mock_customer_create.return_value = {'id': 'cus_api_123'}
        mock_session_create.return_value = {'url': 'https://checkout.example.test/session'}

        resp1 = self.client.post(
            f'/api/v1/billing/checkout/?org={self.org.slug}',
            data={'plan_id': self.plan.id},
            content_type='application/json',
        )
        resp2 = self.client.post(
            f'/api/v1/billing/checkout/?org={self.org.slug}',
            data={'plan_id': self.plan.id},
            content_type='application/json',
        )

        self.assertEqual(resp1.status_code, 200)
        self.assertEqual(resp2.status_code, 429)