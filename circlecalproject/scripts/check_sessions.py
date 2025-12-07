import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE','circlecalproject.settings')
import django
django.setup()
from django.contrib.sessions.models import Session
from django.utils import timezone

now = timezone.now()
print('Checking sessions for admin_pin_ok...')
found = 0
for s in Session.objects.all():
    try:
        data = s.get_decoded()
    except Exception as e:
        data = {}
    if data.get('admin_pin_ok'):
        found += 1
        print('Session:', s.session_key, 'expires:', s.expire_date, 'admin_pin_ok:', data.get('admin_pin_ok'))

if found == 0:
    print('No sessions with admin_pin_ok found.')
else:
    print(f'Found {found} session(s) with admin_pin_ok flag.')
