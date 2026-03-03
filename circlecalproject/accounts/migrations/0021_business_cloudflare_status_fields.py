from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0020_business_custom_domain_cloudflare_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="business",
            name="custom_domain_cloudflare_dcv_records",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="business",
            name="custom_domain_cloudflare_last_checked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="business",
            name="custom_domain_cloudflare_last_error",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="business",
            name="custom_domain_cloudflare_ssl_status",
            field=models.CharField(blank=True, db_index=True, max_length=32, null=True),
        ),
    ]
