import pytest

from django.test import RequestFactory
from django.utils import timezone


@pytest.mark.django_db
def test_axes_handler_does_not_store_get_or_post_payloads():
    from axes.models import AccessAttempt

    from circlecalproject.axes_handlers import PrivacyPreservingAxesDatabaseHandler

    rf = RequestFactory()

    request = rf.post(
        "/accounts/login/",
        data={
            "username": "alice",
            "password": "super-secret",
            "otp": "123456",
        },
    )
    request.GET = request.GET.copy()
    request.GET.update({"next": "/"})

    # Axes middleware normally sets these.
    request.axes_ip_address = "127.0.0.1"
    request.axes_user_agent = "pytest"
    request.axes_path_info = request.path
    request.axes_attempt_time = timezone.now()
    request.axes_http_accept = "*/*"
    request.axes_locked_out = False

    handler = PrivacyPreservingAxesDatabaseHandler()
    handler.user_login_failed(request=request, credentials={"username": "alice"})

    attempt = AccessAttempt.objects.get(
        username="alice",
        ip_address="127.0.0.1",
        user_agent="pytest",
    )
    assert attempt.get_data == ""
    assert attempt.post_data == ""
