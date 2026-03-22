import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken


User = get_user_model()


class ApiAuthSecurityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='otp-user',
            email='otp@example.com',
            password='pass12345',
        )

    def _create_static_otp(self, token='123456'):
        device = StaticDevice.objects.create(user=self.user, name='default', confirmed=True)
        StaticToken.objects.create(device=device, token=token)
        return token

    def test_generic_token_endpoint_requires_otp_when_device_exists(self):
        self._create_static_otp()

        resp = self.client.post(
            '/api/v1/auth/token/',
            data=json.dumps({'username': self.user.username, 'password': 'pass12345'}),
            content_type='application/json',
        )

        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body.get('detail'), 'otp_required')
        self.assertTrue(body.get('otp_required'))

    def test_generic_token_endpoint_accepts_valid_otp(self):
        otp = self._create_static_otp(token='654321')

        resp = self.client.post(
            '/api/v1/auth/token/',
            data=json.dumps({'username': self.user.username, 'password': 'pass12345', 'otp': otp}),
            content_type='application/json',
        )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn('access', body)
        self.assertIn('refresh', body)
        self.assertFalse(body.get('otp_required', False))