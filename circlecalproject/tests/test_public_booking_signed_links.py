import uuid
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.core.signing import TimestampSigner
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Business, Membership
from bookings.emails import _build_signed_booking_url
from bookings.models import Booking, Service


@override_settings(
    SITE_URL='https://circlecal.app',
    DEFAULT_FROM_EMAIL='no-reply@circlecal.app',
)
class TestPublicBookingSignedLinks(TestCase):
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
        self.token = TimestampSigner().sign(str(self.booking.id))

    def _mock_postgres_connection(self):
        cursor = MagicMock()
        cursor_cm = MagicMock()
        cursor_cm.__enter__.return_value = cursor
        cursor_cm.__exit__.return_value = False

        conn = MagicMock()
        conn.vendor = 'postgresql'
        conn.cursor.return_value = cursor_cm
        return conn, cursor

    def test_build_signed_booking_url_encodes_token(self):
        url = _build_signed_booking_url('bookings:cancel_booking', self.booking.id, token=self.token)

        parsed = urlparse(url)
        query = parse_qs(parsed.query)

        self.assertEqual(parsed.scheme, 'https')
        self.assertEqual(parsed.netloc, 'circlecal.app')
        self.assertEqual(parsed.path, reverse('bookings:cancel_booking', args=[self.booking.id]))
        self.assertEqual(query['token'], [self.token])
        self.assertIn('%3A', parsed.query)

    def test_cancel_booking_get_uses_signed_public_rls_scope(self):
        conn, cursor = self._mock_postgres_connection()
        with patch('bookings.views.connection', conn):
            response = self.client.get(
                reverse('bookings:cancel_booking', args=[self.booking.id]),
                {'token': self.token},
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Confirm cancellation')
        params = [call.args[1] for call in cursor.execute.call_args_list]
        self.assertIn(['circlecal.rls_bypass', '1'], params)
        self.assertIn(['circlecal.rls_bypass', '0'], params)

    def test_cancel_booking_post_deletes_inside_signed_public_rls_scope(self):
        conn, cursor = self._mock_postgres_connection()
        with patch('bookings.views.connection', conn):
            response = self.client.post(
                f"{reverse('bookings:cancel_booking', args=[self.booking.id])}?token={self.token}",
                {'token': self.token},
            )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Booking.objects.filter(id=self.booking.id).exists())
        bypass_enables = [call.args[1] for call in cursor.execute.call_args_list].count(['circlecal.rls_bypass', '1'])
        self.assertGreaterEqual(bypass_enables, 2)

    def test_reschedule_booking_get_uses_signed_public_rls_scope(self):
        conn, cursor = self._mock_postgres_connection()
        with patch('bookings.views.connection', conn):
            response = self.client.get(
                reverse('bookings:reschedule_booking', args=[self.booking.id]),
                {'token': self.token},
            )

        self.assertEqual(response.status_code, 200)
        params = [call.args[1] for call in cursor.execute.call_args_list]
        self.assertIn(['circlecal.rls_bypass', '1'], params)
        self.assertIn(['circlecal.rls_bypass', '0'], params)