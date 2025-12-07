import os, sys
proj_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if proj_root not in sys.path:
    sys.path.insert(0, proj_root)
os.environ.setdefault('DJANGO_SETTINGS_MODULE','circlecalproject.settings')
import django
django.setup()
from django.test import RequestFactory
from django.contrib.auth import get_user_model
from billing.admin import AppliedDiscountAdmin
from billing.models import AppliedDiscount, DiscountCode
from django.contrib import admin

User = get_user_model()
# pick an admin user (superuser or staff) to simulate
user = User.objects.filter(is_superuser=True).first() or User.objects.filter(is_staff=True).first()
if not user:
    print('No admin user found; aborting')
    sys.exit(1)

factory = RequestFactory()
req = factory.get('/admin/billing/applieddiscount/subscriptions-for-code/?code_id=1')
req.user = user
# instantiate admin
adm = AppliedDiscountAdmin(AppliedDiscount, admin.site)
resp = adm.subscriptions_for_code(req)
print('status:', getattr(resp, 'status_code', None))
print('content:', resp.content)
