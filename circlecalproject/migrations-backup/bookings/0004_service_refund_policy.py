from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ('bookings', '0003_weeklyavailability'),
    ]

    operations = [
        migrations.AddField(
            model_name='service',
            name='refunds_allowed',
            field=models.BooleanField(default=True, help_text='Whether clients can receive refunds on cancellation.'),
        ),
        migrations.AddField(
            model_name='service',
            name='refund_cutoff_hours',
            field=models.PositiveIntegerField(default=24, help_text='Hours before start time within which refunds are NOT permitted.'),
        ),
        migrations.AddField(
            model_name='service',
            name='refund_policy_text',
            field=models.TextField(blank=True, help_text='Optional custom refund policy text shown to clients.'),
        ),
    ]
