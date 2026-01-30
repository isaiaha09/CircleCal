import uuid
from datetime import time

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Business, Membership
from billing.models import Plan, Subscription
from bookings.models import (
    MemberWeeklyAvailability,
    Service,
    ServiceAssignment,
    WeeklyAvailability,
)


class TestTeamServiceAssignmentRequiresAvailability(TestCase):
    def setUp(self):
        User = get_user_model()
        self.client = Client()

        self.owner = User.objects.create_user(
            username='owner_assign',
            email='owner_assign@example.com',
            password='pass',
        )
        self.staff_user = User.objects.create_user(
            username='staff_assign',
            email='staff_assign@example.com',
            password='pass',
        )

        self.org = Business.objects.create(
            name='OrgAssign',
            slug=f'org-{uuid.uuid4().hex[:8]}',
            owner=self.owner,
        )
        Membership.objects.create(user=self.owner, organization=self.org, role='owner', is_active=True)
        self.staff_mem = Membership.objects.create(user=self.staff_user, organization=self.org, role='staff', is_active=True)

        team_plan = Plan.objects.create(name='Team', slug='team', description='Team', price=0, billing_period='monthly')
        Subscription.objects.update_or_create(
            organization=self.org,
            defaults={'plan': team_plan, 'status': 'active', 'active': True},
        )

        self.svc = Service.objects.create(
            organization=self.org,
            name='Svc',
            slug=f'svc-{uuid.uuid4().hex[:6]}',
            description='',
            duration=60,
            price=0,
            buffer_before=0,
            buffer_after=0,
            min_notice_hours=24,
            max_booking_days=30,
            is_active=True,
        )

        self.client.force_login(self.owner)

    def _post_edit(self, assigned_member_ids):
        url = reverse('calendar_app:edit_service', args=[self.org.slug, self.svc.id])
        payload = {
            'name': self.svc.name,
            'slug': self.svc.slug,
            'description': self.svc.description,
            'duration': '60',
            'price': '0',
            'buffer_before': '0',
            'buffer_after': '0',
            'min_notice_hours': '24',
            'max_booking_days': '30',
        }
        # Multi-select style post
        for mid in assigned_member_ids:
            payload.setdefault('assigned_members', [])
            payload['assigned_members'].append(str(mid))
        return self.client.post(url, payload)

    def test_cannot_assign_member_with_no_availability(self):
        resp = self._post_edit([self.staff_mem.id])
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(ServiceAssignment.objects.filter(service=self.svc, membership=self.staff_mem).exists())

    def test_can_assign_member_with_availability(self):
        # Org overall availability (required by MemberWeeklyAvailability validation)
        WeeklyAvailability.objects.create(
            organization=self.org,
            weekday=0,  # Monday (model index)
            start_time=time(9, 0),
            end_time=time(17, 0),
            is_active=True,
        )
        MemberWeeklyAvailability.objects.create(
            membership=self.staff_mem,
            weekday=0,
            start_time=time(9, 0),
            end_time=time(17, 0),
            is_active=True,
        )

        resp = self._post_edit([self.staff_mem.id])
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(ServiceAssignment.objects.filter(service=self.svc, membership=self.staff_mem).exists())
