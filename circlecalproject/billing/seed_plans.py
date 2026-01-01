# Seed initial plan data for CircleCal
# Run with: python manage.py shell < billing/seed_plans.py

from billing.models import Plan

plans_data = [
    {
        "name": "Basic",
        "slug": "basic",
        "description": "Basic booking calendar for solo coaches",
        "price": 9.99,
        "billing_period": "monthly",
        "max_coaches": 1,
        "max_services": 1,
        "max_bookings_per_month": 100,
        "allow_custom_branding": False,
        "allow_priority_support": False,
        "allow_payment_processing": True,
        "stripe_price_id": "price_1SZhx0ILdL6xY1r90SeZx55m",  # Set this after creating Stripe price
        "is_active": True,
    },
    {
        "name": "Pro",
        "slug": "pro",
        "description": "Full calendar + advanced features for growing businesses",
        "price": 19.99,
        "billing_period": "monthly",
        "max_coaches": 1,
        "max_services": 999,  # Unlimited
        "max_bookings_per_month": 999,
        "allow_custom_branding": True,
        "allow_priority_support": False,
        "allow_payment_processing": True,
        "stripe_price_id": "price_1SXDiKILdL6xY1r9MlcN7Ya9",  # Set this after creating Stripe price
        "is_active": True,
    },
    {
        "name": "Team",
        "slug": "team",
        "description": "Multiple staff accounts + teams for enterprise",
        "price": 49.99,
        "billing_period": "monthly",
        "max_coaches": 999,  # Unlimited
        "max_services": 999,
        "max_bookings_per_month": 9999,
        "allow_custom_branding": True,
        "allow_priority_support": True,
        "allow_payment_processing": True,
        "stripe_price_id": "price_1SXDx1ILdL6xY1r9ffZX4Y1P",  # Set this after creating Stripe price
        "is_active": True,
    },
]

for plan_data in plans_data:
    Plan.objects.update_or_create(
        slug=plan_data["slug"],
        defaults=plan_data
    )
    print(f"✓ Created/Updated plan: {plan_data['name']}")

print("\n✅ Plan seeding complete. Next: add stripe_price_id values in Django admin after creating products in Stripe.")
