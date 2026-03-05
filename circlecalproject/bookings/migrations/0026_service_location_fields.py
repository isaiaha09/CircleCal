from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('bookings', '0025_service_group_pricing_and_booking_counts'),
    ]

    operations = [
        migrations.AddField(
            model_name='service',
            name='location_type',
            field=models.CharField(blank=True, choices=[('address', 'Address'), ('other', 'Other')], default='', max_length=16),
        ),
        migrations.AddField(
            model_name='service',
            name='location_full_address',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='service',
            name='location_other',
            field=models.TextField(blank=True, default=''),
        ),
    ]
