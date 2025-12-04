# Generated (renumbered) migration to replace conflicting 0002_booking_organization
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('bookings', '0007_orgsettings_refund_policy'),
        ('accounts', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='booking',
            name='organization',
            field=models.ForeignKey(default=None, on_delete=django.db.models.deletion.CASCADE, related_name='bookings', to='accounts.Business'),
            preserve_default=False,
        ),
    ]
