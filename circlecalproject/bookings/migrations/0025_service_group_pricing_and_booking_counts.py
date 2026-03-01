from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('bookings', '0024_rename_owner_receives_staff_booking_push_notifications_enabled'),
    ]

    operations = [
        migrations.AddField(
            model_name='service',
            name='group_pricing',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='service',
            name='max_participants',
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name='booking',
            name='participant_count',
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name='booking',
            name='total_price',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=8),
        ),
        migrations.AddField(
            model_name='publicbookingintent',
            name='participant_count',
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name='publicbookingintent',
            name='total_price',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=8),
        ),
    ]
