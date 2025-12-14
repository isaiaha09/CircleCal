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
    avatar = forms.ImageField(required=False)
    timezone = forms.ChoiceField(choices=_tz_choices(), required=False)

    class Meta:
        model = Profile
        fields = ["avatar", "timezone", "email_alerts", "booking_reminders"]

    def clean_avatar(self):
        avatar = self.cleaned_data.get("avatar")
        if avatar:
            # Validate mime type early
            allowed_types = {"image/jpeg", "image/png", "image/webp"}
            content_type = getattr(avatar, "content_type", None)
            if content_type and content_type.lower() not in allowed_types:
                raise forms.ValidationError("Please upload a JPG, PNG, or WebP image.")

            try:
                # Open and fully load to validate image integrity
                img = Image.open(avatar)
                img.load()

                width, height = img.size
                # No orientation restriction; client-side cropper enforces square output

            except UnidentifiedImageError:
                raise forms.ValidationError("Unsupported or corrupted image file.")
            except OSError:
                raise forms.ValidationError("Could not process the image file.")
            finally:
                # Reset file pointer for Django's file handling
                try:
                    avatar.seek(0)
                except Exception:
                    pass

            # Run NSFW moderation (convert to RGB if needed and save temp as JPEG)
            detector = _get_nude_detector()
            if detector is False:
                # Detector failed to initialize
                if getattr(settings, 'DEBUG', False):
                    logger.warning("Skipping image moderation in DEBUG due to unavailable detector.")
                else:
                    raise forms.ValidationError("Image moderation is temporarily unavailable. Please try again later.")
            elif detector:
                try:
                    img_for_check = img
                    if img.mode not in ("RGB", "L"):
                        img_for_check = img.convert("RGB")
                    # Windows-safe temp file pattern: mkstemp then close handle
                    fd, temp_path = tempfile.mkstemp(suffix='.jpg')
                    os.close(fd)
                    try:
                        img_for_check.save(temp_path, format='JPEG')
                        result = detector.detect(temp_path) or []
                        if getattr(settings, 'DEBUG', False):
                            try:
                                logger.info("Moderation detections: %s", result)
                            except Exception:
                                pass
                        # Consider explicit only if exposed sensitive parts over confidence threshold
                        threshold = 0.7
                        sensitive_tokens = ("BREAST", "BUTTOCK", "GENITAL", "AREOLA", "ANUS")
                        explicit = False
                        for det in result:
                            label = str(det.get('label') or det.get('class') or '').upper()
                            score = float(det.get('score') or det.get('confidence') or 0)
                            if 'COVERED' in label:
                                continue
                            if 'EXPOSED' in label and any(tok in label for tok in sensitive_tokens) and score >= threshold:
                                explicit = True
                                break
                        if explicit:
                            raise forms.ValidationError("Please upload a non-explicit photo.")
                    finally:
                        try:
                            os.remove(temp_path)
                        except Exception:
                            pass
                except forms.ValidationError:
                    raise
                except Exception as e:
                    logger.exception("Image moderation error: %s", e)
                    if getattr(settings, 'DEBUG', False):
                        # Do not block dev if moderation breaks
                        pass
                    else:
                        raise forms.ValidationError("Image moderation failed. Please try again later.")
        return avatar
