"""Custom django-axes handlers for CircleCal.

Goal: reduce sensitive data retention. In particular, avoid persisting request
GET/POST payloads into the database while preserving lockout functionality.
"""

from __future__ import annotations

from logging import getLogger

from django.db import router, transaction
from django.db.models import F

from axes.conf import settings
from axes.handlers.database import AxesDatabaseHandler
from axes.helpers import (
    get_client_str,
    get_client_username,
    get_failure_limit,
    get_lockout_parameters,
)
from axes.models import AccessAttempt
from axes.signals import user_locked_out

log = getLogger(__name__)


class PrivacyPreservingAxesDatabaseHandler(AxesDatabaseHandler):
    """Database handler that does NOT store request GET/POST bodies.

    django-axes' default database handler stores a rendered representation of
    request.GET and request.POST into AccessAttempt.get_data / post_data.

    We keep the lockout and auditing behavior, but set those fields to empty
    strings and never append payload data.
    """

    def user_login_failed(self, sender=None, credentials=None, request=None, **kwargs):  # type: ignore[override]
        if request is None:
            log.warning(
                "AXES: %s.user_login_failed does not function without a request.",
                self.__class__.__name__,
            )
            return

        # Clean up expired attempts before logging new ones.
        self.clean_expired_user_attempts(request, credentials)

        username = get_client_username(request, credentials)
        client_str = get_client_str(
            username,
            request.axes_ip_address,
            request.axes_user_agent,
            request.axes_path_info,
            request,
        )

        # If axes denied access, don't record the failed attempt as that would reset the lockout time.
        if (
            not settings.AXES_RESET_COOL_OFF_ON_FAILURE_DURING_LOCKOUT
            and request.axes_locked_out
        ):
            request.axes_credentials = credentials
            user_locked_out.send(
                "axes",
                request=request,
                username=username,
                ip_address=request.axes_ip_address,
            )
            return

        # PRIVACY: do not persist request.GET / request.POST.
        get_data = ""
        post_data = ""

        if self.is_whitelisted(request, credentials):
            log.info("AXES: Login failed from whitelisted client %s.", client_str)
            return

        lockout_parameters = get_lockout_parameters(request, credentials)
        if lockout_parameters == ["username"] and username is None:
            log.warning(
                "AXES: Username is None and username is the only lockout parameter; record will not be created."
            )
        else:
            with transaction.atomic(using=router.db_for_write(AccessAttempt)):
                attempt, created = AccessAttempt.objects.select_for_update().get_or_create(
                    username=username,
                    ip_address=request.axes_ip_address,
                    user_agent=request.axes_user_agent,
                    defaults={
                        "get_data": get_data,
                        "post_data": post_data,
                        "http_accept": request.axes_http_accept,
                        "path_info": request.axes_path_info,
                        "failures_since_start": 1,
                        "attempt_time": request.axes_attempt_time,
                    },
                )

                if created:
                    log.warning(
                        "AXES: New login failure by %s. Created new record in the database.",
                        client_str,
                    )
                else:
                    # Update metadata, but intentionally do NOT append request payloads.
                    attempt.http_accept = request.axes_http_accept
                    attempt.path_info = request.axes_path_info
                    attempt.failures_since_start = F("failures_since_start") + 1
                    attempt.attempt_time = request.axes_attempt_time
                    attempt.save(update_fields=["http_accept", "path_info", "failures_since_start", "attempt_time"])

                    log.warning(
                        "AXES: Repeated login failure by %s. Updated existing record in the database.",
                        client_str,
                    )

        failures_since_start = self.get_failures(request, credentials)
        request.axes_failures_since_start = failures_since_start

        if (
            settings.AXES_LOCK_OUT_AT_FAILURE
            and failures_since_start >= get_failure_limit(request, credentials)
        ):
            log.warning("AXES: Locking out %s.", client_str)
            request.axes_locked_out = True
            request.axes_credentials = credentials
            user_locked_out.send(
                "axes",
                request=request,
                username=username,
                ip_address=request.axes_ip_address,
            )
