import os, sys
# Ensure project root is on PYTHONPATH
proj_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if proj_root not in sys.path:
    sys.path.insert(0, proj_root)
os.environ.setdefault('DJANGO_SETTINGS_MODULE','circlecalproject.settings')
import django
django.setup()
from billing.models import DiscountCode, Subscription
from accounts.models import Membership
from billing.admin import SubscriptionWithUserChoiceField

code_id = 1
try:
    dc = DiscountCode.objects.get(id=code_id)
    users = list(dc.users.all())
    print('users:', [(u.id, u.username) for u in users])
    org_ids = set(Membership.objects.filter(user__in=users).values_list('organization_id', flat=True))
    print('org_ids:', org_ids)
    subs = list(Subscription.objects.filter(organization_id__in=org_ids).select_related('organization','plan'))
    print('subs:', [(s.id, getattr(s.organization,'id',None), getattr(s.organization,'name',None)) for s in subs])
    f = SubscriptionWithUserChoiceField(queryset=Subscription.objects.none())
    labels = [f.label_from_instance(s) for s in subs]
    print('labels:', labels)
except Exception as e:
    import traceback
    traceback.print_exc()
    print('error', e)
