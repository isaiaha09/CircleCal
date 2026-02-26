import uuid

from django.test import TestCase
from django.urls import reverse

from accounts.models import Business
from billing.models import Plan, Subscription


class TestEmbedUnavailableMessage(TestCase):
    def setUp(self):
        self.org = Business.objects.create(
            name='Embed Msg Org',
            slug=f'org-{uuid.uuid4().hex[:10]}',
            owner=None,
            timezone='UTC',
            embed_enabled=True,
            embed_key='abc123',
        )

    def _set_subscription(self, *, slug: str, status: str = 'active', stripe_subscription_id=None):
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

    def test_plan_required_message_is_shown(self):
        # Basic plan should fail embed gating with a clear upgrade message.
        self._set_subscription(slug='basic', status='active', stripe_subscription_id=None)
        url = reverse('bookings:public_org_page', args=[self.org.slug])
        r = self.client.get(url + '?embed=1&key=abc123')
        self.assertContains(r, 'Booking widget unavailable')
        self.assertContains(r, 'Embeds require an active Pro or Team subscription.')

    def test_invalid_key_message_is_shown(self):
        # Do not reveal exact entitlement details; message should still be helpful.
        self._set_subscription(slug='pro', status='active', stripe_subscription_id=None)
        url = reverse('bookings:public_org_page', args=[self.org.slug])
        r = self.client.get(url + '?embed=1&key=wrong')
        self.assertContains(r, 'Booking widget unavailable')
        self.assertContains(r, 'This embed link is invalid or has been rotated.')
