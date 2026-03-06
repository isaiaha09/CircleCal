import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import Business, Membership
from billing.models import Plan, Subscription


class TestHostedSubdomainLegacyDomainActions(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username=f'u{uuid.uuid4().hex[:8]}', password='pass')
        self.org = Business.objects.create(
            name='Prefix Rules Org',
            slug=f'org-{uuid.uuid4().hex[:10]}',
            owner=self.user,
            timezone='UTC',
        )
        Membership.objects.create(user=self.user, organization=self.org, role='owner', is_active=True)

        # Put org on an eligible plan so the view's plan gate doesn't block us.
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
                'stripe_subscription_id': None,
                'active': True,
                'custom_domain_addon_enabled': True,
            },
        )

        self.client.force_login(self.user)

    def _post_set_domain(self, domain: str):
        url = reverse('calendar_app:org_custom_domain_settings', kwargs={'org_slug': self.org.slug})
        return self.client.post(url, {'action': 'set_domain', 'custom_domain': domain}, follow=True)

    def test_set_domain_with_apex_input_is_disabled(self):
        r = self._post_set_domain('coachalvarez44.com')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Branded custom-domain management is currently disabled')
        self.org.refresh_from_db()
        self.assertIsNone(self.org.custom_domain)

    def test_set_domain_with_unapproved_prefix_input_is_disabled(self):
        r = self._post_set_domain('foo.coachalvarez44.com')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Branded custom-domain management is currently disabled')
        self.org.refresh_from_db()
        self.assertIsNone(self.org.custom_domain)

    def test_set_domain_with_two_label_input_is_disabled(self):
        r = self._post_set_domain('booking.com')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Branded custom-domain management is currently disabled')
        self.org.refresh_from_db()
        self.assertIsNone(self.org.custom_domain)

    def test_set_domain_action_is_disabled_for_legacy_custom_domain_flow(self):
        r = self._post_set_domain('booking.coachalvarez44.com')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Branded custom-domain management is currently disabled')
        self.org.refresh_from_db()
        self.assertIsNone(self.org.custom_domain)

    def test_set_domain_split_fields_action_is_disabled(self):
        url = reverse('calendar_app:org_custom_domain_settings', kwargs={'org_slug': self.org.slug})
        r = self.client.post(
            url,
            {
                'action': 'set_domain',
                'custom_domain_prefix': 'schedule',
                'custom_domain_root': 'coachalvarez44.com',
            },
            follow=True,
        )
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Branded custom-domain management is currently disabled')
        self.org.refresh_from_db()
        self.assertIsNone(self.org.custom_domain)
