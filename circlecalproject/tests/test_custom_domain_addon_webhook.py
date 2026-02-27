import json
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from accounts.models import Membership

from accounts.models import Business
from billing.models import Plan, Subscription


class TestCustomDomainAddonWebhook(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username=f'u{uuid.uuid4().hex[:8]}', password='pass')
        self.org = Business.objects.create(
            name='Addon Org',
            slug=f'org-{uuid.uuid4().hex[:10]}',
            owner=self.user,
            timezone='UTC',
        )
        self.plan = Plan.objects.create(
            name='Pro',
            slug='pro',
            description='Pro',
            price=0,
            billing_period='monthly',
        )
        self.sub = Subscription.objects.create(
            organization=self.org,
            plan=self.plan,
            status='active',
            active=True,
            stripe_subscription_id='sub_main_123',
            custom_domain_addon_enabled=False,
        )
        Membership.objects.create(user=self.user, organization=self.org, role='owner', is_active=True)
        self.org.stripe_customer_id = 'cus_test_123'
        self.org.save(update_fields=['stripe_customer_id'])
        self.client.force_login(self.user)

    def _post_webhook(self, event_obj: dict):
        url = reverse('billing:stripe_webhook')
        with patch('billing.views.stripe.Webhook.construct_event', return_value=event_obj):
            return self.client.post(
                url,
                data=json.dumps({'ok': True}),
                content_type='application/json',
                HTTP_STRIPE_SIGNATURE='t=1,v1=test',
            )

    def test_checkout_session_completed_enables_custom_domain_addon(self):
        event = {
            'type': 'checkout.session.completed',
            'data': {
                'object': {
                    'metadata': {
                        'organization_id': str(self.org.id),
                        'purchase_type': 'custom_domain_addon',
                    },
                    'subscription': 'sub_addon_001',
                }
            },
        }

        r = self._post_webhook(event)
        self.assertEqual(r.status_code, 200)

        self.sub.refresh_from_db()
        self.assertTrue(self.sub.custom_domain_addon_enabled)
        self.assertEqual(self.sub.stripe_subscription_id, 'sub_main_123')

    def test_subscription_created_addon_does_not_override_main_subscription_id(self):
        event = {
            'type': 'customer.subscription.created',
            'data': {
                'object': {
                    'id': 'sub_addon_002',
                    'metadata': {
                        'organization_id': str(self.org.id),
                        'purchase_type': 'custom_domain_addon',
                    },
                    'customer': None,
                    'status': 'active',
                }
            },
        }

        r = self._post_webhook(event)
        self.assertEqual(r.status_code, 200)

        self.sub.refresh_from_db()
        self.assertTrue(self.sub.custom_domain_addon_enabled)
        self.assertEqual(self.sub.stripe_subscription_id, 'sub_main_123')

    @patch('billing.views.stripe.Subscription.list')
    def test_sync_endpoint_enables_addon_when_active_addon_subscription_exists(self, mock_list):
        mock_list.return_value = {
            'data': [
                {
                    'id': 'sub_addon_live',
                    'status': 'active',
                    'metadata': {
                        'purchase_type': 'custom_domain_addon',
                        'organization_id': str(self.org.id),
                    },
                }
            ]
        }

        url = reverse('billing:sync_custom_domain_addon_status', kwargs={'org_slug': self.org.slug})
        r = self.client.post(url)
        self.assertEqual(r.status_code, 200)

        self.sub.refresh_from_db()
        self.assertTrue(self.sub.custom_domain_addon_enabled)

    @patch('billing.views.stripe.Subscription.list')
    def test_sync_endpoint_disables_addon_when_no_active_addon_subscription(self, mock_list):
        self.sub.custom_domain_addon_enabled = True
        self.sub.save(update_fields=['custom_domain_addon_enabled'])

        mock_list.return_value = {
            'data': [
                {
                    'id': 'sub_addon_old',
                    'status': 'canceled',
                    'metadata': {
                        'purchase_type': 'custom_domain_addon',
                        'organization_id': str(self.org.id),
                    },
                }
            ]
        }

        url = reverse('billing:sync_custom_domain_addon_status', kwargs={'org_slug': self.org.slug})
        r = self.client.post(url)
        self.assertEqual(r.status_code, 200)

        self.sub.refresh_from_db()
        self.assertFalse(self.sub.custom_domain_addon_enabled)
