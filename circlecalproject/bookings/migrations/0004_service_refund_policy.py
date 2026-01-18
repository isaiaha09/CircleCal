from django.db import migrations, models


def ensure_service_refund_fields(apps, schema_editor):
    Service = apps.get_model('bookings', 'Service')
    table = Service._meta.db_table

    with schema_editor.connection.cursor() as cursor:
        existing_cols = {
            col.name
            for col in schema_editor.connection.introspection.get_table_description(cursor, table)
        }

    # These fields ended up duplicated between the initial migration and this migration
    # in some environments. Ensure they exist without failing if they already do.
    desired_fields = [
        (
            'refunds_allowed',
            models.BooleanField(
                default=True,
                help_text='Whether clients can receive refunds on cancellation.',
            ),
        ),
        (
            'refund_cutoff_hours',
            models.PositiveIntegerField(
                default=24,
                help_text='Hours before start time within which refunds are NOT permitted.',
            ),
        ),
        (
            'refund_policy_text',
            models.TextField(
                blank=True,
                help_text='Optional custom refund policy text shown to clients.',
            ),
        ),
    ]

    for name, field in desired_fields:
        if name in existing_cols:
            continue
        field.set_attributes_from_name(name)
        schema_editor.add_field(Service, field)

class Migration(migrations.Migration):

    dependencies = [
        ('bookings', '0003_weeklyavailability'),
    ]

    operations = [
        migrations.RunPython(ensure_service_refund_fields, migrations.RunPython.noop),
    ]
