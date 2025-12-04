from django.test import TestCase, Client
from accounts.models import Business
from bookings.models import Service


class PublicSmokeTests(TestCase):
    def setUp(self):
        # Ensure at least one business and service exist
        self.org, _ = Business.objects.get_or_create(slug='testing-orgy', defaults={'name': 'Testing Orgy'})
        self.svc, _ = Service.objects.get_or_create(organization=self.org, slug='60-minute-diddy-session', defaults={'name': '60 Minute Diddy Session', 'duration': 60})
        self.client = Client()

    def test_index_and_public_pages(self):
        resp = self.client.get('/', HTTP_HOST='127.0.0.1')
        self.assertIn(resp.status_code, (200, 302))

        resp = self.client.get(f'/bus/{self.org.slug}/', HTTP_HOST='127.0.0.1')
        self.assertEqual(resp.status_code, 200)

        resp = self.client.get(f'/bus/{self.org.slug}/service/{self.svc.slug}/', HTTP_HOST='127.0.0.1')
        self.assertEqual(resp.status_code, 200)

    def test_public_booking_invalid_post(self):
        resp = self.client.post(f'/bus/{self.org.slug}/service/{self.svc.slug}/', {'client_name':'', 'client_email':'', 'start':'', 'end':''}, HTTP_HOST='127.0.0.1')
        self.assertEqual(resp.status_code, 400)
