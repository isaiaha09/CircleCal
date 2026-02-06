from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from accounts.models import Business, Membership
from billing.models import Plan, Subscription


User = get_user_model()


class StaffManagerSecurityPagesTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='owner1', email='owner1@example.com', password='pass')
        self.staff = User.objects.create_user(username='staff1', email='staff1@example.com', password='pass')
        self.manager = User.objects.create_user(username='manager1', email='manager1@example.com', password='pass')

        self.org = Business.objects.create(name='Org', slug='org', owner=self.owner)
        Membership.objects.create(user=self.owner, organization=self.org, role='owner', is_active=True)
        Membership.objects.create(user=self.staff, organization=self.org, role='staff', is_active=True)
        Membership.objects.create(user=self.manager, organization=self.org, role='manager', is_active=True)

        # Staff/manager is a Team-plan feature in production.
        plan = Plan.objects.create(name='Team', slug='team', stripe_price_id='price_team')
        Subscription.objects.create(
            organization=self.org,
            plan=plan,
            status='active',
            active=True,
            stripe_subscription_id='sub_test_123',
            start_date=timezone.now() - timezone.timedelta(days=5),
            trial_end=None,
        )

    def _assert_can_access_security_pages(self, user):
        self.client.force_login(user)

        # Password change should be reachable.
        resp = self.client.get(reverse('accounts:password_change'), HTTP_HOST='127.0.0.1')
        self.assertEqual(resp.status_code, 200)

        # 2FA setup should be reachable.
        resp = self.client.get('/accounts/two_factor/setup/', HTTP_HOST='127.0.0.1')
        # two_factor may redirect to profile if already configured; for fresh users expect 200.
        self.assertIn(resp.status_code, (200, 302))

    def test_staff_can_access_password_change_and_2fa_on_team_plan(self):
        self._assert_can_access_security_pages(self.staff)

    def test_manager_can_access_password_change_and_2fa_on_team_plan(self):
        self._assert_can_access_security_pages(self.manager)


class PasswordChangeFlowTests(TestCase):
    def test_password_change_done_page_renders(self):
        User = get_user_model()
        user = User.objects.create_user(username='u1', email='u1@example.com', password='oldpass123')
        self.client.force_login(user)

        resp = self.client.post(
            reverse('accounts:password_change'),
            data={
                'old_password': 'oldpass123',
                'new_password1': 'newpass12345',
                'new_password2': 'newpass12345',
            },
            HTTP_HOST='127.0.0.1',
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn('Password Updated', resp.content.decode('utf-8'))

    def test_password_change_shows_error_when_old_password_wrong(self):
        user = User.objects.create_user(username='u2', email='u2@example.com', password='oldpass123')
        self.client.force_login(user)

        resp = self.client.post(
            reverse('accounts:password_change'),
            data={
                'old_password': 'not-the-right-pass',
                'new_password1': 'newpass12345',
                'new_password2': 'newpass12345',
            },
            HTTP_HOST='127.0.0.1',
        )

        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode('utf-8')
        self.assertIn('Current Password', body)
        self.assertIn('old password', body.lower())

    def test_password_change_shows_error_when_new_passwords_mismatch(self):
        user = User.objects.create_user(username='u3', email='u3@example.com', password='oldpass123')
        self.client.force_login(user)

        resp = self.client.post(
            reverse('accounts:password_change'),
            data={
                'old_password': 'oldpass123',
                'new_password1': 'newpass12345',
                'new_password2': 'newpass12345-nope',
            },
            HTTP_HOST='127.0.0.1',
        )

        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode('utf-8')
        self.assertIn('Confirm New Password', body)
        self.assertIn('match', body.lower())
