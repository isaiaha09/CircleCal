from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0019_rename_accounts_mo_user_id_31c7b0_idx_accounts_mo_user_id_a1e2e6_idx_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="business",
            name="custom_domain_cloudflare_id",
            field=models.CharField(blank=True, db_index=True, max_length=64, null=True),
        ),
    ]
