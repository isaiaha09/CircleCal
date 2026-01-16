from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('bookings', '0021_service_public_and_facility_flags'),
    ]

    operations = [
        migrations.AddField(
            model_name='service',
            name='signature_updated_at',
            field=models.DateTimeField(default=django.utils.timezone.now),
        ),
    ]
