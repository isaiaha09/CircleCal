from datetime import datetime, time
import uuid
from unittest.mock import patch
import json

from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from accounts.models import Business, Membership
from bookings.models import Booking, Service, ServiceAssignment, MemberWeeklyAvailability, WeeklyAvailability
from bookings.models import ServiceWeeklyAvailability

from calendar_app.views import (
    _enforce_no_overlap_between_mixed_signature_solo_services,
    _enforce_service_windows_within_member_availability,
    _effective_common_weekly_map_minus_other_services,
    _service_can_be_shown_publicly,
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


class TestPublicVisibilityReadiness(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='owner2', email='owner2@example.com', password='pass')
        self.org = Business.objects.create(name='Org2', slug=f'org-{uuid.uuid4().hex[:8]}', owner=self.user)
        self.mem = Membership.objects.create(user=self.user, organization=self.org, role='owner', is_active=True)

        # Member overall availability: Mon 09:00-17:00
        MemberWeeklyAvailability.objects.create(
            membership=self.mem,
            weekday=0,
            start_time=time(9, 0),
            end_time=time(17, 0),
            is_active=True,
        )

    def _mk_service(self, name, *, inc):
        return Service.objects.create(
            organization=self.org,
            name=name,
            slug=f"{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:8]}",
            description='Test',
            duration=60,
            price=0,
            buffer_before=0,
            buffer_after=0,
            time_increment_minutes=inc,
            use_fixed_increment=True,
            allow_squished_bookings=False,
            allow_ends_after_availability=False,
            min_notice_hours=1,
            max_booking_days=30,
            is_active=True,
        )

    def test_public_readiness_blocks_overlap_when_signatures_differ(self):
        svc_a = self._mk_service('A', inc=30)
        svc_b = self._mk_service('B', inc=60)  # different signature
        ServiceAssignment.objects.create(service=svc_a, membership=self.mem)
        ServiceAssignment.objects.create(service=svc_b, membership=self.mem)

        # Explicit window for svc_a that fits within member availability.
        ServiceWeeklyAvailability.objects.create(
            service=svc_a,
            weekday=0,
            start_time=time(9, 0),
            end_time=time(10, 0),
            is_active=True,
        )

        ok, reason = _service_can_be_shown_publicly(self.org, svc_a)
        self.assertFalse(ok)
        self.assertIn('overlaps another solo service', (reason or '').lower())

    def test_public_readiness_allows_overlap_when_signatures_match(self):
        svc_a = self._mk_service('A', inc=30)
        svc_b = self._mk_service('B', inc=30)  # same signature
        ServiceAssignment.objects.create(service=svc_a, membership=self.mem)
        ServiceAssignment.objects.create(service=svc_b, membership=self.mem)

        ServiceWeeklyAvailability.objects.create(
            service=svc_a,
            weekday=0,
            start_time=time(9, 0),
            end_time=time(10, 0),
            is_active=True,
        )

        ok, reason = _service_can_be_shown_publicly(self.org, svc_a)
        self.assertTrue(ok)
        self.assertEqual(reason, '')


class TestGroupServiceOtherServicesConstraint(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='owner3', email='owner3@example.com', password='pass')
        self.org = Business.objects.create(name='Org3', slug=f'org-{uuid.uuid4().hex[:8]}', owner=self.user)

        self.mem1 = Membership.objects.create(user=self.user, organization=self.org, role='owner', is_active=True)
        self.mem2_user = User.objects.create_user(username='staff1', email='staff1@example.com', password='pass')
        self.mem2 = Membership.objects.create(user=self.mem2_user, organization=self.org, role='staff', is_active=True)

        # Both members: Mon 09:00-17:00
        MemberWeeklyAvailability.objects.create(membership=self.mem1, weekday=0, start_time=time(9, 0), end_time=time(17, 0), is_active=True)
        MemberWeeklyAvailability.objects.create(membership=self.mem2, weekday=0, start_time=time(9, 0), end_time=time(17, 0), is_active=True)

    def _mk_service(self, name, *, inc=30):
        return Service.objects.create(
            organization=self.org,
            name=name,
            slug=f"{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:8]}",
            description='Test',
            duration=60,
            price=0,
            buffer_before=0,
            buffer_after=0,
            time_increment_minutes=inc,
            use_fixed_increment=True,
            allow_squished_bookings=False,
            allow_ends_after_availability=False,
            min_notice_hours=1,
            max_booking_days=30,
            is_active=True,
        )

    def test_group_allowed_map_subtracts_members_other_services(self):
        # Member1 has another service that occupies Mon 09:00-10:00.
        solo = self._mk_service('Solo', inc=30)
        ServiceAssignment.objects.create(service=solo, membership=self.mem1)
        ServiceWeeklyAvailability.objects.create(service=solo, weekday=0, start_time=time(9, 0), end_time=time(10, 0), is_active=True)

        group = self._mk_service('Group', inc=30)
        ServiceAssignment.objects.create(service=group, membership=self.mem1)
        ServiceAssignment.objects.create(service=group, membership=self.mem2)

        allowed = _effective_common_weekly_map_minus_other_services(
            self.org,
            [self.mem1.id, self.mem2.id],
            exclude_service_id=group.id,
        )

        # UI 1 = Monday. Should NOT contain 09:00-10:00 because member1's Solo service blocks it.
        self.assertTrue(isinstance(allowed, list) and len(allowed) == 7)
        mon_ranges = allowed[1] or []
        self.assertTrue(all('09:00-10:00' != r for r in mon_ranges))

    def test_public_readiness_blocks_group_when_overlapping_other_service(self):
        solo = self._mk_service('Solo', inc=30)
        ServiceAssignment.objects.create(service=solo, membership=self.mem1)
        ServiceWeeklyAvailability.objects.create(service=solo, weekday=0, start_time=time(9, 0), end_time=time(10, 0), is_active=True)

        group = self._mk_service('Group', inc=30)
        ServiceAssignment.objects.create(service=group, membership=self.mem1)
        ServiceAssignment.objects.create(service=group, membership=self.mem2)

        # Group tries to use the blocked time.
        ServiceWeeklyAvailability.objects.create(service=group, weekday=0, start_time=time(9, 0), end_time=time(10, 0), is_active=True)

        ok, reason = _service_can_be_shown_publicly(self.org, group)
        self.assertFalse(ok)
        self.assertIn('other services', (reason or '').lower())


class TestGroupServicePerDateOverrideGuardrail(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(username='owner4', email='owner4@example.com', password='pass')
        self.org = Business.objects.create(name='Org4', slug=f'org-{uuid.uuid4().hex[:8]}', owner=self.owner)
        self.mem1 = Membership.objects.create(user=self.owner, organization=self.org, role='owner', is_active=True)

        self.staff_user = User.objects.create_user(username='staff2', email='staff2@example.com', password='pass')
        self.mem2 = Membership.objects.create(user=self.staff_user, organization=self.org, role='staff', is_active=True)

        # Ensure we do NOT trigger legacy "no org weekly rows => fully available" behavior.
        # Create at least one org weekly row (for a different weekday).
        WeeklyAvailability.objects.create(
            organization=self.org,
            weekday=1,  # Tuesday
            start_time=time(9, 0),
            end_time=time(17, 0),
            is_active=True,
        )

        self.group = Service.objects.create(
            organization=self.org,
            name='Group',
            slug=f"group-{uuid.uuid4().hex[:8]}",
            description='Test',
            duration=60,
            price=0,
            buffer_before=0,
            buffer_after=0,
            time_increment_minutes=30,
            use_fixed_increment=True,
            allow_squished_bookings=False,
            allow_ends_after_availability=False,
            min_notice_hours=1,
            max_booking_days=30,
            is_active=True,
        )
        ServiceAssignment.objects.create(service=self.group, membership=self.mem1)
        ServiceAssignment.objects.create(service=self.group, membership=self.mem2)

    def test_service_scoped_availability_override_requires_member_overlap(self):
        # Pick a Monday (weekday 0). With no member weekly rows and org weekly rows present,
        # members are unavailable by default on Monday.
        dobj = datetime(2026, 1, 19).date()
        self.assertEqual(dobj.weekday(), 0)

        url = reverse('bookings:batch_create', kwargs={'org_slug': self.org.slug})
        payload = {
            'dates': [dobj.isoformat()],
            'start_time': '09:00',
            'end_time': '17:00',
            'target': f'svc:{self.group.id}',
            'is_blocking': False,
        }

        self.client.force_login(self.owner)
        with patch('bookings.views._can_use_per_date_overrides', return_value=True):
            resp = self.client.post(url, data=json.dumps(payload), content_type='application/json')

        self.assertEqual(resp.status_code, 403)
        self.assertIn('no overlapping availability', resp.json().get('error', '').lower())

    def test_service_scoped_availability_override_allowed_after_member_overrides(self):
        dobj = datetime(2026, 1, 19).date()
        self.assertEqual(dobj.weekday(), 0)

        org_tz = timezone.get_current_timezone()
        s = timezone.make_aware(datetime(dobj.year, dobj.month, dobj.day, 9, 0, 0), org_tz)
        e = timezone.make_aware(datetime(dobj.year, dobj.month, dobj.day, 17, 0, 0), org_tz)

        # Open member availability for both assignees on that date.
        Booking.objects.create(organization=self.org, service=None, assigned_user=self.owner, is_blocking=False, start=s, end=e, client_name='', client_email='', title='Available')
        Booking.objects.create(organization=self.org, service=None, assigned_user=self.staff_user, is_blocking=False, start=s, end=e, client_name='', client_email='', title='Available')

        url = reverse('bookings:batch_create', kwargs={'org_slug': self.org.slug})
        payload = {
            'dates': [dobj.isoformat()],
            'start_time': '09:00',
            'end_time': '17:00',
            'target': f'svc:{self.group.id}',
            'is_blocking': False,
        }

        self.client.force_login(self.owner)
        with patch('bookings.views._can_use_per_date_overrides', return_value=True):
            resp = self.client.post(url, data=json.dumps(payload), content_type='application/json')

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(
            Booking.objects.filter(organization=self.org, service__isnull=True, client_name=f'scope:svc:{self.group.id}', start__lte=s, end__gte=e).exists()
        )
