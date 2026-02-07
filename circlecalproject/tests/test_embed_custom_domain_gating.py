import uuid

from django.test import TestCase

from accounts.models import Business
from billing.models import Plan, Subscription
from billing.utils import can_use_custom_domain, can_use_embed_widget


class TestEmbedCustomDomainGating(TestCase):
    def setUp(self):
        self.org = Business.objects.create(
            name='Gate Org',
            slug=f'org-{uuid.uuid4().hex[:10]}',
            owner=None,
            timezone='UTC',
        )

    def _set_subscription(self, *, slug: str, status: str, stripe_subscription_id: str | None):
        plan = Plan.objects.create(
            name=slug.title(),
            slug=slug,
            description=slug.title(),
            price=0,
            billing_period='monthly',
        )
        Subscription.objects.update_or_create(
            organization=self.org,
            defaults={
                'plan': plan,
                'status': status,
                'stripe_subscription_id': stripe_subscription_id,
                'active': True,
            },
        )

    def test_stripe_managed_trialing_is_blocked(self):
        self._set_subscription(slug='team', status='trialing', stripe_subscription_id='sub_123')
        self.assertFalse(can_use_custom_domain(self.org))
        self.assertFalse(can_use_embed_widget(self.org))

    def test_manual_admin_trialing_is_allowed(self):
        # Manual/admin-assigned: no Stripe subscription id.
        self._set_subscription(slug='team', status='trialing', stripe_subscription_id=None)
        self.assertTrue(can_use_custom_domain(self.org))
        self.assertTrue(can_use_embed_widget(self.org))

    def test_basic_is_blocked(self):
        self._set_subscription(slug='basic', status='active', stripe_subscription_id=None)
        self.assertFalse(can_use_custom_domain(self.org))
        self.assertFalse(can_use_embed_widget(self.org))
