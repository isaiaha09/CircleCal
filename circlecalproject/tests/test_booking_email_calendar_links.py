import uuid
from datetime import datetime, timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import Business, Membership
from bookings.emails import (
    _build_calendar_quick_links,
    send_booking_confirmation,
    send_owner_booking_notification,
)
from bookings.models import Booking, Service


@override_settings(
    SITE_URL='https://circlecal.app',
    DEFAULT_FROM_EMAIL='no-reply@circlecal.app',
)
class TestBookingEmailCalendarLinks(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(
            username=f'owner-{uuid.uuid4().hex[:8]}',
            email='owner@example.com',
            password='pass',
        )
        self.org = Business.objects.create(
            name='CircleCal Training Center',
            slug=f'org-{uuid.uuid4().hex[:10]}',
            owner=self.owner,
            timezone='America/New_York',
        )
        Membership.objects.create(user=self.owner, organization=self.org, role='owner', is_active=True)

        self.service = Service.objects.create(
            organization=self.org,
            name='Pitching Lesson',
            slug=f'svc-{uuid.uuid4().hex[:10]}',
            duration=45,
            price=100,
            buffer_before=0,
            buffer_after=0,
            min_notice_hours=0,
            max_booking_days=60,
            time_increment_minutes=15,
            location_type=Service.LOCATION_TYPE_ADDRESS,
            location_full_address='123 Main St, Miami, FL 33101',
            show_on_public_calendar=True,
            is_active=True,
        )

        start = timezone.make_aware(datetime(2026, 3, 12, 14, 30, 0), timezone.get_fixed_timezone(0))
        end = start + timedelta(minutes=45)
        self.booking = Booking.objects.create(
            organization=self.org,
            service=self.service,
            title=self.service.name,
            start=start,
            end=end,
            client_name='Client Example',
            client_email='client@example.com',
            public_ref='ABC12345',
            payment_method='none',
            payment_status='not_required',
        )

    def test_build_calendar_quick_links_use_expected_provider_routes(self):
        links = _build_calendar_quick_links(self.booking)

        google_url = urlparse(links['google_calendar_url'])
        google_params = parse_qs(google_url.query)
        self.assertEqual(google_url.scheme, 'https')
        self.assertEqual(google_url.netloc, 'calendar.google.com')
        self.assertEqual(google_url.path, '/calendar/render')
        self.assertEqual(google_params['action'], ['TEMPLATE'])
        self.assertEqual(google_params['text'], ['Pitching Lesson'])
        self.assertEqual(google_params['dates'], ['20260312T143000Z/20260312T151500Z'])
        self.assertEqual(google_params['location'], ['123 Main St, Miami, FL 33101'])
        self.assertEqual(google_params['ctz'], ['America/New_York'])
        self.assertIn('Booking at CircleCal Training Center', google_params['details'][0])
        self.assertIn('Location: 123 Main St, Miami, FL 33101', google_params['details'][0])
        self.assertIn('Ref: ABC12345', google_params['details'][0])

        outlook_url = urlparse(links['outlook_web_url'])
        outlook_params = parse_qs(outlook_url.query)
        self.assertEqual(outlook_url.scheme, 'https')
        self.assertEqual(outlook_url.netloc, 'outlook.office.com')
        self.assertEqual(outlook_url.path, '/calendar/0/deeplink/compose')
        self.assertEqual(outlook_params['path'], ['/calendar/action/compose'])
        self.assertEqual(outlook_params['rru'], ['addevent'])
        self.assertEqual(outlook_params['subject'], ['Pitching Lesson'])
        self.assertEqual(outlook_params['startdt'], ['2026-03-12T14:30:00Z'])
        self.assertEqual(outlook_params['enddt'], ['2026-03-12T15:15:00Z'])
        self.assertEqual(outlook_params['location'], ['123 Main St, Miami, FL 33101'])
        self.assertIn('Ref: ABC12345', outlook_params['body'][0])

    def test_confirmation_email_html_includes_calendar_links(self):
        with patch('bookings.emails.EmailMessage') as email_message:
            email_message.return_value.send.return_value = 1

            ok = send_booking_confirmation(self.booking)

        self.assertTrue(ok)
        _, html_content, _, recipients = email_message.call_args.args[:4]
        self.assertEqual(recipients, ['client@example.com'])
        self.assertIn('https://calendar.google.com/calendar/render?', html_content)
        self.assertIn('https://outlook.office.com/calendar/0/deeplink/compose?', html_content)

    def test_owner_notification_email_html_includes_calendar_links(self):
        with patch('bookings.emails._send_html_email') as send_html_email:
            send_html_email.return_value = 1

            send_owner_booking_notification(self.booking)

        _, html_content, recipients = send_html_email.call_args.args[:3]
        self.assertEqual(recipients, ['owner@example.com'])
        self.assertIn('https://calendar.google.com/calendar/render?', html_content)
        self.assertIn('https://outlook.office.com/calendar/0/deeplink/compose?', html_content)
