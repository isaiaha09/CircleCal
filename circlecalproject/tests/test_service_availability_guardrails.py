from datetime import time
import uuid

from django.test import TestCase
from django.contrib.auth import get_user_model

from accounts.models import Business, Membership
from bookings.models import Service, ServiceAssignment, MemberWeeklyAvailability

from calendar_app.views import (
    _enforce_no_overlap_between_mixed_signature_solo_services,
    _enforce_service_windows_within_member_availability,
)


class TestServiceAvailabilityGuardrails(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='owner', email='owner@example.com', password='pass')
        self.org = Business.objects.create(name='Org', slug=f'org-{uuid.uuid4().hex[:8]}', owner=self.user)
        self.mem = Membership.objects.create(user=self.user, organization=self.org, role='owner', is_active=True)

        # Member overall availability: Mon 09:00-17:00 (model weekday 0 = Monday)
        MemberWeeklyAvailability.objects.create(
            membership=self.mem,
            weekday=0,
            start_time=time(9, 0),
            end_time=time(17, 0),
            is_active=True,
        )

    def _mk_service(
        self,
        name,
        *,
        duration=60,
        inc=30,
        allow_squish=False,
        allow_end_after=False,
        use_fixed=True,
        buffer_after=0,
    ):
        return Service.objects.create(
            organization=self.org,
            name=name,
            slug=f"{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:8]}",
            description='Test',
            duration=duration,
            price=0,
            buffer_before=0,
            buffer_after=buffer_after,
            time_increment_minutes=inc,
            use_fixed_increment=use_fixed,
            allow_squished_bookings=allow_squish,
            allow_ends_after_availability=allow_end_after,
            min_notice_hours=1,
            max_booking_days=30,
            is_active=True,
        )

    def test_blocks_overlap_with_inherited_other_service_when_signatures_differ(self):
        # Two solo services for the same member.
        svc_a = self._mk_service('A', inc=30)
        svc_b = self._mk_service('B', inc=60)  # different signature
        ServiceAssignment.objects.create(service=svc_a, membership=self.mem)
        ServiceAssignment.objects.create(service=svc_b, membership=self.mem)

        # svc_b has NO explicit service-weekly windows -> it inherits member availability.
        proposed = [(0, time(9, 0), time(10, 0))]  # Monday 9-10 overlaps inherited availability

        with self.assertRaises(ValueError):
            _enforce_no_overlap_between_mixed_signature_solo_services(self.org, self.mem.id, svc_a, proposed)

    def test_allows_overlap_when_signatures_match_even_if_other_inherits(self):
        svc_a = self._mk_service('A', inc=30)
        svc_b = self._mk_service('B', inc=30)  # same signature
        ServiceAssignment.objects.create(service=svc_a, membership=self.mem)
        ServiceAssignment.objects.create(service=svc_b, membership=self.mem)

        proposed = [(0, time(9, 0), time(10, 0))]

        # Should not raise
        _enforce_no_overlap_between_mixed_signature_solo_services(self.org, self.mem.id, svc_a, proposed)

    def test_blocks_service_window_outside_member_overall_availability(self):
        svc = self._mk_service('A', inc=30)
        proposed = [(0, time(8, 0), time(9, 0))]  # before member availability

        with self.assertRaises(ValueError):
            _enforce_service_windows_within_member_availability(self.org, self.mem.id, proposed)
