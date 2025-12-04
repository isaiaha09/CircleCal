from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.utils.text import slugify
from accounts.models import Business, Membership
from bookings.models import Service
from billing.models import Subscription
from django.utils import timezone


User = get_user_model()


class AuthenticatedSmokeTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.username = 'smoke_user'
        self.password = 'password123'
        self.user, created = User.objects.get_or_create(username=self.username, defaults={'email': 'smoke@example.com'})
        if created:
            self.user.set_password(self.password)
            self.user.save()

        self.org = Business.objects.first()
        if not self.org:
            self.org = Business.objects.create(name='Testing Orgy', slug='testing-orgy', owner=self.user)

        Membership.objects.update_or_create(user=self.user, organization=self.org, defaults={'role':'owner','is_active':True})

        self.svc = Service.objects.filter(organization=self.org, is_active=True).first()
        if not self.svc:
            self.svc = Service.objects.create(organization=self.org, name='Smoke Service', slug='smoke-service', duration=30)

        # Use force_login to avoid external auth backends interfering
        self.client.force_login(self.user)
        # Ensure org has a trialing subscription so billing checks allow availability edits
        Subscription.objects.update_or_create(
            organization=self.org,
            defaults={
                'status': 'trialing',
                'trial_end': timezone.now() + timezone.timedelta(days=7),
                'active': False,
            }
        )

    def test_owner_pages(self):
        resp = self.client.get(f'/bus/{self.org.slug}/services/', HTTP_HOST='127.0.0.1')
        self.assertEqual(resp.status_code, 200)

        resp = self.client.get(f'/bus/{self.org.slug}/services/{self.svc.id}/edit/', HTTP_HOST='127.0.0.1')
        self.assertEqual(resp.status_code, 200)

        resp = self.client.post(f'/bus/{self.org.slug}/services/{self.svc.id}/edit/', {'name': self.svc.name, 'slug': self.svc.slug, 'duration': self.svc.duration}, HTTP_HOST='127.0.0.1')
        self.assertIn(resp.status_code, (200, 302))

        resp = self.client.get(f'/bus/{self.org.slug}/services/create/', HTTP_HOST='127.0.0.1')
        self.assertEqual(resp.status_code, 200)

        new_slug = slugify('Smoke Created Service')
        resp = self.client.post(f'/bus/{self.org.slug}/services/create/', {'name':'Smoke Created Service', 'slug': new_slug, 'duration':30}, HTTP_HOST='127.0.0.1')
        self.assertIn(resp.status_code, (200, 302))

    def test_save_availability(self):
        payload = {
            'availability': [
                {'day': 1, 'ranges': ['09:00-12:00'], 'unavailable': False},
                {'day': 2, 'ranges': ['09:00-12:00'], 'unavailable': False},
            ]
        }
        import json
        resp = self.client.post(f'/bus/{self.org.slug}/availability/save/', json.dumps(payload), content_type='application/json', HTTP_HOST='127.0.0.1')
        self.assertEqual(resp.status_code, 200)
