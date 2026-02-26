import os
from unittest.mock import patch

from django.test import SimpleTestCase

from calendar_app.render_api import get_render_config


class TestRenderApiConfig(SimpleTestCase):
    def test_reads_primary_env_vars(self):
        with patch.dict(os.environ, {"RENDER_API_KEY": "abc", "RENDER_SERVICE_ID": "srv-123"}, clear=True):
            cfg = get_render_config()
            self.assertIsNotNone(cfg)
            assert cfg is not None
            self.assertEqual(cfg.api_key, "abc")
            self.assertEqual(cfg.service_id, "srv-123")

    def test_strips_bearer_and_quotes(self):
        with patch.dict(
            os.environ,
            {"RENDER_API_KEY": "'Bearer abc123'", "RENDER_SERVICE_ID": "srv-123"},
            clear=True,
        ):
            cfg = get_render_config()
            self.assertIsNotNone(cfg)
            assert cfg is not None
            self.assertEqual(cfg.api_key, "abc123")

    def test_reads_alternate_env_var_names(self):
        with patch.dict(
            os.environ,
            {"RENDER_API_TOKEN": "abc", "RENDER_WEB_SERVICE_ID": "srv-456"},
            clear=True,
        ):
            cfg = get_render_config()
            self.assertIsNotNone(cfg)
            assert cfg is not None
            self.assertEqual(cfg.api_key, "abc")
            self.assertEqual(cfg.service_id, "srv-456")
