import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import Business, Membership
from billing.models import Plan, Subscription


class TestHostedSubdomainSettingsAccess(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username=f'u{uuid.uuid4().hex[:8]}', password='pass')
        self.org = Business.objects.create(
            name='Domain Access Org',
            slug=f'org-{uuid.uuid4().hex[:10]}',
            owner=self.user,
            timezone='UTC',
        )
        Membership.objects.create(user=self.user, organization=self.org, role='owner', is_active=True)

        plan = Plan.objects.create(
            name='Basic',
            slug='basic',
            description='Basic',
            price=0,
            billing_period='monthly',
        )
        Subscription.objects.update_or_create(
            organization=self.org,
            defaults={
                'plan': plan,
                'status': 'active',
                'stripe_subscription_id': None,
                'active': True,
                'custom_domain_addon_enabled': False,
            },
        )

        self.client.force_login(self.user)

    @override_settings(HOSTED_SUBDOMAIN_BASE='circlecal.app')
    def test_page_loads_and_shows_purchase_cta_without_custom_domain_addon(self):
        url = reverse('calendar_app:org_custom_domain_settings', kwargs={'org_slug': self.org.slug})
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, f'https://{self.org.slug}.circlecal.app')
        self.assertContains(r, 'Purchase Booking Flow Bundle')

    def test_remove_domain_action_is_disabled_without_custom_domain_addon(self):
        self.org.custom_domain = 'booking.coachalvarez44.com'
        self.org.custom_domain_verified = True
        self.org.custom_domain_verification_token = 'tok123'
        self.org.save(update_fields=['custom_domain', 'custom_domain_verified', 'custom_domain_verification_token'])

        url = reverse('calendar_app:org_custom_domain_settings', kwargs={'org_slug': self.org.slug})
        r = self.client.post(url, {'action': 'remove_domain'}, follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Branded custom-domain management is currently disabled')

        self.org.refresh_from_db()
        self.assertEqual(self.org.custom_domain, 'booking.coachalvarez44.com')
        self.assertTrue(self.org.custom_domain_verified)
        self.assertEqual(self.org.custom_domain_verification_token, 'tok123')
