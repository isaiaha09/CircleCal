from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0014_business_slug_redirect"),
    ]

    operations = [
        migrations.AddField(
            model_name="profile",
            name="avatar_updated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
