import os, sys, pathlib
sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'circlecalproject.settings')
import django
django.setup()

from django.test import Client
from bookings.models import Service
from accounts.models import Business

client = Client()
results = []

# 1) Home page (root)
resp = client.get('/', HTTP_HOST='127.0.0.1')
results.append(('GET /', resp.status_code))

# 2) Public org page for first org
org = Business.objects.first()
if org:
    resp = client.get(f'/bus/{org.slug}/', HTTP_HOST='127.0.0.1')
    results.append((f'GET /bus/{org.slug}/', resp.status_code))
else:
    results.append(('GET /bus/<none>','no business'))

# 3) Public service page for first service
svc = Service.objects.filter(is_active=True).first()
if svc:
    resp = client.get(f'/bus/{svc.organization.slug}/service/{svc.slug}/', HTTP_HOST='127.0.0.1')
    results.append((f'GET /bus/{svc.organization.slug}/service/{svc.slug}/', resp.status_code))
else:
    results.append(('GET /bus/.../service/...','no service'))

# 4) Booking create POST (simulate invalid POST to public service page -> should 400 or redirect)
if svc:
    resp = client.post(f'/bus/{svc.organization.slug}/service/{svc.slug}/', {'client_name':'', 'client_email':'', 'start':'', 'end':''}, HTTP_HOST='127.0.0.1')
    results.append((f'POST /bus/{svc.organization.slug}/service/{svc.slug}/ (invalid)', resp.status_code))

for r in results:
    print(r)
