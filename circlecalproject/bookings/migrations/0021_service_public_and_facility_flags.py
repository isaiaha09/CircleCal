from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bookings", "0020_orgsettings_offline_method_info"),
    ]

    operations = [
        migrations.AddField(
            model_name="service",
            name="show_on_public_calendar",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="service",
            name="requires_facility_resources",
            field=models.BooleanField(default=False),
        ),
    ]
