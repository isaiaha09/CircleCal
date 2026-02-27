from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0009_applieddiscount_source_user"),
    ]

    operations = [
        migrations.AddField(
            model_name="subscription",
            name="custom_domain_addon_enabled",
            field=models.BooleanField(default=False),
        ),
    ]
