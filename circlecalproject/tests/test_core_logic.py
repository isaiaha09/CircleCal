from django.test import TestCase
from accounts.models import Business
from bookings.models import Service, WeeklyAvailability, ServiceWeeklyAvailability
from bookings.models import Booking
from django.utils import timezone
from datetime import datetime, time, timedelta


class CoreLogicTests(TestCase):
    def setUp(self):
        self.org = Business.objects.create(name='Logic Org', slug='logic-org')
        self.service = Service.objects.create(organization=self.org, name='Logic Service', slug='logic-service', duration=30)

    def test_service_weekly_availability_clean_within_org(self):
        # Org weekly availability: Mon 09:00-17:00
        WeeklyAvailability.objects.create(organization=self.org, weekday=0, start_time=time(9,0), end_time=time(17,0), is_active=True)
        # Service window inside org window should be valid
        swa = ServiceWeeklyAvailability(service=self.service, weekday=0, start_time=time(10,0), end_time=time(11,0), is_active=True)
        swa.full_clean()  # should not raise

    def test_service_weekly_availability_clean_outside_org(self):
        WeeklyAvailability.objects.create(organization=self.org, weekday=0, start_time=time(9,0), end_time=time(12,0), is_active=True)
        swa = ServiceWeeklyAvailability(service=self.service, weekday=0, start_time=time(8,0), end_time=time(10,0), is_active=True)
        with self.assertRaises(Exception):
            swa.full_clean()

    def test_is_within_availability_overrides(self):
        # Create a blocking per-date override for tomorrow covering 10:00-12:00
        tomorrow = (timezone.now() + timedelta(days=1)).date()
        start_dt = timezone.make_aware(datetime.combine(tomorrow, time(10,0)), timezone.get_current_timezone())
        end_dt = timezone.make_aware(datetime.combine(tomorrow, time(12,0)), timezone.get_current_timezone())
        Booking.objects.create(organization=self.org, start=start_dt, end=end_dt, is_blocking=True, service=None)

        # A slot fully inside the blocking window should be unavailable
        from bookings.views import is_within_availability
        s = timezone.make_aware(datetime.combine(tomorrow, time(10,30)), timezone.get_current_timezone())
        e = timezone.make_aware(datetime.combine(tomorrow, time(11,0)), timezone.get_current_timezone())
        self.assertFalse(is_within_availability(self.org, s, e, service=None))

    def test_service_weekly_rows_scope_days_no_fallback_to_org(self):
        """If a service has any service-weekly availability rows, it should be
        restricted to ONLY those days/times and not fall back to org weekly."""
        # Org is open every day 09:00-17:00
        for wd in range(7):
            WeeklyAvailability.objects.create(
                organization=self.org,
                weekday=wd,
                start_time=time(9, 0),
                end_time=time(17, 0),
                is_active=True,
            )

        # Service is only open on Wednesday (weekday=2) 10:00-11:00
        ServiceWeeklyAvailability.objects.create(
            service=self.service,
            weekday=2,
            start_time=time(10, 0),
            end_time=time(11, 0),
            is_active=True,
        )

        from bookings.views import is_within_availability
        # Monday 10:00-10:30 should be unavailable for this service
        d_mon = (timezone.now() + timedelta(days=(7 - timezone.now().weekday()))).date()  # next Monday
        s_mon = timezone.make_aware(datetime.combine(d_mon, time(10, 0)), timezone.get_current_timezone())
        e_mon = timezone.make_aware(datetime.combine(d_mon, time(10, 30)), timezone.get_current_timezone())
        self.assertFalse(is_within_availability(self.org, s_mon, e_mon, service=self.service))

        # Next Wednesday 10:00-10:30 should be available
        d_wed = d_mon + timedelta(days=2)
        s_wed = timezone.make_aware(datetime.combine(d_wed, time(10, 0)), timezone.get_current_timezone())
        e_wed = timezone.make_aware(datetime.combine(d_wed, time(10, 30)), timezone.get_current_timezone())
        self.assertTrue(is_within_availability(self.org, s_wed, e_wed, service=self.service))

    def test_unassigned_service_no_weekly_rows_no_fallback_to_org(self):
        """Unassigned services have their own schedule; an empty schedule means no availability."""
        # Org is open every day 09:00-17:00
        for wd in range(7):
            WeeklyAvailability.objects.create(
                organization=self.org,
                weekday=wd,
                start_time=time(9, 0),
                end_time=time(17, 0),
                is_active=True,
            )

        # No ServiceWeeklyAvailability rows, and no ServiceAssignment rows => unassigned

        from bookings.views import is_within_availability
        d = (timezone.now() + timedelta(days=1)).date()
        s = timezone.make_aware(datetime.combine(d, time(10, 0)), timezone.get_current_timezone())
        e = timezone.make_aware(datetime.combine(d, time(10, 30)), timezone.get_current_timezone())
        self.assertFalse(is_within_availability(self.org, s, e, service=self.service))
