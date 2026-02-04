import logging
import os
import tempfile
import importlib
from django import forms
from django.contrib.auth.models import User
from django.conf import settings
from .models import Profile
from django.utils import timezone
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    # Satisfy static type checkers / linters in environments without Pillow
    from PIL import Image, UnidentifiedImageError  # type: ignore
try:
    from PIL import Image, UnidentifiedImageError  # Pillow  # type: ignore
except Exception:
    Image = None
    class UnidentifiedImageError(Exception):
        pass
    import logging as _logging
    _logging.getLogger(__name__).warning(
        "Pillow (PIL) not available; image validation will be disabled. Install via 'pip install Pillow'."
    )
# ...existing code...

logger = logging.getLogger(__name__)

_NUDE_DETECTOR = None

def _get_nude_detector():
    global _NUDE_DETECTOR
    if _NUDE_DETECTOR is None:
        try:
            mod = importlib.import_module('nudenet')
            NudeDetector = getattr(mod, 'NudeDetector', None)
            if NudeDetector is None:
                raise ImportError("nudenet.NudeDetector not found")
            _NUDE_DETECTOR = NudeDetector()
        except Exception as e:
            logger.warning("NudeDetector unavailable: %s", e)
            _NUDE_DETECTOR = False  # sentinel for failure
    return _NUDE_DETECTOR

logger = logging.getLogger(__name__)

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
        fields = ["avatar", "timezone", "display_name", "email_alerts", "booking_reminders", "push_booking_notifications_enabled"]

    def clean_avatar(self):
        avatar = self.cleaned_data.get("avatar")

        # IMPORTANT (Cloudinary): When the user is *not* uploading a new avatar,
        # Django may populate `cleaned_data['avatar']` with the existing FieldFile.
        # For Cloudinary-backed storage, `storage.open()` is intentionally not
        # supported (no streaming), so we must not attempt PIL/Image.open().
        uploaded = self.files.get("avatar")
        if not uploaded:
            # ClearableFileInput uses False to indicate clear.
            if avatar is False:
                return None
            return avatar

        # From here on, we know there's a new uploaded file.
        avatar = uploaded
        if avatar:
            if Image is None:
                raise forms.ValidationError("Server error: image processing library not installed; please install Pillow.")

            # Guardrails for avatar uploads (kept intentionally conservative)
            max_bytes = int(getattr(settings, "AVATAR_MAX_UPLOAD_BYTES", 5 * 1024 * 1024))  # default: 5 MB
            max_px = int(getattr(settings, "AVATAR_MAX_PIXELS", 512))  # default: 512x512 bounding box

            try:
                uploaded_size = int(getattr(avatar, "size", 0) or 0)
            except Exception:
                uploaded_size = 0
            if uploaded_size and uploaded_size > max_bytes:
                raise forms.ValidationError("Please upload an image smaller than 5 MB.")

            # Validate mime type early
            allowed_types = {"image/jpeg", "image/png", "image/webp"}
            content_type = getattr(avatar, "content_type", None)
            if content_type and content_type.lower() not in allowed_types:
                raise forms.ValidationError("Please upload a JPG, PNG, or WebP image.")

            try:
                # Open and fully load to validate image integrity
                img = Image.open(avatar)
                img.load()

                # Ensure EXIF orientation is honored (common for phone photos)
                try:
                    from PIL import ImageOps  # type: ignore
                    img = ImageOps.exif_transpose(img)
                except Exception:
                    pass

                # Validate file format if Pillow can detect it
                fmt = (getattr(img, "format", None) or "").upper()
                if fmt and fmt not in {"JPEG", "JPG", "PNG", "WEBP"}:
                    raise forms.ValidationError("Please upload a JPG, PNG, or WebP image.")

                width, height = img.size
                # No orientation restriction; client-side cropper enforces square output

                # Normalize: resize to a reasonable avatar size (keeps aspect ratio)
                try:
                    img.thumbnail((max_px, max_px))
                except Exception:
                    # If thumbnail fails, keep original but still proceed to moderation
                    pass

                # Re-encode to keep files small and consistent.
                # - Use PNG only when alpha/transparency is present.
                # - Otherwise use JPEG for better compression.
                has_alpha = False
                try:
                    if img.mode in ("RGBA", "LA"):
                        has_alpha = True
                    elif img.mode == "P":
                        has_alpha = "transparency" in (img.info or {})
                except Exception:
                    has_alpha = False

                from io import BytesIO
                from django.core.files.uploadedfile import SimpleUploadedFile

                out = BytesIO()
                if has_alpha:
                    # Ensure a sane mode for PNG
                    if img.mode not in ("RGBA", "LA"):
                        try:
                            img = img.convert("RGBA")
                        except Exception:
                            pass
                    img.save(out, format="PNG", optimize=True)
                    out_name = "profile_pic.png"
                    out_type = "image/png"
                else:
                    # JPEG cannot store alpha; ensure RGB
                    if img.mode != "RGB":
                        try:
                            img = img.convert("RGB")
                        except Exception:
                            pass
                    img.save(out, format="JPEG", quality=85, optimize=True, progressive=True)
                    out_name = "profile_pic.jpg"
                    out_type = "image/jpeg"

                out.seek(0)
                avatar = SimpleUploadedFile(out_name, out.read(), content_type=out_type)
                # Keep cleaned_data consistent for downstream consumers
                self.cleaned_data["avatar"] = avatar

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
                    # Re-open the normalized avatar for moderation (ensures pointer is correct)
                    img_for_check = Image.open(avatar)
                    img_for_check.load()
                    if img_for_check.mode not in ("RGB", "L"):
                        img_for_check = img_for_check.convert("RGB")
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

    def save(self, commit=True):
        instance = super().save(commit=False)
        try:
            avatar_changed = "avatar" in getattr(self, "changed_data", [])
            # ClearableFileInput uses '<fieldname>-clear'
            avatar_cleared = str(self.data.get("avatar-clear", "")).lower() in ("1", "true", "on", "yes")
            avatar_uploaded = bool(self.files.get("avatar"))
            if avatar_changed or avatar_cleared or avatar_uploaded:
                instance.avatar_updated_at = timezone.now()
        except Exception:
            pass

        if commit:
            instance.save()
            try:
                self.save_m2m()
            except Exception:
                pass
        return instance

