import json
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.middleware.csrf import _get_new_csrf_string
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Business, Membership
from billing.models import Plan, Subscription


User = get_user_model()


class CsrfProtectedMutationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='csrf-owner', email='csrf@example.com', password='pw')
        self.org = Business.objects.create(name='CSRF Org', slug='csrf-org', owner=self.user)
        Membership.objects.create(user=self.user, organization=self.org, role='owner', is_active=True)
        self.plan = Plan.objects.create(name='Pro', slug='pro', description='Pro', price=10, billing_period='monthly', stripe_price_id='price_csrf')
        Subscription.objects.update_or_create(
            organization=self.org,
            defaults={'plan': self.plan, 'status': 'active', 'active': True},
        )

    def _csrf_client(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        token = _get_new_csrf_string()
        client.cookies[settings.CSRF_COOKIE_NAME] = token
        return client, token

    @patch('billing.views.stripe.Subscription.create')
    @patch('billing.views.stripe.Customer.create')
    def test_create_embedded_subscription_requires_csrf(self, mock_customer_create, mock_subscription_create):
        mock_customer = MagicMock()
        mock_customer.id = 'cus_csrf_123'
        mock_customer_create.return_value = mock_customer
        mock_subscription_create.return_value = {
            'id': 'sub_csrf_123',
            'latest_invoice': {
                'id': 'in_csrf_123',
                'payments': {
                    'data': [
                        {
                            'payment': {
                                'payment_intent': {
                                    'id': 'pi_csrf_123',
                                    'client_secret': 'pi_csrf_secret_123',
                                }
                            }
                        }
                    ]
                }
            }
        }

        client, token = self._csrf_client()
        url = reverse('billing:create_embedded_subscription', kwargs={'org_slug': self.org.slug, 'plan_id': self.plan.id})

        blocked = client.post(url, HTTP_HOST='127.0.0.1')
        self.assertEqual(blocked.status_code, 403)

        allowed = client.post(url, HTTP_HOST='127.0.0.1', HTTP_X_CSRFTOKEN=token)
        self.assertEqual(allowed.status_code, 200)

    def test_batch_create_requires_csrf(self):
        client, token = self._csrf_client()
        url = reverse('bookings:batch_create', kwargs={'org_slug': self.org.slug})

        blocked = client.post(url, data=json.dumps({}), content_type='application/json')
        self.assertEqual(blocked.status_code, 403)

        allowed = client.post(url, data=json.dumps({}), content_type='application/json', HTTP_X_CSRFTOKEN=token)
        self.assertEqual(allowed.status_code, 400)