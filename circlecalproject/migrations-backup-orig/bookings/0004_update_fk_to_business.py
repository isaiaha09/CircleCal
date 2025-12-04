from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0004_rename_organization_to_business'),
        ('bookings', '0003_weeklyavailability'),
    ]

    operations = [
        migrations.AlterField(
            model_name='service',
            name='organization',
            field=models.ForeignKey(to='accounts.Business', on_delete=models.deletion.CASCADE, related_name='services'),
        ),
        migrations.AlterField(
            model_name='booking',
            name='organization',
            field=models.ForeignKey(to='accounts.Business', on_delete=models.deletion.CASCADE, related_name='bookings'),
        ),
        migrations.AlterField(
            model_name='orgsettings',
            name='organization',
            field=models.OneToOneField(to='accounts.Business', on_delete=models.deletion.CASCADE, related_name='settings'),
        ),
        migrations.AlterField(
            model_name='weeklyavailability',
            name='organization',
            field=models.ForeignKey(to='accounts.Business', on_delete=models.deletion.CASCADE, related_name='weekly_availability'),
        ),
    ]
