from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import Business, Membership
from accounts.models import Profile
from bookings.models import OrgSettings
from bookings.models import Booking, Service


User = get_user_model()


class BookingPushRecipientsTests(TestCase):
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

        self.service = Service.objects.create(
            organization=self.org,
            name='Svc',
            slug='svc-org',
            duration=60,
        )

    def _called_usernames(self, mock_send_push) -> set[str]:
        usernames: set[str] = set()
        for call in mock_send_push.call_args_list:
            kwargs = call.kwargs
            user = kwargs.get('user')
            if user is not None:
                usernames.add(getattr(user, 'username', str(user)))
        return usernames

    def test_create_booking_notifies_management_and_involved_staff(self):
        start = timezone.now()
        end = start + timedelta(minutes=60)

        with patch('bookings.signals.send_push_to_user') as mock_send:
            with self.captureOnCommitCallbacks(execute=True):
                Booking.objects.create(
                    organization=self.org,
                    service=self.service,
                    title='Test',
                    start=start,
                    end=end,
                    assigned_user=self.staff,
                )

        self.assertEqual(
            self._called_usernames(mock_send),
            {'owner1', 'gm1', 'manager1', 'staff1'},
        )

    def test_create_unassigned_booking_notifies_management(self):
        start = timezone.now()
        end = start + timedelta(minutes=60)

        with patch('bookings.signals.send_push_to_user') as mock_send:
            with self.captureOnCommitCallbacks(execute=True):
                Booking.objects.create(
                    organization=self.org,
                    service=self.service,
                    title='Unassigned',
                    start=start,
                    end=end,
                    assigned_user=None,
                    assigned_team=None,
                )

        self.assertEqual(
            self._called_usernames(mock_send),
            {'owner1', 'gm1', 'manager1'},
        )

    def test_owner_can_disable_owner_receipt_of_booking_push_notifications(self):
        # Disable owner receipt of booking pushes at the org level.
        settings_obj, _ = OrgSettings.objects.get_or_create(organization=self.org)
        settings_obj.owner_receives_staff_booking_push_notifications_enabled = False
        settings_obj.save(update_fields=['owner_receives_staff_booking_push_notifications_enabled'])

        start = timezone.now()
        end = start + timedelta(minutes=60)

        with patch('bookings.signals.send_push_to_user') as mock_send:
            with self.captureOnCommitCallbacks(execute=True):
                Booking.objects.create(
                    organization=self.org,
                    service=self.service,
                    title='Test',
                    start=start,
                    end=end,
                    assigned_user=self.staff,
                )

        # Owner is suppressed; staff still receives; other management still receives.
        self.assertEqual(
            self._called_usernames(mock_send),
            {'gm1', 'manager1', 'staff1'},
        )

    def test_user_can_disable_own_booking_push_notifications(self):
        # Staff opts out of booking pushes; management remains.
        Profile.objects.get_or_create(user=self.staff, defaults={"push_booking_notifications_enabled": False})
        prof = Profile.objects.get(user=self.staff)
        prof.push_booking_notifications_enabled = False
        prof.save(update_fields=["push_booking_notifications_enabled"])

        start = timezone.now()
        end = start + timedelta(minutes=60)

        with patch('bookings.signals.send_push_to_user') as mock_send:
            with self.captureOnCommitCallbacks(execute=True):
                Booking.objects.create(
                    organization=self.org,
                    service=self.service,
                    title='Test',
                    start=start,
                    end=end,
                    assigned_user=self.staff,
                )

        self.assertEqual(
            self._called_usernames(mock_send),
            {'owner1', 'gm1', 'manager1'},
        )

    def test_reschedule_booking_notifies_management_and_involved_staff(self):
        start = timezone.now()
        end = start + timedelta(minutes=60)
        booking = Booking.objects.create(
            organization=self.org,
            service=self.service,
            title='Resched',
            start=start,
            end=end,
            assigned_user=self.staff,
        )

        with patch('bookings.signals.send_push_to_user') as mock_send:
            with self.captureOnCommitCallbacks(execute=True):
                booking.start = booking.start + timedelta(hours=2)
                booking.end = booking.end + timedelta(hours=2)
                booking.save()

        # Staff is involved; management always notified.
        self.assertEqual(
            self._called_usernames(mock_send),
            {'owner1', 'gm1', 'manager1', 'staff1'},
        )

    def test_cancel_booking_notifies_management_and_involved_staff(self):
        start = timezone.now()
        end = start + timedelta(minutes=60)
        booking = Booking.objects.create(
            organization=self.org,
            service=self.service,
            title='CancelMe',
            start=start,
            end=end,
            assigned_user=self.staff,
        )

        with patch('bookings.signals.send_push_to_user') as mock_send:
            with self.captureOnCommitCallbacks(execute=True):
                booking.delete()

        self.assertEqual(
            self._called_usernames(mock_send),
            {'owner1', 'gm1', 'manager1', 'staff1'},
        )
