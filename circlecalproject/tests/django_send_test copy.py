import os, traceback
os.environ.setdefault('DJANGO_SETTINGS_MODULE','circlecalproject.settings_prod')
import django
django.setup()
from django.core.mail import send_mail
try:
    sent = send_mail(
        'CircleCal Django test',
        'This is a test from Django send_mail()',
        'CircleCal <noreply@circlecal.app>',
        ['you@example.com'],  # <-- replace with an address you control
        fail_silently=False,
    )
    print('send_mail returned:', sent)
except Exception:
    traceback.print_exc()
