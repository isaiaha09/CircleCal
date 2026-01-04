import logging

from django.conf import settings
from django.core.mail import EmailMessage
from django.template.loader import render_to_string


logger = logging.getLogger(__name__)


def _send_account_email(*, subject: str, template_name: str, to_email: str, context: dict) -> bool:
    if not to_email:
        logger.warning('Skipping email (missing to_email) subject=%r template=%r', subject, template_name)
        return False
    try:
        html_content = render_to_string(template_name, context)
        msg = EmailMessage(subject, html_content, settings.DEFAULT_FROM_EMAIL, [to_email])
        msg.content_subtype = "html"
        msg.send()
        return True
    except Exception:
        logger.exception('Failed sending account email template=%s to=%s', template_name, to_email)
        return False


def send_account_deactivated_email(user, *, business_names=None) -> bool:
    business_names = list(business_names or [])
    context = {
        'user': user,
        'site_url': getattr(settings, 'SITE_URL', 'http://localhost:8000'),
        'business_names': business_names,
    }
    return _send_account_email(
        subject='Your CircleCal account has been deactivated',
        template_name='accounts/emails/account_deactivated.html',
        to_email=getattr(user, 'email', ''),
        context=context,
    )


def send_account_deleted_email(user, *, business_names=None) -> bool:
    business_names = list(business_names or [])
    context = {
        'user': user,
        'site_url': getattr(settings, 'SITE_URL', 'http://localhost:8000'),
        'business_names': business_names,
    }
    return _send_account_email(
        subject='Your CircleCal account has been deleted',
        template_name='accounts/emails/account_deleted.html',
        to_email=getattr(user, 'email', ''),
        context=context,
    )


def send_trial_deletion_scheduled_email(user, *, scheduled_for, business_names=None) -> bool:
    business_names = list(business_names or [])
    context = {
        'user': user,
        'site_url': getattr(settings, 'SITE_URL', 'http://localhost:8000'),
        'business_names': business_names,
        'scheduled_for': scheduled_for,
    }
    return _send_account_email(
        subject='CircleCal account deactivation scheduled',
        template_name='accounts/emails/trial_deletion_scheduled.html',
        to_email=getattr(user, 'email', ''),
        context=context,
    )


def send_subscription_cancellation_scheduled_email(user, *, business_name: str, scheduled_for=None) -> bool:
    context = {
        'user': user,
        'business_name': business_name,
        'scheduled_for': scheduled_for,
        'site_url': getattr(settings, 'SITE_URL', 'http://localhost:8000'),
    }
    return _send_account_email(
        subject='Your CircleCal subscription will cancel at period end',
        template_name='accounts/emails/subscription_cancellation_scheduled.html',
        to_email=getattr(user, 'email', ''),
        context=context,
    )
