import os
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import Profile
import accounts.storage as storage_mod


User = get_user_model()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="cc_test_media_"))
class ProfileAvatarClearTests(TestCase):
    def test_clearing_avatar_removes_field_and_deletes_file_when_possible(self):
        user = User.objects.create_user(username="u_avatar", email="u_avatar@example.com", password="pass12345")
        self.client.force_login(user)

        profile, _ = Profile.objects.get_or_create(user=user)

        # Create a tiny valid PNG. We mock storage network calls when Cloudinary
        # is active so this test is deterministic.
        png_bytes = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe"
            b"\r\xefF\xb8\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        uploaded = SimpleUploadedFile("avatar.png", png_bytes, content_type="image/png")

        use_cloudinary = bool(getattr(storage_mod, "_use_cloudinary", False) and getattr(storage_mod, "cloudinary", None) is not None)
        if use_cloudinary:
            with patch("accounts.storage.cloudinary.uploader.upload", return_value={"format": "png"}) as _up, patch(
                "accounts.storage.cloudinary.uploader.destroy", return_value={"result": "ok"}
            ) as _destroy:
                profile.avatar = uploaded
                profile.save()
        else:
            profile.avatar = uploaded
            profile.save()

        # Confirm the ClearableFileInput checkbox name we should submit.
        get_resp = self.client.get(reverse("accounts:profile"), HTTP_HOST="127.0.0.1")
        self.assertEqual(get_resp.status_code, 200)
        self.assertIn('name="avatar-clear"', get_resp.content.decode("utf-8"))

        # Capture the stored name/path before clearing.
        old_name = profile.avatar.name
        storage = profile.avatar.storage
        old_fs_path = None
        try:
            if hasattr(storage, "path"):
                old_fs_path = storage.path(old_name)
        except Exception:
            old_fs_path = None

        # Clear via the profile form submission.
        resp = self.client.post(
            reverse("accounts:profile"),
            data={
                "first_name": "Test",
                "last_name": "User",
                "avatar-clear": "on",
                # Keep booleans stable (matches how checked checkboxes submit)
                "email_alerts": "on",
                "booking_reminders": "on",
                "push_booking_notifications_enabled": "on",
            },
            HTTP_HOST="127.0.0.1",
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(getattr(resp, "redirect_chain", None), "Expected redirect on successful profile save")

        profile.refresh_from_db()
        self.assertFalse(bool(profile.avatar))

        # If we're using filesystem-backed storage, ensure the file is gone.
        if old_fs_path is not None:
            self.assertFalse(os.path.exists(old_fs_path))
