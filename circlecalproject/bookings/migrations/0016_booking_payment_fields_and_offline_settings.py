from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bookings", "0015_facilityresource_max_services"),
    ]

    operations = [
        migrations.AddField(
            model_name="booking",
            name="payment_method",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="booking",
            name="payment_status",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="booking",
            name="stripe_checkout_session_id",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="booking",
            name="rescheduled_from_booking_id",
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="orgsettings",
            name="offline_payment_methods",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="orgsettings",
            name="offline_payment_instructions",
            field=models.TextField(blank=True, default=""),
        ),
    ]
