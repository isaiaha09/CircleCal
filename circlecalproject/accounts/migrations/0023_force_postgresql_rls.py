from django.db import migrations


RLS_TABLES = [
    'accounts_membership',
    'accounts_invite',
    'accounts_team',
    'accounts_teammembership',
    'bookings_service',
    'bookings_facilityresource',
    'bookings_serviceresource',
    'bookings_weeklyavailability',
    'bookings_serviceweeklyavailability',
    'bookings_memberweeklyavailability',
    'bookings_servicesettingfreeze',
    'bookings_serviceassignment',
    'bookings_booking',
    'bookings_publicbookingintent',
    'bookings_orgsettings',
    'bookings_auditbooking',
    'billing_subscription',
    'billing_paymentmethod',
    'billing_invoicemeta',
    'billing_invoiceactionlog',
    'billing_subscriptionchange',
    'billing_applieddiscount',
]


def _apply_sql(schema_editor, statements):
    if schema_editor.connection.vendor != 'postgresql':
        return

    with schema_editor.connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)


def apply_force_rls(apps, schema_editor):
    _apply_sql(
        schema_editor,
        [f"ALTER TABLE public.{table_name} FORCE ROW LEVEL SECURITY;" for table_name in RLS_TABLES],
    )


def unapply_force_rls(apps, schema_editor):
    _apply_sql(
        schema_editor,
        [f"ALTER TABLE public.{table_name} NO FORCE ROW LEVEL SECURITY;" for table_name in reversed(RLS_TABLES)],
    )


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0022_postgresql_rls'),
    ]

    operations = [
        migrations.RunPython(apply_force_rls, unapply_force_rls),
    ]