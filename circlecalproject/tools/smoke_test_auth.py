import os, sys, pathlib
sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'circlecalproject.settings')
import django
django.setup()

from django.test import Client
from django.contrib.auth import get_user_model
from accounts.models import Business, Membership
from bookings.models import Service
from django.utils.text import slugify

User = get_user_model()

client = Client()

USERNAME = 'smoke_user'
PASSWORD = 'password123'

# Create user if missing
user, created = User.objects.get_or_create(username=USERNAME, defaults={'email': 'smoke@example.com'})
if created:
    user.set_password(PASSWORD)
    user.save()

# Ensure a business exists
org = Business.objects.first()
if not org:
    org = Business.objects.create(name='Testing Orgy', slug='testing-orgy', owner=user)

# Ensure membership: make smoke_user owner
Membership.objects.update_or_create(user=user, organization=org, defaults={'role':'owner','is_active':True})

# Ensure a service exists for the org
svc = Service.objects.filter(organization=org, is_active=True).first()
if not svc:
    svc = Service.objects.create(organization=org, name='Smoke Service', slug='smoke-service', duration=30)

# Login (use force_login to bypass custom auth backends that expect a request)
client.force_login(user)

results = []

# Access services page (owner)
resp = client.get(f'/bus/{org.slug}/services/', HTTP_HOST='127.0.0.1')
results.append((f'GET /bus/{org.slug}/services/', resp.status_code))

# Access edit_service page
resp = client.get(f'/bus/{org.slug}/services/{svc.id}/edit/', HTTP_HOST='127.0.0.1')
results.append((f'GET edit_service ({svc.id})', resp.status_code))

# POST update service (simulate changing name)
resp = client.post(f'/bus/{org.slug}/services/{svc.id}/edit/', {'name': svc.name, 'slug': svc.slug, 'duration': svc.duration}, HTTP_HOST='127.0.0.1')
results.append((f'POST edit_service ({svc.id})', resp.status_code))

# Access create_service page
resp = client.get(f'/bus/{org.slug}/services/create/', HTTP_HOST='127.0.0.1')
results.append((f'GET create_service', resp.status_code))

# POST create a new service
new_slug = slugify('Smoke Created Service')
resp = client.post(f'/bus/{org.slug}/services/create/', {'name':'Smoke Created Service', 'slug': new_slug, 'duration':30}, HTTP_HOST='127.0.0.1')
results.append((f'POST create_service', resp.status_code))

# Save availability via org endpoint
payload = {
    'availability': [
        {'day': 1, 'ranges': ['09:00-12:00'], 'unavailable': False},
        {'day': 2, 'ranges': ['09:00-12:00'], 'unavailable': False},
    ]
}
import json
resp = client.post(f'/bus/{org.slug}/availability/save/', json.dumps(payload), content_type='application/json', HTTP_HOST='127.0.0.1')
results.append((f'POST save_availability', resp.status_code))

# Dashboard
resp = client.get(f'/bus/{org.slug}/dashboard/', HTTP_HOST='127.0.0.1')
results.append((f'GET dashboard', resp.status_code))

for r in results:
    print(r)
