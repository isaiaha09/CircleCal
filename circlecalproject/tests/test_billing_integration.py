from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from accounts.models import Business, Membership
from billing.models import Plan
from unittest.mock import patch, MagicMock
from django.utils import timezone

User = get_user_model()


class BillingIntegrationTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.username = 'billing_user'
        self.password = 'password123'
        self.user, created = User.objects.get_or_create(username=self.username, defaults={'email': 'billing@example.com'})
        if created:
            self.user.set_password(self.password)
            self.user.save()

        self.org = Business.objects.create(name='Billing Org', slug='billing-org', owner=self.user)
        Membership.objects.update_or_create(user=self.user, organization=self.org, defaults={'role':'owner','is_active':True})
        self.plan = Plan.objects.create(name='Test Plan', stripe_price_id='price_test')
        self.client.force_login(self.user)

    def test_billing_portal_returns_400_if_no_customer(self):
        # org.stripe_customer_id is None by default
        url = reverse('billing:billing_portal', kwargs={'org_slug': self.org.slug})
        resp = self.client.get(url, HTTP_HOST='127.0.0.1')
        self.assertEqual(resp.status_code, 400)

    @patch('billing.views.stripe.checkout.Session.create')
    @patch('billing.views.stripe.Customer.create')
    def test_create_checkout_session_creates_customer_and_redirects(self, mock_customer_create, mock_session_create):
        # simulate Stripe Customer.create and Checkout Session.create
        mock_customer = MagicMock()
        mock_customer.id = 'cus_test_123'
        mock_customer_create.return_value = mock_customer

        mock_session = MagicMock()
        mock_session.url = 'https://checkout.test/session/abc'
        mock_session_create.return_value = mock_session

        url = reverse('billing:create_checkout_session', kwargs={'org_slug': self.org.slug, 'plan_id': self.plan.id})
        resp = self.client.get(url, HTTP_HOST='127.0.0.1')

        # view should redirect to the mocked session URL
        self.assertIn(resp.status_code, (302, 301))
        self.assertEqual(resp['Location'], mock_session.url)

        # organization should have stripe_customer_id saved
        self.org.refresh_from_db()
        self.assertEqual(self.org.stripe_customer_id, mock_customer.id)