from django.contrib.auth.forms import PasswordResetForm
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm
from django import forms


class StaffAuthenticationForm(AuthenticationForm):
    """Authentication form for staff logins.

    - Shows the username field labeled as 'Email'
    - Uses a clearer invalid login message referencing 'email'
    """
    error_messages = {
        'invalid_login': (
            "Please enter a correct email and password."
            " Note that both fields may be case-sensitive."
        ),
        'inactive': "This account is inactive.",
    }

    def __init__(self, request=None, *args, **kwargs):
        super().__init__(request=request, *args, **kwargs)
        # Label username as Email and make it an email input
        if 'username' in self.fields:
            self.fields['username'].label = 'Email'
            try:
                self.fields['username'].widget.attrs.update({'type': 'email', 'placeholder': 'you@company.com'})
            except Exception:
                pass


class PasswordResetIncludeInactiveForm(PasswordResetForm):
    """PasswordResetForm that includes inactive users when finding accounts by email.

    By default Django's PasswordResetForm filters to `is_active=True`; this subclass
    yields users regardless of `is_active` so deactivated accounts can still receive
    reset emails (useful for self-serve reactivation flows).
    """
    def get_users(self, email):
        UserModel = get_user_model()
        email_field_name = UserModel.get_email_field_name() if hasattr(UserModel, 'get_email_field_name') else 'email'
        filter_kwargs = {f"{email_field_name}__iexact": email}
        for user in UserModel._default_manager.filter(**filter_kwargs):
            # Skip users without usable password
            if not user.has_usable_password():
                continue
            yield user



