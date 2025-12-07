import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'circlecalproject.settings')
import django
django.setup()
from django.conf import settings
print('ENV ADMIN_PIN:', repr(settings.ADMIN_PIN))
try:
    from calendar_app.models import AdminPin
    latest = AdminPin.objects.order_by('-id').first()
    if latest:
        print('DB PIN exists. Last set:', latest.created_at)
    else:
        print('No DB PIN found.')
except Exception as e:
    print('Error checking DB PIN:', e)
