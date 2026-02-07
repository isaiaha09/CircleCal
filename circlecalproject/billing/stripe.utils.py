import stripe
from django.conf import settings

stripe.api_key = settings.STRIPE_SECRET_KEY

def create_customer(email, name=None, metadata=None):
    kwargs = {"email": email}
    if name:
        kwargs["name"] = name
    if metadata:
        kwargs["metadata"] = metadata
    return stripe.Customer.create(**kwargs)