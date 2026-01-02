from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('bookings', '0019_booking_offline_payment_method'),
    ]

    operations = [
        migrations.AddField(
            model_name='orgsettings',
            name='offline_venmo',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='orgsettings',
            name='offline_zelle',
            field=models.TextField(blank=True, default=''),
        ),
    ]
