from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0012_business_embed_widget"),
    ]

    operations = [
        migrations.AddField(
            model_name="business",
            name="custom_domain",
            field=models.CharField(blank=True, db_index=True, max_length=255, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="business",
            name="custom_domain_verified",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="business",
            name="custom_domain_verified_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="business",
            name="custom_domain_verification_token",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
    ]
