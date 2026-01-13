import json
import uuid
from datetime import time

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from accounts.models import Business, Membership
from billing.models import Plan, Subscription
from bookings.models import Booking, MemberWeeklyAvailability, Service, ServiceAssignment, ServiceWeeklyAvailability


class TestMemberOverridesAffectGroupServices(TestCase):
    def setUp(self):
        User = get_user_model()
        self.client = Client()

        self.owner = User.objects.create_user(username='owner', email='owner@example.com', password='pass')
        self.org = Business.objects.create(name='Org', slug=f'org-{uuid.uuid4().hex[:8]}', owner=self.owner)
        self.owner_mem = Membership.objects.create(user=self.owner, organization=self.org, role='owner', is_active=True)

        # Team plan so per-date overrides are enabled
        plan = Plan.objects.create(name='Team', slug='team', description='Team', price=0, billing_period='monthly')
        Subscription.objects.update_or_create(
            organization=self.org,
            defaults={'plan': plan, 'status': 'active', 'active': True},
        )

        # Two staff members
        self.u1 = User.objects.create_user(username='m1', email='m1@example.com', password='pass')
        self.u2 = User.objects.create_user(username='m2', email='m2@example.com', password='pass')
        self.m1 = Membership.objects.create(user=self.u1, organization=self.org, role='staff', is_active=True)
        self.m2 = Membership.objects.create(user=self.u2, organization=self.org, role='staff', is_active=True)

        # Weekly availability for members (Monday 09:00-17:00)
        MemberWeeklyAvailability.objects.create(membership=self.m1, weekday=0, start_time=time(9, 0), end_time=time(17, 0), is_active=True)
        MemberWeeklyAvailability.objects.create(membership=self.m2, weekday=0, start_time=time(9, 0), end_time=time(17, 0), is_active=True)

        # Group service with explicit weekly windows (Monday 09:00-17:00)
        self.group_svc = Service.objects.create(
            organization=self.org,
            name='Group',
            slug=f'group-{uuid.uuid4().hex[:6]}',
            duration=60,
            max_booking_days=5000,
        )
        ServiceAssignment.objects.create(service=self.group_svc, membership=self.m1)
        ServiceAssignment.objects.create(service=self.group_svc, membership=self.m2)
        ServiceWeeklyAvailability.objects.create(service=self.group_svc, weekday=0, start_time=time(9, 0), end_time=time(17, 0), is_active=True)

        self.client.force_login(self.owner)

    def _block_member_full_day(self, membership_id: int, date_str: str):
        payload = {
            'dates': [date_str],
            'start_time': '00:00',
            'end_time': '23:59',
            'is_blocking': True,
            'target': str(membership_id),
        }
        return self.client.post(
            f'/bus/{self.org.slug}/bookings/batch_create/',
            data=json.dumps(payload),
            content_type='application/json',
            HTTP_HOST='127.0.0.1',
        )

    def test_group_service_unavailable_if_any_member_blocked(self):
        # Monday
        date_str = '2030-01-07'
        resp = self._block_member_full_day(self.m1.id, date_str)
        self.assertEqual(resp.status_code, 200)

        # Group service availability should be empty because assigned members must overlap
        # (client expects all coaches/resources to be available).
        avail = self.client.get(
            f'/bus/{self.org.slug}/services/{self.group_svc.slug}/availability/?start={date_str}T00:00:00&end={date_str}T23:59:59',
            HTTP_HOST='127.0.0.1',
        )
        self.assertEqual(avail.status_code, 200)
        data = json.loads(avail.content.decode('utf-8'))
        self.assertEqual(data, [])

    def test_group_service_unavailable_if_all_members_blocked(self):
        date_str = '2030-01-07'
        resp1 = self._block_member_full_day(self.m1.id, date_str)
        resp2 = self._block_member_full_day(self.m2.id, date_str)
        self.assertEqual(resp1.status_code, 200)
        self.assertEqual(resp2.status_code, 200)

        avail = self.client.get(
            f'/bus/{self.org.slug}/services/{self.group_svc.slug}/availability/?start={date_str}T00:00:00&end={date_str}T23:59:59',
            HTTP_HOST='127.0.0.1',
        )
        self.assertEqual(avail.status_code, 200)
        data = json.loads(avail.content.decode('utf-8'))
        self.assertEqual(data, [])


