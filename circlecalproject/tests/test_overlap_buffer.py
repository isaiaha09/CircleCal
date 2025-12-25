from django.test import TestCase, Client
from django.utils import timezone
from datetime import datetime, timedelta, time
from accounts.models import Business, Membership
from bookings.models import Service, Booking
from django.contrib.auth import get_user_model

from bookings.views import _has_overlap

User = get_user_model()


class OverlapBufferTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(username='ov_user', email='ov@example.com')
        self.user.set_password('password')
        self.user.save()
        self.org = Business.objects.create(name='Overlap Org', slug='overlap-org', owner=self.user)
        Membership.objects.update_or_create(user=self.user, organization=self.org, defaults={'role':'owner','is_active':True})
        # Service with buffers: 30 min before, 30 min after (anchors spaced accordingly)
        self.service = Service.objects.create(organization=self.org, name='Buffer Service', slug='buffer-service', duration=60, buffer_before=30, buffer_after=30)

    def test_has_overlap_considers_buffer(self):
        # Existing booking: 09:00 - 10:00 tomorrow (anchor start)
        tomorrow = (timezone.now() + timedelta(days=1)).date()
        start_existing = timezone.make_aware(datetime.combine(tomorrow, time(9, 0)), timezone.get_current_timezone())
        end_existing = timezone.make_aware(datetime.combine(tomorrow, time(10, 0)), timezone.get_current_timezone())
        Booking.objects.create(organization=self.org, start=start_existing, end=end_existing, service=self.service)

        # New booking starting at 11:10 (violates buffer_before=30 -> proposed_start=10:40 overlaps existing)
        new_start = timezone.make_aware(datetime.combine(tomorrow, time(11, 10)), timezone.get_current_timezone())
        new_end = new_start + timedelta(minutes=self.service.duration)

        self.assertFalse(_has_overlap(self.org, new_start, new_end, service=self.service))

        # New booking starting at 11:00 is the next anchor (duration 60 + buffers 30+30 => 120min spacing)
        new_start_ok = timezone.make_aware(datetime.combine(tomorrow, time(11, 0)), timezone.get_current_timezone())
        new_end_ok = new_start_ok + timedelta(minutes=self.service.duration)
        self.assertFalse(_has_overlap(self.org, new_start_ok, new_end_ok, service=self.service))


class CreateBookingBufferIntegrationTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create(username='int_user', email='int@example.com')
        self.user.set_password('password')
        self.user.save()
        self.org = Business.objects.create(name='Int Org', slug='int-org', owner=self.user)
        membership, _ = Membership.objects.update_or_create(user=self.user, organization=self.org, defaults={'role':'owner','is_active':True})
        self.service = Service.objects.create(organization=self.org, name='Buffer Service', slug='buffer-service', duration=60, buffer_before=30, buffer_after=30)
        # Ensure the service is single-assignee so it can inherit org/member weekly availability
        # (unassigned services require explicit service-weekly rows).
        from bookings.models import ServiceAssignment
        ServiceAssignment.objects.get_or_create(service=self.service, membership=membership)
        # Add a weekly availability window so anchors are generated predictably
        from bookings.models import WeeklyAvailability
        from datetime import time
        # Use a wide window covering morning hours so anchors like 09:00/11:00 exist
        WeeklyAvailability.objects.create(organization=self.org, weekday=(timezone.now()+timedelta(days=1)).weekday(), start_time=time(9,0), end_time=time(17,0), is_active=True)
        self.client.force_login(self.user)

    def test_create_booking_blocked_by_buffer(self):
        tomorrow = (timezone.now() + timedelta(days=1)).date()
        existing_start = timezone.make_aware(datetime.combine(tomorrow, time(9, 0)), timezone.get_current_timezone())
        existing_end = timezone.make_aware(datetime.combine(tomorrow, time(10, 0)), timezone.get_current_timezone())
        Booking.objects.create(organization=self.org, start=existing_start, end=existing_end, service=self.service)

        # Attempt to create booking at 11:10 -> should be rejected due to buffer and anchor rules
        new_start = (existing_end + timedelta(minutes=10)).isoformat()
        payload = {
            "service_id": self.service.id,
            "start": new_start,
        }
        import json
        resp = self.client.post(f'/bus/{self.org.slug}/bookings/create/', data=json.dumps(payload), content_type='application/json', HTTP_HOST='127.0.0.1')
        self.assertEqual(resp.status_code, 400)

    def test_create_booking_allowed_outside_buffer(self):
        tomorrow = (timezone.now() + timedelta(days=1)).date()
        # Existing booking earlier in the morning so 11:00 is outside the buffer window
        existing_start = timezone.make_aware(datetime.combine(tomorrow, time(9, 0)), timezone.get_current_timezone())
        existing_end = timezone.make_aware(datetime.combine(tomorrow, time(10, 0)), timezone.get_current_timezone())
        Booking.objects.create(organization=self.org, start=existing_start, end=existing_end, service=self.service)

        # Attempt to create booking at 11:00 -> should be allowed (aligned anchor)
        new_start = timezone.make_aware(datetime.combine(tomorrow, time(11, 0)), timezone.get_current_timezone()).isoformat()
        payload = {
            "service_id": self.service.id,
            "start": new_start,
        }
        import json
        resp = self.client.post(f'/bus/{self.org.slug}/bookings/create/', data=json.dumps(payload), content_type='application/json', HTTP_HOST='127.0.0.1')
        self.assertIn(resp.status_code, (200, 201))
