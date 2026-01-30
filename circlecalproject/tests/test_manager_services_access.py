import uuid

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Business, Membership


class TestManagerServicesAccess(TestCase):
    def setUp(self):
        User = get_user_model()
        self.client = Client()

        self.owner = User.objects.create_user(username='owner_mgr', email='owner_mgr@example.com', password='pass')
        self.manager = User.objects.create_user(username='manager_mgr', email='manager_mgr@example.com', password='pass')

        self.org = Business.objects.create(name='OrgMgr', slug=f'org-{uuid.uuid4().hex[:8]}', owner=self.owner)
        Membership.objects.create(user=self.owner, organization=self.org, role='owner', is_active=True)
        Membership.objects.create(user=self.manager, organization=self.org, role='manager', is_active=True)

        self.client.force_login(self.manager)

    def test_manager_can_open_services_page(self):
        url = reverse('calendar_app:services_page', args=[self.org.slug])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
