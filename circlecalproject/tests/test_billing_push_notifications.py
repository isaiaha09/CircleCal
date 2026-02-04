from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import Business, Membership
from billing.models import Plan, Subscription


User = get_user_model()


class BillingPushNotificationsTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='owner1', email='owner1@example.com', password='pass')
        self.gm = User.objects.create_user(username='gm1', email='gm1@example.com', password='pass')
        self.manager = User.objects.create_user(username='manager1', email='manager1@example.com', password='pass')
        self.staff = User.objects.create_user(username='staff1', email='staff1@example.com', password='pass')

        self.org = Business.objects.create(name='Org', slug='org', owner=self.owner)
        Membership.objects.create(user=self.owner, organization=self.org, role='owner', is_active=True)
        Membership.objects.create(user=self.gm, organization=self.org, role='admin', is_active=True)
        Membership.objects.create(user=self.manager, organization=self.org, role='manager', is_active=True)
        Membership.objects.create(user=self.staff, organization=self.org, role='staff', is_active=True)

        self.plan = Plan.objects.create(name='Team', slug='team', stripe_price_id='price_team', billing_period='monthly')

    def _called_usernames(self, mock_send_push) -> set[str]:
        usernames: set[str] = set()
        for call in mock_send_push.call_args_list:
            user = call.kwargs.get('user')
            if user is not None:
                usernames.add(getattr(user, 'username', str(user)))
        return usernames

    def test_subscription_created_notifies_owner_only(self):
        with patch('accounts.push.send_push_to_user') as mock_send:
            with self.captureOnCommitCallbacks(execute=True):
                Subscription.objects.create(organization=self.org, plan=self.plan, status='trialing', active=True)

        self.assertEqual(self._called_usernames(mock_send), {'owner1'})

    def test_subscription_status_change_notifies_owner_only(self):
        sub = Subscription.objects.create(organization=self.org, plan=self.plan, status='trialing', active=True)

        with patch('accounts.push.send_push_to_user') as mock_send:
            with self.captureOnCommitCallbacks(execute=True):
                sub.status = 'active'
                sub.save()

        self.assertEqual(self._called_usernames(mock_send), {'owner1'})
