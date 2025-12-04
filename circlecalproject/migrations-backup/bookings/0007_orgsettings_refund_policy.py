from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ('bookings', '0006_merge_0004_0005'),
    ]

    operations = [
        migrations.AddField(
            model_name='orgsettings',
            name='org_refunds_allowed',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='orgsettings',
            name='org_refund_cutoff_hours',
            field=models.PositiveIntegerField(default=24),
        ),
        migrations.AddField(
            model_name='orgsettings',
            name='org_refund_policy_text',
            field=models.TextField(blank=True),
        ),
    ]
