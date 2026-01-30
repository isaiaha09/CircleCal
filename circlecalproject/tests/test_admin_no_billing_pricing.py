from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Business, Membership


User = get_user_model()


class AdminNoBillingPricingTests(TestCase):
    def setUp(self):
        self.client = Client()

        self.owner = User.objects.create_user(username='owner1', email='owner1@example.com', password='pw')
        self.admin = User.objects.create_user(username='admin1', email='admin1@example.com', password='pw')

        self.org = Business.objects.create(name='Org', slug='org-1', owner=self.owner)
        Membership.objects.update_or_create(user=self.owner, organization=self.org, defaults={'role': 'owner', 'is_active': True})
        Membership.objects.update_or_create(user=self.admin, organization=self.org, defaults={'role': 'admin', 'is_active': True})

    def test_admin_cannot_open_billing_manage_page(self):
        self.client.force_login(self.admin)
        url = reverse('billing:manage_billing', kwargs={'org_slug': self.org.slug})
        resp = self.client.get(url, HTTP_HOST='127.0.0.1')
        self.assertEqual(resp.status_code, 403)

    def test_admin_is_redirected_away_from_pricing_page(self):
        self.client.force_login(self.admin)
        url = reverse('calendar_app:pricing_page', kwargs={'org_slug': self.org.slug})
        resp = self.client.get(url, HTTP_HOST='127.0.0.1')
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse('calendar_app:dashboard', kwargs={'org_slug': self.org.slug}), resp['Location'])
