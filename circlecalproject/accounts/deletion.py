from django.utils import timezone


def delete_due_trial_accounts(*, limit: int = 200, dry_run: bool = False):
    """Deactivate accounts scheduled for deactivation at/after trial end.

    This is the shared implementation used by both the management command and
    the opportunistic middleware runner.

    Returns: dict with counts.
    """
    from accounts.models import Profile, Business
    from accounts.emails import send_account_deactivated_email

    now = timezone.now()

    qs = Profile.objects.select_related('user').filter(
        scheduled_account_deletion_reason='trial_cancel_at_period_end',
        scheduled_account_deletion_at__isnull=False,
        scheduled_account_deletion_at__lte=now,
    ).order_by('scheduled_account_deletion_at')

    if limit:
        qs = qs[: int(limit)]

    deactivated_count = 0
    skipped_count = 0

    for profile in qs:
        user = getattr(profile, 'user', None)
        if not user:
            continue

        owned_orgs = list(Business.objects.filter(owner=user))

        # If user has reactivated/resubscribed (active + not canceling), clear schedule.
        has_active_sub = False
        for org in owned_orgs:
            sub = getattr(org, 'subscription', None)
            if not sub:
                continue
            if getattr(sub, 'active', False) and getattr(sub, 'status', None) in ('active', 'trialing') and not getattr(sub, 'cancel_at_period_end', False):
                has_active_sub = True
                break

        if has_active_sub:
            skipped_count += 1
            if not dry_run:
                profile.scheduled_account_deletion_at = None
                profile.scheduled_account_deletion_reason = None
                profile.save(update_fields=['scheduled_account_deletion_at', 'scheduled_account_deletion_reason'])
            continue

        business_names = [getattr(b, 'name', '') for b in owned_orgs if getattr(b, 'name', '')]

        if dry_run:
            deactivated_count += 1
            continue

        # Disconnect Stripe in CircleCal for owned orgs
        for b in owned_orgs:
            try:
                if getattr(b, 'stripe_connect_account_id', None):
                    b.stripe_connect_account_id = None
                    b.stripe_connect_details_submitted = False
                    b.stripe_connect_charges_enabled = False
                    b.stripe_connect_payouts_enabled = False
                    b.save(update_fields=[
                        'stripe_connect_account_id',
                        'stripe_connect_details_submitted',
                        'stripe_connect_charges_enabled',
                        'stripe_connect_payouts_enabled',
                    ])
            except Exception:
                pass

        # Email confirmation before deactivating
        try:
            send_account_deactivated_email(user, business_names=business_names)
        except Exception:
            pass

        # Deactivate user and archive owned orgs (soft-disable access) instead of deleting.
        try:
            user.is_active = False
            user.save(update_fields=['is_active'])
        except Exception:
            pass

        for b in owned_orgs:
            try:
                if hasattr(b, 'is_archived'):
                    b.is_archived = True
                    b.save(update_fields=['is_archived'])
            except Exception:
                pass

        # Clear schedule so we don't repeatedly process the same profile.
        try:
            profile.scheduled_account_deletion_at = None
            profile.scheduled_account_deletion_reason = None
            profile.save(update_fields=['scheduled_account_deletion_at', 'scheduled_account_deletion_reason'])
        except Exception:
            pass

        deactivated_count += 1

    return {
        'deactivated': deactivated_count,
        'skipped': skipped_count,
    }
