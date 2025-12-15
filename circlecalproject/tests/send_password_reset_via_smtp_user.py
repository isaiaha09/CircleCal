import os,traceback
os.environ.setdefault("DJANGO_SETTINGS_MODULE","circlecalproject.settings_prod")
import django; django.setup()
from django.contrib.auth.forms import PasswordResetForm
from django.conf import settings
target="emailtestappworks@gmail.com"
from_addr = os.environ.get("BREVO_SMTP_USER") or settings.DEFAULT_FROM_EMAIL
form = PasswordResetForm({"email": target})
if not form.is_valid():
    print("No user found for that email or invalid form.")
else:
    try:
        form.save(from_email=from_addr, domain_override="circlecal.app", use_https=True)
        print("Triggered password reset using From=", from_addr)
    except Exception:
        traceback.print_exc()
