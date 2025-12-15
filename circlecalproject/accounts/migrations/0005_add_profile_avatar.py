from django.db import migrations, models


class Migration(migrations.Migration):

    # This migration was squashed into 0001_initial_squashed_0002_alter_business_owner_alter_business_timezone_and_more
    # which already creates the `avatar` column. Keeping this file as a no-op to avoid duplicate-column errors
    # when creating test databases.
    dependencies = [
        ('accounts', '0004_rename_organization_to_business'),
    ]

    operations = [
        # No-op: field already present in the squashed initial migration.
    ]
