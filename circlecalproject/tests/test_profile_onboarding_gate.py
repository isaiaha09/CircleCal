from http import HTTPStatus
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test import RequestFactory
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import Business, Membership
from calendar_app.middleware import OrganizationMiddleware


User = get_user_model()


@override_settings(STRIPE_SECRET_KEY='sk_test_profile_onboarding')
class ProfileOnboardingGateTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(
            username='owner_onboarding',
            email='owner_onboarding@example.com',
            password='pass12345',
            first_name='',
            last_name='',
        )
        self.org = Business.objects.create(
            name='Onboarding Org',
            slug='onboarding-org',
            owner=self.user,
            stripe_connect_account_id='acct_pending123',
            stripe_connect_charges_enabled=False,
        )
        Membership.objects.create(user=self.user, organization=self.org, role='owner', is_active=True)
        self.client.force_login(self.user)
        session = self.client.session
        session['cc_active_org_id'] = self.org.id
        session.save()

    def test_owner_without_name_is_forced_back_to_profile(self):
        request = self.factory.get(
            reverse('calendar_app:dashboard', kwargs={'org_slug': self.org.slug}),
            HTTP_HOST='127.0.0.1',
        )
        request.user = self.user
        session_middleware = SessionMiddleware(lambda req: HttpResponse('OK'))
        session_middleware.process_request(request)
        request.session['cc_active_org_id'] = self.org.id
        request.session.save()

        middleware = OrganizationMiddleware(lambda req: HttpResponse('OK'))
        with patch.dict('os.environ', {}, clear=True), patch('sys.argv', ['manage.py']):
            response = middleware(request)

        self.assertEqual(response.status_code, HTTPStatus.FOUND)
        self.assertEqual(response.url, reverse('accounts:profile'))

    def test_profile_request_primes_rls_user_context_before_org_resolution(self):
        request = self.factory.get(reverse('accounts:profile'), HTTP_HOST='127.0.0.1')
        request.user = self.user
        session_middleware = SessionMiddleware(lambda req: HttpResponse('OK'))
        session_middleware.process_request(request)
        request.session['cc_active_org_id'] = self.org.id
        request.session.save()

        seen = []

        def capture_set_config(name, value):
            seen.append((name, value))

        middleware = OrganizationMiddleware(lambda req: HttpResponse('OK'))
        with patch('calendar_app.middleware.connection.vendor', 'postgresql'), patch.object(middleware, '_set_rls_config', side_effect=capture_set_config):
            response = middleware(request)

        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertEqual(getattr(request, 'organization', None), self.org)
        self.assertEqual(seen[:3], [
            ('circlecal.current_user_id', str(self.user.id)),
            ('circlecal.rls_bypass', '0'),
            ('circlecal.current_org_id', ''),
        ])
        self.assertEqual(seen[-3:], [
            ('circlecal.current_user_id', ''),
            ('circlecal.current_org_id', ''),
            ('circlecal.rls_bypass', '0'),
        ])

    def test_successful_profile_save_auto_opens_stripe_modal(self):
        response = self.client.post(
            reverse('accounts:profile'),
            data={
                'email': self.user.email,
                'first_name': 'Owner',
                'last_name': 'User',
                'email_alerts': 'on',
                'booking_reminders': 'on',
                'push_booking_notifications_enabled': 'on',
            },
            HTTP_HOST='127.0.0.1',
        )

        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertContains(response, 'Profile updated successfully.')
        self.assertContains(response, 'Continue to Stripe')
        self.assertContains(response, 'setTimeout(function () {')

    def test_post_login_redirect_sends_owner_without_name_to_profile(self):
        response = self.client.get(reverse('calendar_app:post_login'), HTTP_HOST='127.0.0.1')

        self.assertEqual(response.status_code, HTTPStatus.FOUND)
        self.assertEqual(response.url, reverse('accounts:profile'))