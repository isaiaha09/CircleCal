from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0001_initial'),
        ('billing', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='subscription',
            name='organization',
            field=models.OneToOneField(to='accounts.Business', on_delete=models.deletion.CASCADE, related_name='subscription'),
        ),
    ]