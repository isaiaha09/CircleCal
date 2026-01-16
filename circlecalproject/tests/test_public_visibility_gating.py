import uuid
from datetime import time

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Business, Membership
from billing.models import Plan, Subscription
from bookings.models import Service, ServiceWeeklyAvailability, WeeklyAvailability


class TestPublicVisibilityGatingOnPro(TestCase):
    def setUp(self):
        User = get_user_model()
        self.client = Client()

        self.owner = User.objects.create_user(username='owner', email='owner@example.com', password='pass')
        self.org = Business.objects.create(name='Org', slug=f'org-{uuid.uuid4().hex[:8]}', owner=self.owner)
        Membership.objects.create(user=self.owner, organization=self.org, role='owner', is_active=True)

        plan = Plan.objects.create(name='Pro', slug='pro', description='Pro', price=0, billing_period='monthly')
        Subscription.objects.update_or_create(
            organization=self.org,
            defaults={'plan': plan, 'status': 'active', 'active': True},
        )

        # Define overall org availability (Mon 9-5).
        WeeklyAvailability.objects.create(
            organization=self.org,
            weekday=0,
            start_time=time(9, 0),
            end_time=time(17, 0),
            is_active=True,
        )

        # Existing active service consumes the entire overall schedule.
        svc_a = Service.objects.create(
            organization=self.org,
            name='Service A',
            slug=f'a-{uuid.uuid4().hex[:6]}',
            description='',
            duration=60,
            price=0,
            buffer_before=0,
            buffer_after=0,
            min_notice_hours=0,
            max_booking_days=365,
            is_active=True,
            show_on_public_calendar=True,
        )
        ServiceWeeklyAvailability.objects.create(
            service=svc_a,
            weekday=0,
            start_time=time(9, 0),
            end_time=time(17, 0),
            is_active=True,
        )

        self.client.force_login(self.owner)

    def test_create_service_cannot_be_public_without_remaining_space(self):
        url = reverse('calendar_app:create_service', args=[self.org.slug])
        resp = self.client.post(
            url,
            data={
                'name': 'Service B',
                'description': '',
                'duration': '60',
                'price': '0',
                'buffer_after': '0',
                'allow_stripe_payments': '1',
                'min_notice_hours': '24',
                'max_booking_days': '30',
                # User tries to turn on public visibility immediately.
                'show_on_public_calendar': '1',
                # Intentionally omit svc_avail_* (no remaining space to allocate).
            },
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)

        svc_b = Service.objects.get(organization=self.org, name='Service B')
        self.assertFalse(bool(svc_b.show_on_public_calendar))

        msgs = [m.message for m in get_messages(resp.wsgi_request)]
        self.assertTrue(any('public' in (m or '').lower() for m in msgs))
