# Backup of accounts/migrations/0004_rename_organization_to_business.py
from django.db import migrations

class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0003_organization_timezone'),
    ]

    operations = [
        migrations.RenameModel(
            old_name='Organization',
            new_name='Business',
        ),
    ]
