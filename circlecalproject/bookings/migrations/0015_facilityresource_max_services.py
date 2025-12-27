from django.db import migrations, models
import django.core.validators


class Migration(migrations.Migration):

    dependencies = [
        (
            "bookings",
            "0014_rename_bookings_fa_organiza_d55d7a_idx_bookings_fa_organiz_59f1ef_idx_and_more",
        ),
    ]

    operations = [
        migrations.AddField(
            model_name="facilityresource",
            name="max_services",
            field=models.IntegerField(
                default=1,
                validators=[django.core.validators.MinValueValidator(0)],
                help_text="Maximum number of services that can link to this resource (0 = unlimited).",
            ),
        ),
    ]
