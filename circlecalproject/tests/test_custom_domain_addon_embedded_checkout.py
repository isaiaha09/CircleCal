import json
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import Business, Membership
from billing.models import Plan, Subscription


class TestCustomDomainAddonEmbeddedCheckout(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username=f'u{uuid.uuid4().hex[:8]}', password='pass')
        self.org = Business.objects.create(
            name='Embedded Addon Org',
            slug=f'org-{uuid.uuid4().hex[:10]}',
            owner=self.user,
            timezone='UTC',
        )
        Membership.objects.create(user=self.user, organization=self.org, role='owner', is_active=True)

        plan = Plan.objects.create(
            name='Pro',
            slug='pro',
            description='Pro',
            price=0,
            billing_period='monthly',
        )
        Subscription.objects.update_or_create(
            organization=self.org,
            defaults={
                'plan': plan,
                'status': 'active',
                'stripe_subscription_id': 'sub_main_123',
                'active': True,
                'custom_domain_addon_enabled': False,
            },
        )

        self.client.force_login(self.user)

    def test_embedded_checkout_page_renders(self):
        url = reverse('billing:embedded_custom_domain_addon_checkout_page', kwargs={'org_slug': self.org.slug})
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Custom-domain Add-on')

    def test_create_embedded_checkout_session_returns_client_secret(self):
        url = reverse('billing:create_embedded_custom_domain_addon_checkout_session', kwargs={'org_slug': self.org.slug})

        with patch.dict('os.environ', {'STRIPE_PRICE_ID_CUSTOM_DOMAIN_ADDON': 'price_addon_123'}, clear=False):
            with patch('billing.views.stripe.checkout.Session.create', return_value={'client_secret': 'cs_test_123'}):
                r = self.client.post(url, data=json.dumps({}), content_type='application/json')

        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data.get('client_secret'), 'cs_test_123')

    def test_create_embedded_checkout_session_requires_price_config(self):
        url = reverse('billing:create_embedded_custom_domain_addon_checkout_session', kwargs={'org_slug': self.org.slug})

        with patch.dict('os.environ', {'STRIPE_PRICE_ID_CUSTOM_DOMAIN_ADDON': ''}, clear=False):
            r = self.client.post(url, data=json.dumps({}), content_type='application/json')

        self.assertEqual(r.status_code, 400)
        self.assertIn('error', r.json())
