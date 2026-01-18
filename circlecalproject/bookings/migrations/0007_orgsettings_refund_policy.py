from django.db import migrations, models


def ensure_orgsettings_refund_fields(apps, schema_editor):
    OrgSettings = apps.get_model('bookings', 'OrgSettings')
    table = OrgSettings._meta.db_table

    with schema_editor.connection.cursor() as cursor:
        existing_cols = {
            col.name
            for col in schema_editor.connection.introspection.get_table_description(cursor, table)
        }

    desired_fields = [
        ('org_refunds_allowed', models.BooleanField(default=True)),
        ('org_refund_cutoff_hours', models.PositiveIntegerField(default=24)),
        ('org_refund_policy_text', models.TextField(blank=True)),
    ]

    for name, field in desired_fields:
        if name in existing_cols:
            continue
        field.set_attributes_from_name(name)
        schema_editor.add_field(OrgSettings, field)

class Migration(migrations.Migration):
    dependencies = [
        ('bookings', '0006_merge_0004_0005'),
    ]

    operations = [
        migrations.RunPython(ensure_orgsettings_refund_fields, migrations.RunPython.noop),
    ]
