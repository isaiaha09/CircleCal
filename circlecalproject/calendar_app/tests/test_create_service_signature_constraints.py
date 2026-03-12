from datetime import datetime

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Business, Membership
from billing.models import Plan, Subscription
from bookings.models import Service, ServiceWeeklyAvailability, WeeklyAvailability


class CreateServiceSignatureConstraintTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username='create-svc-owner',
            email='owner@example.com',
            password='pass',
        )
        self.org = Business.objects.create(name='Create Org', slug='create-org', owner=self.user)
        Membership.objects.create(user=self.user, organization=self.org, role='owner', is_active=True)

        pro_plan = Plan.objects.create(
            name='Pro',
            slug='pro',
            billing_period='monthly',
            price='29.00',
        )
        Subscription.objects.create(
            organization=self.org,
            plan=pro_plan,
            status='active',
            active=True,
        )

        WeeklyAvailability.objects.create(
            organization=self.org,
            weekday=0,
            start_time=datetime.strptime('09:00', '%H:%M').time(),
            end_time=datetime.strptime('17:00', '%H:%M').time(),
            is_active=True,
        )

        self.existing_service = Service.objects.create(
            organization=self.org,
            name='Existing Session',
            slug='existing-session',
            duration=60,
            price=0,
            buffer_after=15,
            time_increment_minutes=30,
            use_fixed_increment=False,
            allow_squished_bookings=False,
            allow_ends_after_availability=False,
            min_notice_hours=24,
            max_booking_days=30,
        )
        ServiceWeeklyAvailability.objects.create(
            service=self.existing_service,
            weekday=0,
            start_time=datetime.strptime('09:00', '%H:%M').time(),
            end_time=datetime.strptime('17:00', '%H:%M').time(),
            is_active=True,
        )

        self.client = Client()
        self.client.force_login(self.user)

    def test_constraints_endpoint_keeps_same_signature_windows_available(self):
        response = self.client.get(
            reverse('calendar_app:service_availability_constraints', kwargs={'org_slug': self.org.slug}),
            {
                'duration': '60',
                'buffer_after': '15',
                'time_increment_minutes': '30',
                'use_fixed_increment': '0',
                'allow_squished_bookings': '0',
                'allow_ends_after_availability': '0',
            },
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        monday = data['days'][1]
        self.assertFalse(monday['allowed_empty'])
        self.assertEqual(monday['remaining'], [{'start': 540, 'end': 1020}])

    def test_create_service_allows_weekly_windows_when_signature_matches(self):
        response = self.client.post(
            reverse('calendar_app:create_service', kwargs={'org_slug': self.org.slug}),
            {
                'name': 'New Matching Session',
                'description': 'Same scheduling signature as existing service.',
                'duration': '60',
                'price': '0',
                'buffer_after': '15',
                'min_notice_hours': '24',
                'max_booking_days': '30',
                'time_increment_minutes': '30',
                'svc_avail_1': '09:00-10:00',
                'allow_stripe_payments': '1',
            },
            follow=False,
        )

        self.assertEqual(response.status_code, 302)
        new_service = Service.objects.get(organization=self.org, slug='new-matching-session')
        windows = list(
            ServiceWeeklyAvailability.objects.filter(service=new_service, is_active=True)
            .values_list('weekday', 'start_time', 'end_time')
        )
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0][0], 0)
        self.assertEqual(windows[0][1].strftime('%H:%M'), '09:00')
        self.assertEqual(windows[0][2].strftime('%H:%M'), '10:00')