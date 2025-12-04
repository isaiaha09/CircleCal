from django.db import migrations

class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0003_organization_timezone'),
    ]

    operations = [
        # No-op: project already uses `Business` and DB tables exist.
        # This migration used to rename Organization->Business; we've
        # reconstructed the initial migration to define Business directly,
        # so keep this migration as a no-op to preserve historical ordering.
    ]
