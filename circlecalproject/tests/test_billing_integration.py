from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from accounts.models import Business, Membership
from billing.models import Plan
from billing.models import DiscountCode
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

    @patch('billing.views.stripe.checkout.Session.create')
    @patch('billing.views.stripe.Customer.create')
    def test_create_checkout_session_applies_user_discount_code_to_stripe(self, mock_customer_create, mock_session_create):
        mock_customer = MagicMock()
        mock_customer.id = 'cus_test_123'
        mock_customer_create.return_value = mock_customer

        mock_session = MagicMock()
        mock_session.url = 'https://checkout.test/session/with-discount'
        mock_session_create.return_value = mock_session

        dc = DiscountCode.objects.create(
            code='ADMIN-ASSIGNED',
            description='Test discount',
            percent_off=10,
            active=True,
            start_date=timezone.now() - timezone.timedelta(days=1),
            expires_at=timezone.now() + timezone.timedelta(days=30),
            stripe_coupon_id='coupon_test_10off',
        )
        dc.users.add(self.user)

        url = reverse('billing:create_checkout_session', kwargs={'org_slug': self.org.slug, 'plan_id': self.plan.id})
        resp = self.client.get(url, HTTP_HOST='127.0.0.1')

        self.assertIn(resp.status_code, (302, 301))
        self.assertEqual(resp['Location'], mock_session.url)

        # Stripe checkout session should include the discount.
        _, kwargs = mock_session_create.call_args
        self.assertIn('discounts', kwargs)
        self.assertEqual(kwargs['discounts'], [{'coupon': 'coupon_test_10off'}])

    @patch('billing.views.stripe.Subscription.create')
    @patch('billing.views.stripe.Customer.create')
    def test_create_embedded_subscription_applies_user_discount_code_to_stripe(self, mock_customer_create, mock_subscription_create):
        mock_customer = MagicMock()
        mock_customer.id = 'cus_test_123'
        mock_customer_create.return_value = mock_customer

        # Return a shape that matches what create_embedded_subscription expects
        mock_subscription_create.return_value = {
            'id': 'sub_test_123',
            'latest_invoice': {
                'id': 'in_test_123',
                'payments': {
                    'data': [
                        {
                            'payment': {
                                'payment_intent': {
                                    'id': 'pi_test_123',
                                    'client_secret': 'pi_test_secret_123',
                                }
                            }
                        }
                    ]
                }
            }
        }

        dc = DiscountCode.objects.create(
            code='ADMIN-ASSIGNED',
            description='Test discount',
            percent_off=10,
            active=True,
            start_date=timezone.now() - timezone.timedelta(days=1),
            expires_at=timezone.now() + timezone.timedelta(days=30),
            stripe_coupon_id='coupon_test_10off',
        )
        dc.users.add(self.user)

        url = reverse('billing:create_embedded_subscription', kwargs={'org_slug': self.org.slug, 'plan_id': self.plan.id})
        resp = self.client.post(url, HTTP_HOST='127.0.0.1')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data.get('subscription_id'), 'sub_test_123')
        self.assertEqual(data.get('client_secret'), 'pi_test_secret_123')

        # Stripe Subscription.create should include the discount.
        _, kwargs = mock_subscription_create.call_args
        self.assertIn('discounts', kwargs)
        self.assertEqual(kwargs['discounts'], [{'coupon': 'coupon_test_10off'}])
