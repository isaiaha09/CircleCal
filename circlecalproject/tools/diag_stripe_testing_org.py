# Run with: python manage.py shell < tools/diag_stripe_testing_org.py
from django.conf import settings
import stripe, pprint, sys
from accounts.models import Business
from billing.models import Subscription

pp = pprint.PrettyPrinter(indent=2)
stripe.api_key = settings.STRIPE_SECRET_KEY

ORG_SLUG = "testing-orgy"  # change if needed

print('Starting Stripe diagnostic for org slug:', ORG_SLUG)
print('---')
try:
    org = Business.objects.get(slug=ORG_SLUG)
except Business.DoesNotExist:
    print(f"Organization with slug '{ORG_SLUG}' not found.")
    sys.exit(1)

print('Org id:', org.id)
print('Org stripe_customer_id:', org.stripe_customer_id)
subs = getattr(org, 'subscription', None)
print('Local subscription exists?:', bool(subs))
if subs:
    print('Local subscription.stripe_subscription_id:', subs.stripe_subscription_id)
    print('Local subscription.status:', subs.status)
    print('Local subscription.current_period_end:', subs.current_period_end)
    print('Local subscription.cancel_at_period_end:', subs.cancel_at_period_end)

print('\n--- Stripe subscription (retrieve) ---')
if subs and subs.stripe_subscription_id:
    try:
        stripe_sub = stripe.Subscription.retrieve(subs.stripe_subscription_id, expand=['items.data.price'])
        pp.pprint(stripe_sub)
    except Exception as e:
        print('Error retrieving Stripe subscription:', e)
else:
    print('No stripe_subscription_id on local subscription')

print('\n--- Upcoming invoice (Stripe.Invoice.upcoming) ---')
try:
    # Newer stripe client libs use `create_preview` instead of `upcoming`
    ui = getattr(stripe.Invoice, 'upcoming', None)
    if callable(ui):
        ui = ui(customer=org.stripe_customer_id)
    else:
        ui = stripe.Invoice.create_preview(customer=org.stripe_customer_id)
    pp.pprint(ui)
except Exception as e:
    print('Upcoming invoice error:', e)

print('\n--- Recent invoices (limit 10) ---')
try:
    invs = stripe.Invoice.list(customer=org.stripe_customer_id, limit=10)
    for inv in invs.auto_paging_iter():
        print('\nInvoice id:', inv.id, 'status:', inv.status, 'amount_due:', inv.amount_due, 'amount_paid:', inv.amount_paid, 'currency:', inv.currency)
        # Show invoice.lines summary
        try:
            lines = inv.get('lines', {}).get('data', [])
            for ln in lines:
                print('  - line:', ln.get('description') or ln.get('plan', {}).get('nickname'), 'amt:', ln.get('amount'), 'proration:', ln.get('proration'), 'billing_reason:', ln.get('billing_reason'))
        except Exception:
            pass
        # Expand payment_intent/charges if present
        try:
            if inv.get('payment_intent'):
                pi = stripe.PaymentIntent.retrieve(inv['payment_intent'])
                chs = pi.get('charges', {}).get('data', [])
                print('  payment_intent:', pi.id, 'status:', pi.status)
                if chs:
                    ch = chs[0]
                    print('    charge id:', ch.get('id'), 'amount:', ch.get('amount'), 'paid:', ch.get('paid'))
                    pm = ch.get('payment_method_details', {}).get('card')
                    if pm:
                        print('    card:', pm.get('brand'), pm.get('last4'))
        except Exception as e:
            print('  payment_intent expand error:', e)
except Exception as e:
    print('Recent invoices error:', e)

print('\n--- InvoiceItems / Pending invoice items (limit 50) ---')
try:
    items = stripe.InvoiceItem.list(customer=org.stripe_customer_id, limit=50)
    for it in items.auto_paging_iter():
        print('InvoiceItem id:', it.id, 'amount:', it.amount, 'currency:', it.currency, 'description:', it.description, 'invoice:', it.invoice, 'pending_invoice_item_amount:', it.get('pending', False))
except Exception as e:
    print('InvoiceItem list error:', e)

print('\n--- Done ---')
print('If you want, paste this output back into the chat.')