class TestMemberBlockGuardrailForSoloBookings(TestCase):
    def setUp(self):
        User = get_user_model()
        self.client = Client()

        self.owner = User.objects.create_user(username='owner', email='owner@example.com', password='pass')
        self.org = Business.objects.create(name='Org', slug=f'org-{uuid.uuid4().hex[:8]}', owner=self.owner)
        self.owner_mem = Membership.objects.create(user=self.owner, organization=self.org, role='owner', is_active=True)

        plan = Plan.objects.create(name='Team', slug='team', description='Team', price=0, billing_period='monthly')
        Subscription.objects.update_or_create(
            organization=self.org,
            defaults={'plan': plan, 'status': 'active', 'active': True},
        )

        self.u1 = User.objects.create_user(username='m1', email='m1@example.com', password='pass')
        self.m1 = Membership.objects.create(user=self.u1, organization=self.org, role='staff', is_active=True)

        self.solo_svc = Service.objects.create(
            organization=self.org,
            name='Solo',
            slug=f'solo-{uuid.uuid4().hex[:6]}',
            duration=60,
            max_booking_days=5000,
        )
        ServiceAssignment.objects.create(service=self.solo_svc, membership=self.m1)

        self.client.force_login(self.owner)

    def test_cannot_block_member_for_day_if_solo_booking_exists(self):
        # Create a real booking on that date for the solo service
        from zoneinfo import ZoneInfo
        from django.conf import settings
        from django.utils import timezone
        from datetime import datetime, timedelta

        tz = ZoneInfo(getattr(self.org, 'timezone', getattr(settings, 'TIME_ZONE', 'UTC')))
        day = datetime(2030, 1, 7, 10, 0, 0, tzinfo=tz)
        Booking.objects.create(
            organization=self.org,
            service=self.solo_svc,
            title='Booked',
            start=day,
            end=day + timedelta(minutes=60),
            is_blocking=False,
        )

        payload = {
            'dates': ['2030-01-07'],
            'start_time': '00:00',
            'end_time': '23:59',
            'is_blocking': True,
            'target': str(self.m1.id),
        }
        resp = self.client.post(
            f'/bus/{self.org.slug}/bookings/batch_create/',
            data=json.dumps(payload),
            content_type='application/json',
            HTTP_HOST='127.0.0.1',
        )
        self.assertEqual(resp.status_code, 400)

    def test_cannot_block_member_for_day_if_assigned_group_booking_exists(self):
        # Create a group service and a booking assigned to this member on that date.
        from zoneinfo import ZoneInfo
        from django.conf import settings
        from datetime import datetime, timedelta

        u2 = get_user_model().objects.create_user(username='m2', email='m2@example.com', password='pass')
        m2 = Membership.objects.create(user=u2, organization=self.org, role='staff', is_active=True)

        group_svc = Service.objects.create(
            organization=self.org,
            name='Group',
            slug=f'group-{uuid.uuid4().hex[:6]}',
            duration=60,
            max_booking_days=5000,
        )
        ServiceAssignment.objects.create(service=group_svc, membership=self.m1)
        ServiceAssignment.objects.create(service=group_svc, membership=m2)

        tz = ZoneInfo(getattr(self.org, 'timezone', getattr(settings, 'TIME_ZONE', 'UTC')))
        day = datetime(2030, 1, 7, 10, 0, 0, tzinfo=tz)
        Booking.objects.create(
            organization=self.org,
            service=group_svc,
            title='Group booked',
            start=day,
            end=day + timedelta(minutes=60),
            is_blocking=False,
            assigned_user=self.m1.user,
        )

        payload = {
            'dates': ['2030-01-07'],
            'start_time': '00:00',
            'end_time': '23:59',
            'is_blocking': True,
            'target': str(self.m1.id),
        }
        resp = self.client.post(
            f'/bus/{self.org.slug}/bookings/batch_create/',
            data=json.dumps(payload),
            content_type='application/json',
            HTTP_HOST='127.0.0.1',
        )
        self.assertEqual(resp.status_code, 400)


