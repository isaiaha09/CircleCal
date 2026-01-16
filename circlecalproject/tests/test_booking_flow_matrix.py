import json
import uuid
from datetime import datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone

from accounts.models import Business, Membership
from billing.models import Plan, Subscription
from bookings.models import (
    Booking,
    FacilityResource,
    MemberWeeklyAvailability,
    Service,
    ServiceAssignment,
    ServiceResource,
    ServiceWeeklyAvailability,
    WeeklyAvailability,
)


class TestCreateBookingFlowMatrix(TestCase):
    def setUp(self):
        User = get_user_model()
        self.client = Client()

        self.owner = User.objects.create_user(username='owner', email='owner@example.com', password='pass')
        self.org = Business.objects.create(name='Org', slug=f'org-{uuid.uuid4().hex[:8]}', owner=self.owner)
        self.mem = Membership.objects.create(user=self.owner, organization=self.org, role='owner', is_active=True)

        # Team plan enables facility resources + keeps feature gates simple.
        plan = Plan.objects.create(name='Team', slug='team', description='Team', price=0, billing_period='monthly')
        Subscription.objects.update_or_create(
            organization=self.org,
            defaults={'plan': plan, 'status': 'active', 'active': True},
        )

        # Wide weekly availability across all days so our tests don't depend on weekday.
        for wd in range(7):
            WeeklyAvailability.objects.create(
                organization=self.org,
                weekday=wd,
                start_time=time(0, 0),
                end_time=time(23, 59),
                is_active=True,
            )
            MemberWeeklyAvailability.objects.create(
                membership=self.mem,
                weekday=wd,
                start_time=time(0, 0),
                end_time=time(23, 59),
                is_active=True,
            )

        self.client.force_login(self.owner)

    def _mk_aware(self, dt: datetime):
        tz = timezone.get_current_timezone()
        return timezone.make_aware(dt, tz) if timezone.is_naive(dt) else dt

    def _mk_start(self, *, days_ahead: int, hh: int, mm: int = 0):
        tz = timezone.get_current_timezone()
        d = (timezone.now().astimezone(tz).date() + timedelta(days=int(days_ahead)))
        return timezone.make_aware(datetime(d.year, d.month, d.day, hh, mm, 0), tz)

    def _post_create(self, *, service: Service, start_dt, extra: dict | None = None):
        payload = {'service_id': service.id, 'start': start_dt.isoformat()}
        if extra:
            payload.update(extra)
        return self.client.post(
            f'/bus/{self.org.slug}/bookings/create/',
            data=json.dumps(payload),
            content_type='application/json',
            HTTP_HOST='127.0.0.1',
        )

    def _make_service_bookable_every_day(self, service: Service):
        for wd in range(7):
            ServiceWeeklyAvailability.objects.create(
                service=service,
                weekday=wd,
                start_time=time(0, 0),
                end_time=time(23, 59),
                is_active=True,
            )

    def test_min_notice_enforced(self):
        svc = Service.objects.create(
            organization=self.org,
            name='Notice',
            slug=f'notice-{uuid.uuid4().hex[:8]}',
            duration=60,
            min_notice_hours=24,
            max_booking_days=60,
        )
        ServiceAssignment.objects.create(service=svc, membership=self.mem)
        self._make_service_bookable_every_day(svc)

        start_dt = timezone.now() + timedelta(hours=1)
        resp = self._post_create(service=svc, start_dt=start_dt)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('min notice', resp.content.decode('utf-8').lower())

    def test_max_booking_days_enforced(self):
        svc = Service.objects.create(
            organization=self.org,
            name='MaxWindow',
            slug=f'max-{uuid.uuid4().hex[:8]}',
            duration=60,
            min_notice_hours=0,
            max_booking_days=30,
        )
        ServiceAssignment.objects.create(service=svc, membership=self.mem)
        self._make_service_bookable_every_day(svc)

        start_dt = timezone.now() + timedelta(days=31)
        resp = self._post_create(service=svc, start_dt=start_dt)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('too far', resp.content.decode('utf-8').lower())

    def test_create_booking_succeeds_when_rules_satisfied(self):
        svc = Service.objects.create(
            organization=self.org,
            name='OK',
            slug=f'ok-{uuid.uuid4().hex[:8]}',
            duration=60,
            min_notice_hours=1,
            max_booking_days=30,
        )
        ServiceAssignment.objects.create(service=svc, membership=self.mem)
        self._make_service_bookable_every_day(svc)

        start_dt = self._mk_start(days_ahead=2, hh=10)
        resp = self._post_create(service=svc, start_dt=start_dt)
        self.assertIn(resp.status_code, (200, 201), resp.content.decode('utf-8'))

        data = json.loads(resp.content.decode('utf-8'))
        self.assertEqual(data.get('status'), 'ok')
        self.assertTrue(data.get('id'))

    def test_buffer_violation_can_be_allowed_with_squish_warning(self):
        svc = Service.objects.create(
            organization=self.org,
            name='Squish',
            slug=f'squish-{uuid.uuid4().hex[:8]}',
            duration=60,
            buffer_after=30,
            allow_squished_bookings=True,
            min_notice_hours=0,
            max_booking_days=365,
        )
        ServiceAssignment.objects.create(service=svc, membership=self.mem)
        self._make_service_bookable_every_day(svc)

        day = self._mk_start(days_ahead=2, hh=0)
        existing_start = day.replace(hour=10, minute=0)
        existing_end = day.replace(hour=11, minute=0)
        Booking.objects.create(
            organization=self.org,
            service=svc,
            start=existing_start,
            end=existing_end,
            client_name='A',
            client_email='a@example.com',
            is_blocking=False,
        )

        # Starts exactly at existing_end; buffer_after=30 should trigger an overlap.
        new_start = day.replace(hour=11, minute=0)
        resp = self._post_create(service=svc, start_dt=new_start)
        self.assertIn(resp.status_code, (200, 201), resp.content.decode('utf-8'))

        data = json.loads(resp.content.decode('utf-8'))
        self.assertEqual(data.get('status'), 'ok')
        self.assertEqual(data.get('warning'), 'slot_violates_buffer')

    def test_facility_resources_auto_assigns_available_resource(self):
        svc = Service.objects.create(
            organization=self.org,
            name='ResourceSvc',
            slug=f'res-{uuid.uuid4().hex[:8]}',
            duration=60,
            min_notice_hours=0,
            max_booking_days=365,
            requires_facility_resources=True,
        )
        ServiceAssignment.objects.create(service=svc, membership=self.mem)
        self._make_service_bookable_every_day(svc)

        r1 = FacilityResource.objects.create(
            organization=self.org,
            name='Cage 1',
            slug=f'cage-1-{uuid.uuid4().hex[:6]}',
            is_active=True,
            max_services=0,
        )
        ServiceResource.objects.create(service=svc, resource=r1)

        start_dt = self._mk_start(days_ahead=2, hh=9)
        resp = self._post_create(service=svc, start_dt=start_dt)
        self.assertIn(resp.status_code, (200, 201), resp.content.decode('utf-8'))

        data = json.loads(resp.content.decode('utf-8'))
        event = data.get('event') or {}
        ext = (event.get('extendedProps') or {})
        self.assertEqual(ext.get('resource_id'), r1.id)

        created = Booking.objects.filter(organization=self.org, service=svc).order_by('-id').first()
        self.assertIsNotNone(created)
        self.assertEqual(getattr(created, 'resource_id', None), r1.id)

    def test_facility_resources_rejects_invalid_requested_resource(self):
        svc = Service.objects.create(
            organization=self.org,
            name='ResourceSvc2',
            slug=f'res2-{uuid.uuid4().hex[:8]}',
            duration=60,
            min_notice_hours=0,
            max_booking_days=365,
            requires_facility_resources=True,
        )
        ServiceAssignment.objects.create(service=svc, membership=self.mem)
        self._make_service_bookable_every_day(svc)

        linked = FacilityResource.objects.create(
            organization=self.org,
            name='Linked',
            slug=f'linked-{uuid.uuid4().hex[:6]}',
            is_active=True,
            max_services=0,
        )
        unlinked = FacilityResource.objects.create(
            organization=self.org,
            name='Unlinked',
            slug=f'unlinked-{uuid.uuid4().hex[:6]}',
            is_active=True,
            max_services=0,
        )
        ServiceResource.objects.create(service=svc, resource=linked)

        start_dt = self._mk_start(days_ahead=2, hh=10)
        resp = self._post_create(service=svc, start_dt=start_dt, extra={'resource_id': unlinked.id})
        self.assertEqual(resp.status_code, 400)
        self.assertIn('resource', resp.content.decode('utf-8').lower())

    def test_facility_resources_fails_when_none_available(self):
        svc = Service.objects.create(
            organization=self.org,
            name='ResourceSvc3',
            slug=f'res3-{uuid.uuid4().hex[:8]}',
            duration=60,
            min_notice_hours=0,
            max_booking_days=365,
            requires_facility_resources=True,
        )
        ServiceAssignment.objects.create(service=svc, membership=self.mem)
        self._make_service_bookable_every_day(svc)

        r1 = FacilityResource.objects.create(
            organization=self.org,
            name='Only',
            slug=f'only-{uuid.uuid4().hex[:6]}',
            is_active=True,
            max_services=0,
        )
        ServiceResource.objects.create(service=svc, resource=r1)

        day = self._mk_start(days_ahead=2, hh=0)
        busy_start = day.replace(hour=9, minute=0)
        busy_end = day.replace(hour=10, minute=0)
        Booking.objects.create(
            organization=self.org,
            service=svc,
            start=busy_start,
            end=busy_end,
            client_name='Busy',
            client_email='b@example.com',
            is_blocking=False,
            resource=r1,
        )

        resp = self._post_create(service=svc, start_dt=busy_start)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('no facility resources', resp.content.decode('utf-8').lower())
