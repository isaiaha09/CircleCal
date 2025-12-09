from django.test import TestCase, Client
from django.utils import timezone
from datetime import datetime, timedelta, time
from accounts.models import Business, Membership
from bookings.models import Service, Booking
from django.contrib.auth import get_user_model

User = get_user_model()


class CandidateBufferRejectionTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create(username='cb_user', email='cb@example.com')
        self.user.set_password('password')
        self.user.save()
        self.org = Business.objects.create(name='CB Org', slug='cb-org', owner=self.user)
        Membership.objects.update_or_create(user=self.user, organization=self.org, defaults={'role':'owner','is_active':True})
        # Service with 60min duration and 5min post-buffer
        self.service = Service.objects.create(organization=self.org, name='Short Buffer', slug='short-buffer', duration=60, buffer_after=5)
        # Wide weekly window so anchors exist
        from bookings.models import WeeklyAvailability
        WeeklyAvailability.objects.create(organization=self.org, weekday=(timezone.now()+timedelta(days=1)).weekday(), start_time=time(8,0), end_time=time(18,0), is_active=True)
        self.client.force_login(self.user)

    def test_candidate_rejected_when_post_buffer_collides(self):
        tomorrow = (timezone.now() + timedelta(days=1)).date()
        # Existing bookings: 09:00-10:00 and 11:05-12:05
        start1 = timezone.make_aware(datetime.combine(tomorrow, time(9,0)), timezone.get_current_timezone())
        end1 = start1 + timedelta(minutes=60)
        Booking.objects.create(organization=self.org, start=start1, end=end1, service=self.service)

        start2 = timezone.make_aware(datetime.combine(tomorrow, time(11,5)), timezone.get_current_timezone())
        end2 = start2 + timedelta(minutes=60)
        Booking.objects.create(organization=self.org, start=start2, end=end2, service=self.service)

        # Candidate: 10:05-11:05. Candidate end + buffer = 11:10 which overlaps 11:05 booking
        candidate_start = timezone.make_aware(datetime.combine(tomorrow, time(10,5)), timezone.get_current_timezone())
        candidate_end = candidate_start + timedelta(minutes=60)

        # Import the overlap checker
        from bookings.views import _has_overlap
        self.assertTrue(_has_overlap(self.org, candidate_start, candidate_end, service=self.service))

        # Also assert the create endpoint rejects it
        payload = {
            'service_id': self.service.id,
            'start': candidate_start.isoformat(),
        }
        import json
        resp = self.client.post(f'/bus/{self.org.slug}/bookings/create/', data=json.dumps(payload), content_type='application/json', HTTP_HOST='127.0.0.1')
        self.assertEqual(resp.status_code, 400)
