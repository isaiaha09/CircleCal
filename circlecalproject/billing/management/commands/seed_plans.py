from __future__ import annotations

import os

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        "Seed default billing plans (idempotent). "
        "Designed for hosts that don't allow interactive shell access (e.g., Render free plan)."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--force",
            action="store_true",
            help="Run even if DJANGO_SEED_PLANS is not set to '1'.",
        )
        parser.add_argument(
            "--update-existing",
            action="store_true",
            help=(
                "Update existing plan fields (name/price/limits/etc) to match the defaults in code. "
                "By default, existing plans are left as-is (only missing stripe ids may be filled)."
            ),
        )
        parser.add_argument(
            "--overwrite-stripe-ids",
            action="store_true",
            help=(
                "Overwrite existing Plan.stripe_price_id values. "
                "By default, stripe ids are only populated when missing."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print actions without writing to the database.",
        )

    def handle(self, *args, **options) -> None:
        enabled = os.getenv("DJANGO_SEED_PLANS", "").strip() == "1"
        if not enabled and not options.get("force"):
            self.stdout.write("seed_plans: disabled (set DJANGO_SEED_PLANS=1)")
            return

        dry_run = bool(options.get("dry_run"))
        update_existing = bool(options.get("update_existing"))
        overwrite_stripe_ids = bool(options.get("overwrite_stripe_ids"))

        # Import lazily to ensure Django is set up.
        from billing.models import Plan

        def _stripe_id_for(slug: str, default: str | None) -> str | None:
            env_key = f"STRIPE_PRICE_ID_{slug.upper()}"
            val = (os.getenv(env_key) or "").strip()
            return val or default

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
                "stripe_price_id": _stripe_id_for("basic", "price_1SZhx0ILdL6xY1r90SeZx55m"),
                "is_active": True,
            },
            {
                "name": "Pro",
                "slug": "pro",
                "description": "Full calendar + advanced features for growing businesses",
                "price": 19.99,
                "billing_period": "monthly",
                "max_coaches": 1,
                "max_services": 999,
                "max_bookings_per_month": 999,
                "allow_custom_branding": True,
                "allow_priority_support": False,
                "allow_payment_processing": True,
                "stripe_price_id": _stripe_id_for("pro", "price_1SXDiKILdL6xY1r9MlcN7Ya9"),
                "is_active": True,
            },
            {
                "name": "Team",
                "slug": "team",
                "description": "Multiple staff accounts + teams for enterprise",
                "price": 49.99,
                "billing_period": "monthly",
                "max_coaches": 999,
                "max_services": 999,
                "max_bookings_per_month": 9999,
                "allow_custom_branding": True,
                "allow_priority_support": True,
                "allow_payment_processing": True,
                "stripe_price_id": _stripe_id_for("team", "price_1SXDx1ILdL6xY1r9ffZX4Y1P"),
                "is_active": True,
            },
        ]

        created_count = 0
        updated_count = 0
        skipped_update_count = 0
        stripe_set_count = 0

        for plan_data in plans_data:
            slug = plan_data["slug"]

            # By default we do NOT overwrite stripe ids (so you can set them via admin safely).
            desired_stripe_id = plan_data.pop("stripe_price_id", None)

            if dry_run:
                exists = Plan.objects.filter(slug=slug).exists()
                self.stdout.write(
                    f"seed_plans: would {'create' if not exists else ('update' if update_existing else 'no-change')} plan slug={slug!r} "
                    f"(stripe_price_id={'overwrite' if overwrite_stripe_ids else 'fill-if-missing'})"
                )
                continue

            obj = Plan.objects.filter(slug=slug).first()
            created = False

            if obj is None:
                obj = Plan.objects.create(**plan_data, stripe_price_id=desired_stripe_id or None)
                created = True
                created_count += 1
            else:
                if update_existing:
                    for k, v in plan_data.items():
                        setattr(obj, k, v)
                    obj.save()
                    updated_count += 1
                else:
                    skipped_update_count += 1

            # Stripe ids: fill only if missing, unless explicitly told to overwrite.
            if overwrite_stripe_ids:
                if obj.stripe_price_id != desired_stripe_id:
                    obj.stripe_price_id = desired_stripe_id
                    obj.save(update_fields=["stripe_price_id"])
                    stripe_set_count += 1
            else:
                if (not obj.stripe_price_id) and desired_stripe_id:
                    obj.stripe_price_id = desired_stripe_id
                    obj.save(update_fields=["stripe_price_id"])
                    stripe_set_count += 1

            self.stdout.write(f"✓ Seeded plan: {obj.name} (slug={obj.slug})")

        if dry_run:
            self.stdout.write("seed_plans: dry-run complete")
            return

        self.stdout.write(
            f"seed_plans: done (created={created_count} updated={updated_count} skipped_updates={skipped_update_count} stripe_ids_set={stripe_set_count}). "
            "Recommended: set DJANGO_SEED_PLANS=0 after verification."
        )
