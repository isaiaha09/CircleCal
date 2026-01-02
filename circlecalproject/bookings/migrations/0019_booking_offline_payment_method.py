from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('bookings', '0018_service_payment_method_controls'),
    ]

    operations = [
        migrations.AddField(
            model_name='booking',
            name='offline_payment_method',
            field=models.CharField(blank=True, db_index=True, default='', max_length=20),
        ),
    ]
