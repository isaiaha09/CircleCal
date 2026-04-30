from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import PushDevice
from accounts.push import send_push_to_user


User = get_user_model()


class ApiPushSecurityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='push-user', email='push@example.com', password='pw')
        self.client.force_login(self.user)

    @override_settings(MAX_ACTIVE_PUSH_DEVICES_PER_USER=2)
    def test_push_registration_deactivates_oldest_extra_devices(self):
        self.client.post('/api/v1/push/tokens/', data={'token': 'ExponentPushToken[1]', 'platform': 'ios'})
        self.client.post('/api/v1/push/tokens/', data={'token': 'ExponentPushToken[2]', 'platform': 'ios'})
        self.client.post('/api/v1/push/tokens/', data={'token': 'ExponentPushToken[3]', 'platform': 'ios'})

        active_tokens = list(
            PushDevice.objects.filter(user=self.user, is_active=True).order_by('token').values_list('token', flat=True)
        )
        self.assertEqual(active_tokens, ['ExponentPushToken[2]', 'ExponentPushToken[3]'])

    @override_settings(REST_FRAMEWORK={'DEFAULT_THROTTLE_RATES': {'push_write': '1/minute'}})
    def test_push_registration_is_throttled(self):
        cache.clear()
        resp1 = self.client.post('/api/v1/push/tokens/', data={'token': 'ExponentPushToken[a]', 'platform': 'ios'})
        resp2 = self.client.post('/api/v1/push/tokens/', data={'token': 'ExponentPushToken[b]', 'platform': 'ios'})

        self.assertEqual(resp1.status_code, 200)
        self.assertEqual(resp2.status_code, 429)

    @override_settings(MAX_ACTIVE_PUSH_DEVICES_PER_USER=2, EXPO_PUSH_ENABLED=True)
    @patch('accounts.push.send_expo_push', return_value={'data': []})
    def test_push_send_caps_fanout_to_latest_active_devices(self, mock_send):
        now = timezone.now()
        PushDevice.objects.create(user=self.user, token='ExponentPushToken[old]', is_active=True, last_seen_at=now - timedelta(days=2))
        PushDevice.objects.create(user=self.user, token='ExponentPushToken[mid]', is_active=True, last_seen_at=now - timedelta(days=1))
        PushDevice.objects.create(user=self.user, token='ExponentPushToken[new]', is_active=True, last_seen_at=now)

        attempted = send_push_to_user(user=self.user, title='Hello', body='World')

        self.assertEqual(attempted, 2)
        sent_messages = mock_send.call_args.args[0]
        self.assertEqual([msg['to'] for msg in sent_messages], ['ExponentPushToken[new]', 'ExponentPushToken[mid]'])