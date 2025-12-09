from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('bookings', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='service',
            name='allow_ends_after_availability',
            field=models.BooleanField(default=False),
        ),
    ]
