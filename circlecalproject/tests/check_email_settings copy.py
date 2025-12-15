import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE','circlecalproject.settings_prod')
import django
django.setup()
from django.conf import settings
print("EMAIL_BACKEND=", settings.EMAIL_BACKEND)
print("EMAIL_HOST=", settings.EMAIL_HOST)
print("EMAIL_PORT=", settings.EMAIL_PORT)
print("EMAIL_USE_TLS=", settings.EMAIL_USE_TLS)
print("EMAIL_HOST_USER=", bool(settings.EMAIL_HOST_USER))
print("DEFAULT_FROM_EMAIL=", settings.DEFAULT_FROM_EMAIL)
