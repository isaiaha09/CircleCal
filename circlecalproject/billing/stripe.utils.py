import stripe
from django.conf import settings

stripe.api_key = settings.STRIPE_SECRET_KEY

def create_customer(email):
    return stripe.Customer.create(email=email)