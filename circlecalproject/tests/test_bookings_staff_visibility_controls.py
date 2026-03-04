import uuid
from datetime import datetime, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Business, Membership
from billing.models import Plan, Subscription
from bookings.models import AuditBooking, Booking, Service, ServiceAssignment


@override_settings(TURNSTILE_ENABLED=False)
class TestBookingsStaffVisibilityControls(TestCase):
    def setUp(self):
        User = get_user_model()

        self.owner = User.objects.create_user(
            username=f"owner-{uuid.uuid4().hex[:8]}",
            email="owner@example.com",
            password="pass",
        )
        self.staff = User.objects.create_user(
            username=f"staff-{uuid.uuid4().hex[:8]}",
            email="staff@example.com",
            password="pass",
        )
        self.staff_other = User.objects.create_user(
            username=f"staff-other-{uuid.uuid4().hex[:8]}",
            email="staff.other@example.com",
            password="pass",
        )
        self.manager = User.objects.create_user(
            username=f"manager-{uuid.uuid4().hex[:8]}",
            email="manager@example.com",
            password="pass",
        )

        self.org = Business.objects.create(
            name="Staff Scope Org",
            slug=f"org-{uuid.uuid4().hex[:10]}",
            owner=self.owner,
            timezone="UTC",
        )

        self.owner_mem = Membership.objects.create(user=self.owner, organization=self.org, role="owner", is_active=True)
        self.staff_mem = Membership.objects.create(user=self.staff, organization=self.org, role="staff", is_active=True)
        self.staff_other_mem = Membership.objects.create(user=self.staff_other, organization=self.org, role="staff", is_active=True)
        self.manager_mem = Membership.objects.create(user=self.manager, organization=self.org, role="manager", is_active=True)

        plan = Plan.objects.create(name="Team", slug="team", description="Team", price=0, billing_period="monthly")
        Subscription.objects.update_or_create(
            organization=self.org,
            defaults={"plan": plan, "status": "active", "active": True},
        )

        self.staff_service = Service.objects.create(
            organization=self.org,
            name="Staff Service",
            slug=f"staff-svc-{uuid.uuid4().hex[:8]}",
            duration=30,
            price=50,
            show_on_public_calendar=True,
            is_active=True,
        )
        self.other_service = Service.objects.create(
            organization=self.org,
            name="Other Service",
            slug=f"other-svc-{uuid.uuid4().hex[:8]}",
            duration=30,
            price=60,
            show_on_public_calendar=True,
            is_active=True,
        )

        ServiceAssignment.objects.create(service=self.staff_service, membership=self.staff_mem)
        ServiceAssignment.objects.create(service=self.other_service, membership=self.staff_other_mem)

        now = timezone.now()
        self.staff_booking = Booking.objects.create(
            organization=self.org,
            service=self.staff_service,
            title="Staff booking",
            start=now + timedelta(days=1),
            end=now + timedelta(days=1, minutes=30),
            client_name="Visible Client",
            client_email="visible@example.com",
            is_blocking=False,
        )
        self.other_booking = Booking.objects.create(
            organization=self.org,
            service=self.other_service,
            title="Other booking",
            start=now + timedelta(days=2),
            end=now + timedelta(days=2, minutes=30),
            client_name="Hidden Client",
            client_email="hidden@example.com",
            is_blocking=False,
        )

        self.staff_audit = AuditBooking.objects.create(
            organization=self.org,
            booking_id=self.staff_booking.id,
            event_type=AuditBooking.EVENT_DELETED,
            booking_snapshot={
                "public_ref": self.staff_booking.public_ref,
                "start": self.staff_booking.start.isoformat(),
                "end": self.staff_booking.end.isoformat(),
            },
            service=self.staff_service,
            start=self.staff_booking.start,
            end=self.staff_booking.end,
            client_name=self.staff_booking.client_name,
            client_email=self.staff_booking.client_email,
        )
        self.other_audit = AuditBooking.objects.create(
            organization=self.org,
            booking_id=self.other_booking.id,
            event_type=AuditBooking.EVENT_DELETED,
            booking_snapshot={
                "public_ref": self.other_booking.public_ref,
                "start": self.other_booking.start.isoformat(),
                "end": self.other_booking.end.isoformat(),
            },
            service=self.other_service,
            start=self.other_booking.start,
            end=self.other_booking.end,
            client_name=self.other_booking.client_name,
            client_email=self.other_booking.client_email,
        )

    def test_staff_bookings_page_only_shows_assigned_bookings_and_hides_controls(self):
        self.client.force_login(self.staff)
        url = reverse("calendar_app:bookings_list", args=[self.org.slug])
        resp = self.client.get(url)

        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode("utf-8", errors="ignore")

        self.assertIn("Visible Client", body)
        self.assertNotIn("Hidden Client", body)

        self.assertNotIn("id=\"deleteSelectedBtn\"", body)
        self.assertNotIn("id=\"exportAuditBtn\"", body)
        self.assertNotIn("id=\"deleteAuditBtn\"", body)

    def test_staff_recent_and_audit_endpoints_are_scoped(self):
        self.client.force_login(self.staff)

        since = (timezone.now() - timedelta(days=7)).isoformat()
        recent_url = reverse("calendar_app:bookings_recent", args=[self.org.slug])
        recent_resp = self.client.get(recent_url, {"since": since})
        self.assertEqual(recent_resp.status_code, 200)
        ids = {int(it["id"]) for it in recent_resp.json().get("items", [])}
        self.assertIn(self.staff_booking.id, ids)
        self.assertNotIn(self.other_booking.id, ids)

        audit_url = reverse("calendar_app:bookings_audit_list", args=[self.org.slug])
        audit_resp = self.client.get(audit_url)
        self.assertEqual(audit_resp.status_code, 200)
        audit_booking_ids = {int(it["booking_id"]) for it in audit_resp.json().get("items", []) if it.get("booking_id") is not None}
        self.assertIn(self.staff_booking.id, audit_booking_ids)
        self.assertNotIn(self.other_booking.id, audit_booking_ids)

    def test_staff_cannot_export_audit(self):
        self.client.force_login(self.staff)
        url = reverse("calendar_app:bookings_audit_export", args=[self.org.slug])
        resp = self.client.get(url, {"ids": str(self.staff_audit.id)})
        self.assertEqual(resp.status_code, 403)

    def test_manager_can_access_audit_export(self):
        self.client.force_login(self.manager)
        url = reverse("calendar_app:bookings_audit_export", args=[self.org.slug])
        resp = self.client.get(url, {"ids": str(self.staff_audit.id)})
        self.assertEqual(resp.status_code, 200)
