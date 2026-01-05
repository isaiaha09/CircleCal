from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0011_profile_scheduled_deletion"),
    ]

    operations = [
        migrations.AddField(
            model_name="business",
            name="embed_enabled",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="business",
            name="embed_key",
            field=models.CharField(blank=True, db_index=True, max_length=64, null=True),
        ),
    ]
