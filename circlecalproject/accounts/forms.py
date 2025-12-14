import logging
import os
import tempfile
from django import forms
from django.contrib.auth.models import User
from django.conf import settings
from .models import Profile
from PIL import Image, UnidentifiedImageError

logger = logging.getLogger(__name__)

_NUDE_DETECTOR = None

def _get_nude_detector():
    global _NUDE_DETECTOR
    if _NUDE_DETECTOR is None:
        try:
            from nudenet import NudeDetector
            _NUDE_DETECTOR = NudeDetector()
        except Exception as e:
            logger.warning("NudeDetector unavailable: %s", e)
            _NUDE_DETECTOR = False  # sentinel for failure
    return _NUDE_DETECTOR

from zoneinfo import available_timezones

def _tz_choices():
    # Prioritize common zones on top
    common = [
        'UTC',
        'America/Los_Angeles','America/Denver','America/Chicago','America/New_York',
        'Europe/London','Europe/Berlin','Europe/Paris','Europe/Madrid','Europe/Rome',
        'Asia/Kolkata','Asia/Tokyo','Asia/Shanghai','Asia/Singapore','Australia/Sydney'
    ]
    rest = sorted(tz for tz in available_timezones() if tz not in common)
    ordered = common + rest
    return [(tz, tz) for tz in ordered]

class ProfileForm(forms.ModelForm):
    timezone = forms.ChoiceField(choices=_tz_choices(), required=False)

    class Meta:
        model = Profile
        fields = ["timezone", "email_alerts", "booking_reminders"]
