from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0017_mobile_sso_token'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='push_booking_notifications_enabled',
            field=models.BooleanField(default=True),
        ),
    ]
