from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0010_business_stripe_connect_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='scheduled_account_deletion_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='profile',
            name='scheduled_account_deletion_reason',
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
    ]
