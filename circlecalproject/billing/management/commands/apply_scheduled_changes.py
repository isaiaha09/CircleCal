from django.core.management.base import BaseCommand
from django.utils import timezone
from django.conf import settings
import stripe
from billing.models import Subscription

stripe.api_key = settings.STRIPE_SECRET_KEY

class Command(BaseCommand):
    help = 'Apply scheduled subscription changes (downgrades) whose scheduled_change_at <= now.'

    def handle(self, *args, **options):
        now = timezone.now()
        subs = Subscription.objects.filter(scheduled_plan__isnull=False).filter(
            scheduled_change_at__lte=now
        )
        if not subs.exists():
            self.stdout.write('No scheduled changes to apply.')
            return

        for sub in subs:
            org = sub.organization
            target_plan = sub.scheduled_plan
            self.stdout.write(f'Applying scheduled change for {org.slug} -> {target_plan.slug}')
            if not org.stripe_customer_id or not sub.stripe_subscription_id:
                self.stdout.write(f'  Skipping: missing stripe customer or subscription id for {org.slug}')
                continue

            try:
                # Modify subscription on Stripe: set the new price item without prorations
                stripe_sub = stripe.Subscription.modify(
                    sub.stripe_subscription_id,
                    items=[{"price": target_plan.stripe_price_id}],
                    proration_behavior='none',
                )

                # Update local subscription
                sub.plan = target_plan
                sub.status = stripe_sub.get('status', sub.status)
                sub.active = (sub.status == 'active' or sub.status == 'trialing')
                if stripe_sub.get('current_period_end'):
                    from datetime import datetime
                    sub.current_period_end = timezone.make_aware(datetime.fromtimestamp(stripe_sub['current_period_end']))
                sub.scheduled_plan = None
                sub.scheduled_change_at = None
                sub.save()
                self.stdout.write(f'  Applied scheduled change for {org.slug} to {target_plan.slug}')
            except Exception as e:
                self.stderr.write(f'  Failed to apply scheduled change for {org.slug}: {e}')
