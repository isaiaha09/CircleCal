from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0004_rename_organization_to_business'),
    ]

    operations = [
        migrations.AddField(
            model_name='business',
            name='is_archived',
            field=models.BooleanField(default=False),
        ),
    ]
