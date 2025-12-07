from billing.models import DiscountCode, Subscription
from accounts.models import Membership, Business
import json
out=[]
for d in DiscountCode.objects.all():
    users=[{'id':u.id,'username':u.username} for u in d.users.all()]
    org_ids=set(Membership.objects.filter(user__in=d.users.all()).values_list('organization_id',flat=True))
    orgs=[]
    for oid in org_ids:
        try:
            org=Business.objects.get(id=oid)
            subs=[{'id':s.id,'stripe':s.stripe_subscription_id} for s in Subscription.objects.filter(organization=org)]
            orgs.append({'id':org.id,'name':org.name,'subs':subs})
        except Exception:
            pass
    out.append({'discount':d.code,'id':d.id,'users':users,'orgs':orgs})
print(json.dumps(out, indent=2))
