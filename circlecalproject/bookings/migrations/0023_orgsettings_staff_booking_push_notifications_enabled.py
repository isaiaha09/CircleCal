from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('bookings', '0022_service_signature_updated_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='orgsettings',
            name='staff_booking_push_notifications_enabled',
            field=models.BooleanField(default=True),
        ),
    ]
