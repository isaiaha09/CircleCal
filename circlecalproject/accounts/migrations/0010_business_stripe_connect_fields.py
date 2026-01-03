from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0009_add_profile_display_name"),
    ]

    operations = [
        migrations.AddField(
            model_name="business",
            name="stripe_connect_account_id",
            field=models.CharField(blank=True, db_index=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name="business",
            name="stripe_connect_details_submitted",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="business",
            name="stripe_connect_charges_enabled",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="business",
            name="stripe_connect_payouts_enabled",
            field=models.BooleanField(default=False),
        ),
    ]
