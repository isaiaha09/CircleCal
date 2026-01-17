from types import SimpleNamespace

from accounts.models import Profile


def test_profile_avatar_upload_to_is_per_user():
    profile_stub = SimpleNamespace(user_id=123)
    path = Profile._profile_upload_to(profile_stub, "my_photo.png")
    assert path == "profile_pictures/user_123/avatar.png"


def test_profile_avatar_upload_to_defaults_to_jpg_when_no_ext():
    profile_stub = SimpleNamespace(user_id=456)
    path = Profile._profile_upload_to(profile_stub, "no_ext")
    assert path == "profile_pictures/user_456/avatar.jpg"
