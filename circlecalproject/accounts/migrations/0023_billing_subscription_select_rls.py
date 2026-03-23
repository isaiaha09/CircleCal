from django.db import migrations


FORWARD_SQL = [
    "DROP POLICY IF EXISTS cc_billing_subscription_manage ON public.billing_subscription;",
    "DROP POLICY IF EXISTS cc_billing_subscription_select ON public.billing_subscription;",
    "DROP POLICY IF EXISTS cc_billing_subscription_modify ON public.billing_subscription;",
    "CREATE POLICY cc_billing_subscription_select ON public.billing_subscription FOR SELECT USING (public.cc_can_read_org(organization_id));",
    "CREATE POLICY cc_billing_subscription_modify ON public.billing_subscription FOR ALL USING (public.cc_can_manage_org(organization_id)) WITH CHECK (public.cc_can_manage_org(organization_id));",
]


REVERSE_SQL = [
    "DROP POLICY IF EXISTS cc_billing_subscription_modify ON public.billing_subscription;",
    "DROP POLICY IF EXISTS cc_billing_subscription_select ON public.billing_subscription;",
    "CREATE POLICY cc_billing_subscription_manage ON public.billing_subscription FOR ALL USING (public.cc_can_manage_org(organization_id)) WITH CHECK (public.cc_can_manage_org(organization_id));",
]


def _apply_sql(schema_editor, statements):
    if schema_editor.connection.vendor != 'postgresql':
        return

    with schema_editor.connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)


def apply_forward(apps, schema_editor):
    _apply_sql(schema_editor, FORWARD_SQL)


def apply_reverse(apps, schema_editor):
    _apply_sql(schema_editor, REVERSE_SQL)


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0022_postgresql_rls'),
    ]

    operations = [
        migrations.RunPython(apply_forward, apply_reverse),
    ]