class TestServiceBlockGuardrailForBookings(TestCase):
    def setUp(self):
        User = get_user_model()
        self.client = Client()

        self.owner = User.objects.create_user(username='owner', email='owner@example.com', password='pass')
        self.org = Business.objects.create(name='Org', slug=f'org-{uuid.uuid4().hex[:8]}', owner=self.owner)
        Membership.objects.create(user=self.owner, organization=self.org, role='owner', is_active=True)

        plan = Plan.objects.create(name='Team', slug='team', description='Team', price=0, billing_period='monthly')
        Subscription.objects.update_or_create(
            organization=self.org,
            defaults={'plan': plan, 'status': 'active', 'active': True},
        )

        self.client.force_login(self.owner)

    def test_cannot_block_service_day_if_booking_exists_unassigned_service(self):
        from zoneinfo import ZoneInfo
        from django.conf import settings
        from datetime import datetime, timedelta

        svc = Service.objects.create(
            organization=self.org,
            name='No-assignees',
            slug=f'noasg-{uuid.uuid4().hex[:6]}',
            duration=60,
            max_booking_days=5000,
        )

        tz = ZoneInfo(getattr(self.org, 'timezone', getattr(settings, 'TIME_ZONE', 'UTC')))
        day = datetime(2030, 1, 7, 10, 0, 0, tzinfo=tz)
        Booking.objects.create(
            organization=self.org,
            service=svc,
            title='Booked',
            start=day,
            end=day + timedelta(minutes=60),
            is_blocking=False,
        )

        payload = {
            'dates': ['2030-01-07'],
            'start_time': '00:00',
            'end_time': '23:59',
            'is_blocking': True,
            'target': f'svc:{svc.id}',
        }
        resp = self.client.post(
            f'/bus/{self.org.slug}/bookings/batch_create/',
            data=json.dumps(payload),
            content_type='application/json',
            HTTP_HOST='127.0.0.1',
        )
        self.assertEqual(resp.status_code, 400)

    def test_cannot_block_service_day_if_booking_exists_assigned_service(self):
        from zoneinfo import ZoneInfo
        from django.conf import settings
        from datetime import datetime, timedelta

        u1 = get_user_model().objects.create_user(username='m1', email='m1@example.com', password='pass')
        m1 = Membership.objects.create(user=u1, organization=self.org, role='staff', is_active=True)

        svc = Service.objects.create(
            organization=self.org,
            name='Assigned',
            slug=f'asg-{uuid.uuid4().hex[:6]}',
            duration=60,
            max_booking_days=5000,
        )
        ServiceAssignment.objects.create(service=svc, membership=m1)

        tz = ZoneInfo(getattr(self.org, 'timezone', getattr(settings, 'TIME_ZONE', 'UTC')))
        day = datetime(2030, 1, 7, 10, 0, 0, tzinfo=tz)
        Booking.objects.create(
            organization=self.org,
            service=svc,
            title='Booked',
            start=day,
            end=day + timedelta(minutes=60),
            is_blocking=False,
            assigned_user=u1,
        )

        payload = {
            'dates': ['2030-01-07'],
            'start_time': '00:00',
            'end_time': '23:59',
            'is_blocking': True,
            'target': f'svc:{svc.id}',
        }
        resp = self.client.post(
            f'/bus/{self.org.slug}/bookings/batch_create/',
            data=json.dumps(payload),
            content_type='application/json',
            HTTP_HOST='127.0.0.1',
        )
        self.assertEqual(resp.status_code, 400)
