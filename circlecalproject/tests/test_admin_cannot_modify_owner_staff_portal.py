from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Business, Membership
from billing.models import Plan, Subscription


User = get_user_model()


class AdminCannotModifyOwnerStaffPortalTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="owner1", email="owner1@example.com", password="pass")
        self.admin = User.objects.create_user(username="admin1", email="admin1@example.com", password="pass")
        self.staff = User.objects.create_user(username="staff1", email="staff1@example.com", password="pass")

        self.org = Business.objects.create(name="Org", slug="org", owner=self.owner)
        self.owner_membership = Membership.objects.create(
            user=self.owner, organization=self.org, role="owner", is_active=True
        )
        self.admin_membership = Membership.objects.create(
            user=self.admin, organization=self.org, role="admin", is_active=True
        )
        self.staff_membership = Membership.objects.create(
            user=self.staff, organization=self.org, role="staff", is_active=True
        )

        # Staff portal is Team-plan gated in production.
        plan = Plan.objects.create(name="Team", slug="team", stripe_price_id="price_team")
        Subscription.objects.create(
            organization=self.org,
            plan=plan,
            status="active",
            active=True,
            stripe_subscription_id="sub_test_123",
            start_date=timezone.now() - timezone.timedelta(days=5),
            trial_end=None,
        )

    def test_admin_cannot_change_owner_role(self):
        self.client.force_login(self.admin)
        url = (
            reverse("calendar_app:update_member_role", args=[self.org.slug, self.owner_membership.id])
            + "?role=staff"
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 403)

    def test_admin_cannot_remove_owner(self):
        self.client.force_login(self.admin)
        url = reverse("calendar_app:remove_member", args=[self.org.slug, self.owner_membership.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 403)

    def test_admin_can_change_non_owner_role(self):
        self.client.force_login(self.admin)
        url = (
            reverse("calendar_app:update_member_role", args=[self.org.slug, self.staff_membership.id])
            + "?role=manager"
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 302)

        self.staff_membership.refresh_from_db()
        self.assertEqual(self.staff_membership.role, "manager")
