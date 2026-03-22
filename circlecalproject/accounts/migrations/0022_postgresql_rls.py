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


FORWARD_SQL = [
    """
    CREATE OR REPLACE FUNCTION public.cc_current_user_id()
    RETURNS bigint
    LANGUAGE sql
    STABLE
    AS $$
        SELECT NULLIF(current_setting('circlecal.current_user_id', true), '')::bigint
    $$;
    """,
    """
    CREATE OR REPLACE FUNCTION public.cc_current_org_id()
    RETURNS bigint
    LANGUAGE sql
    STABLE
    AS $$
        SELECT NULLIF(current_setting('circlecal.current_org_id', true), '')::bigint
    $$;
    """,
    """
    CREATE OR REPLACE FUNCTION public.cc_rls_bypass()
    RETURNS boolean
    LANGUAGE sql
    STABLE
    AS $$
        SELECT COALESCE(current_setting('circlecal.rls_bypass', true), '0') = '1'
    $$;
    """,
    """
    CREATE OR REPLACE FUNCTION public.cc_auth_has_org_access(org_id bigint)
    RETURNS boolean
    LANGUAGE sql
    STABLE
    SECURITY DEFINER
    SET search_path = public
    AS $$
        SELECT
            public.cc_rls_bypass()
            OR (
                public.cc_current_user_id() IS NOT NULL
                AND (
                    EXISTS (
                        SELECT 1
                        FROM public.accounts_business b
                        WHERE b.id = org_id
                          AND b.owner_id = public.cc_current_user_id()
                    )
                    OR EXISTS (
                        SELECT 1
                        FROM public.accounts_membership m
                        WHERE m.organization_id = org_id
                          AND m.user_id = public.cc_current_user_id()
                          AND m.is_active
                    )
                )
            )
    $$;
    """,
    """
    CREATE OR REPLACE FUNCTION public.cc_can_read_org(org_id bigint)
    RETURNS boolean
    LANGUAGE sql
    STABLE
    SECURITY DEFINER
    SET search_path = public
    AS $$
        SELECT
            public.cc_auth_has_org_access(org_id)
            OR (
                public.cc_current_org_id() IS NOT NULL
                AND public.cc_current_org_id() = org_id
            )
    $$;
    """,
    """
    CREATE OR REPLACE FUNCTION public.cc_can_manage_org(org_id bigint)
    RETURNS boolean
    LANGUAGE sql
    STABLE
    SECURITY DEFINER
    SET search_path = public
    AS $$
        SELECT public.cc_auth_has_org_access(org_id)
    $$;
    """,
    "ALTER TABLE public.accounts_membership ENABLE ROW LEVEL SECURITY;",
    "DROP POLICY IF EXISTS cc_accounts_membership_select ON public.accounts_membership;",
    "CREATE POLICY cc_accounts_membership_select ON public.accounts_membership FOR SELECT USING (public.cc_can_read_org(organization_id));",
    "DROP POLICY IF EXISTS cc_accounts_membership_modify ON public.accounts_membership;",
    "CREATE POLICY cc_accounts_membership_modify ON public.accounts_membership FOR ALL USING (public.cc_can_manage_org(organization_id)) WITH CHECK (public.cc_can_manage_org(organization_id));",
    "ALTER TABLE public.accounts_invite ENABLE ROW LEVEL SECURITY;",
    "DROP POLICY IF EXISTS cc_accounts_invite_manage ON public.accounts_invite;",
    "CREATE POLICY cc_accounts_invite_manage ON public.accounts_invite FOR ALL USING (public.cc_can_manage_org(organization_id)) WITH CHECK (public.cc_can_manage_org(organization_id));",
    "ALTER TABLE public.accounts_team ENABLE ROW LEVEL SECURITY;",
    "DROP POLICY IF EXISTS cc_accounts_team_select ON public.accounts_team;",
    "CREATE POLICY cc_accounts_team_select ON public.accounts_team FOR SELECT USING (public.cc_can_read_org(organization_id));",
    "DROP POLICY IF EXISTS cc_accounts_team_modify ON public.accounts_team;",
    "CREATE POLICY cc_accounts_team_modify ON public.accounts_team FOR ALL USING (public.cc_can_manage_org(organization_id)) WITH CHECK (public.cc_can_manage_org(organization_id));",
    "ALTER TABLE public.accounts_teammembership ENABLE ROW LEVEL SECURITY;",
    "DROP POLICY IF EXISTS cc_accounts_teammembership_select ON public.accounts_teammembership;",
    "CREATE POLICY cc_accounts_teammembership_select ON public.accounts_teammembership FOR SELECT USING (public.cc_can_read_org((SELECT t.organization_id FROM public.accounts_team t WHERE t.id = team_id)));",
    "DROP POLICY IF EXISTS cc_accounts_teammembership_modify ON public.accounts_teammembership;",
    "CREATE POLICY cc_accounts_teammembership_modify ON public.accounts_teammembership FOR ALL USING (public.cc_can_manage_org((SELECT t.organization_id FROM public.accounts_team t WHERE t.id = team_id))) WITH CHECK (public.cc_can_manage_org((SELECT t.organization_id FROM public.accounts_team t WHERE t.id = team_id)));",
    "ALTER TABLE public.bookings_service ENABLE ROW LEVEL SECURITY;",
    "DROP POLICY IF EXISTS cc_bookings_service_select ON public.bookings_service;",
    "CREATE POLICY cc_bookings_service_select ON public.bookings_service FOR SELECT USING (public.cc_can_read_org(organization_id));",
    "DROP POLICY IF EXISTS cc_bookings_service_modify ON public.bookings_service;",
    "CREATE POLICY cc_bookings_service_modify ON public.bookings_service FOR ALL USING (public.cc_can_manage_org(organization_id)) WITH CHECK (public.cc_can_manage_org(organization_id));",
    "ALTER TABLE public.bookings_facilityresource ENABLE ROW LEVEL SECURITY;",
    "DROP POLICY IF EXISTS cc_bookings_facilityresource_select ON public.bookings_facilityresource;",
    "CREATE POLICY cc_bookings_facilityresource_select ON public.bookings_facilityresource FOR SELECT USING (public.cc_can_read_org(organization_id));",
    "DROP POLICY IF EXISTS cc_bookings_facilityresource_modify ON public.bookings_facilityresource;",
    "CREATE POLICY cc_bookings_facilityresource_modify ON public.bookings_facilityresource FOR ALL USING (public.cc_can_manage_org(organization_id)) WITH CHECK (public.cc_can_manage_org(organization_id));",
    "ALTER TABLE public.bookings_serviceresource ENABLE ROW LEVEL SECURITY;",
    "DROP POLICY IF EXISTS cc_bookings_serviceresource_select ON public.bookings_serviceresource;",
    "CREATE POLICY cc_bookings_serviceresource_select ON public.bookings_serviceresource FOR SELECT USING (public.cc_can_read_org((SELECT s.organization_id FROM public.bookings_service s WHERE s.id = service_id)));",
    "DROP POLICY IF EXISTS cc_bookings_serviceresource_modify ON public.bookings_serviceresource;",
    "CREATE POLICY cc_bookings_serviceresource_modify ON public.bookings_serviceresource FOR ALL USING (public.cc_can_manage_org((SELECT s.organization_id FROM public.bookings_service s WHERE s.id = service_id))) WITH CHECK (public.cc_can_manage_org((SELECT s.organization_id FROM public.bookings_service s WHERE s.id = service_id)));",
    "ALTER TABLE public.bookings_weeklyavailability ENABLE ROW LEVEL SECURITY;",
    "DROP POLICY IF EXISTS cc_bookings_weeklyavailability_select ON public.bookings_weeklyavailability;",
    "CREATE POLICY cc_bookings_weeklyavailability_select ON public.bookings_weeklyavailability FOR SELECT USING (public.cc_can_read_org(organization_id));",
    "DROP POLICY IF EXISTS cc_bookings_weeklyavailability_modify ON public.bookings_weeklyavailability;",
    "CREATE POLICY cc_bookings_weeklyavailability_modify ON public.bookings_weeklyavailability FOR ALL USING (public.cc_can_manage_org(organization_id)) WITH CHECK (public.cc_can_manage_org(organization_id));",
    "ALTER TABLE public.bookings_serviceweeklyavailability ENABLE ROW LEVEL SECURITY;",
    "DROP POLICY IF EXISTS cc_bookings_serviceweeklyavailability_select ON public.bookings_serviceweeklyavailability;",
    "CREATE POLICY cc_bookings_serviceweeklyavailability_select ON public.bookings_serviceweeklyavailability FOR SELECT USING (public.cc_can_read_org((SELECT s.organization_id FROM public.bookings_service s WHERE s.id = service_id)));",
    "DROP POLICY IF EXISTS cc_bookings_serviceweeklyavailability_modify ON public.bookings_serviceweeklyavailability;",
    "CREATE POLICY cc_bookings_serviceweeklyavailability_modify ON public.bookings_serviceweeklyavailability FOR ALL USING (public.cc_can_manage_org((SELECT s.organization_id FROM public.bookings_service s WHERE s.id = service_id))) WITH CHECK (public.cc_can_manage_org((SELECT s.organization_id FROM public.bookings_service s WHERE s.id = service_id)));",
    "ALTER TABLE public.bookings_memberweeklyavailability ENABLE ROW LEVEL SECURITY;",
    "DROP POLICY IF EXISTS cc_bookings_memberweeklyavailability_select ON public.bookings_memberweeklyavailability;",
    "CREATE POLICY cc_bookings_memberweeklyavailability_select ON public.bookings_memberweeklyavailability FOR SELECT USING (public.cc_can_read_org((SELECT m.organization_id FROM public.accounts_membership m WHERE m.id = membership_id)));",
    "DROP POLICY IF EXISTS cc_bookings_memberweeklyavailability_modify ON public.bookings_memberweeklyavailability;",
    "CREATE POLICY cc_bookings_memberweeklyavailability_modify ON public.bookings_memberweeklyavailability FOR ALL USING (public.cc_can_manage_org((SELECT m.organization_id FROM public.accounts_membership m WHERE m.id = membership_id))) WITH CHECK (public.cc_can_manage_org((SELECT m.organization_id FROM public.accounts_membership m WHERE m.id = membership_id)));",
    "ALTER TABLE public.bookings_servicesettingfreeze ENABLE ROW LEVEL SECURITY;",
    "DROP POLICY IF EXISTS cc_bookings_servicesettingfreeze_select ON public.bookings_servicesettingfreeze;",
    "CREATE POLICY cc_bookings_servicesettingfreeze_select ON public.bookings_servicesettingfreeze FOR SELECT USING (public.cc_can_read_org((SELECT s.organization_id FROM public.bookings_service s WHERE s.id = service_id)));",
    "DROP POLICY IF EXISTS cc_bookings_servicesettingfreeze_modify ON public.bookings_servicesettingfreeze;",
    "CREATE POLICY cc_bookings_servicesettingfreeze_modify ON public.bookings_servicesettingfreeze FOR ALL USING (public.cc_can_manage_org((SELECT s.organization_id FROM public.bookings_service s WHERE s.id = service_id))) WITH CHECK (public.cc_can_manage_org((SELECT s.organization_id FROM public.bookings_service s WHERE s.id = service_id)));",
    "ALTER TABLE public.bookings_serviceassignment ENABLE ROW LEVEL SECURITY;",
    "DROP POLICY IF EXISTS cc_bookings_serviceassignment_select ON public.bookings_serviceassignment;",
    "CREATE POLICY cc_bookings_serviceassignment_select ON public.bookings_serviceassignment FOR SELECT USING (public.cc_can_read_org((SELECT s.organization_id FROM public.bookings_service s WHERE s.id = service_id)));",
    "DROP POLICY IF EXISTS cc_bookings_serviceassignment_modify ON public.bookings_serviceassignment;",
    "CREATE POLICY cc_bookings_serviceassignment_modify ON public.bookings_serviceassignment FOR ALL USING (public.cc_can_manage_org((SELECT s.organization_id FROM public.bookings_service s WHERE s.id = service_id))) WITH CHECK (public.cc_can_manage_org((SELECT s.organization_id FROM public.bookings_service s WHERE s.id = service_id)));",
    "ALTER TABLE public.bookings_booking ENABLE ROW LEVEL SECURITY;",
    "DROP POLICY IF EXISTS cc_bookings_booking_select ON public.bookings_booking;",
    "CREATE POLICY cc_bookings_booking_select ON public.bookings_booking FOR SELECT USING (public.cc_can_read_org(organization_id));",
    "DROP POLICY IF EXISTS cc_bookings_booking_modify ON public.bookings_booking;",
    "CREATE POLICY cc_bookings_booking_modify ON public.bookings_booking FOR ALL USING (public.cc_can_read_org(organization_id)) WITH CHECK (public.cc_can_read_org(organization_id));",
    "ALTER TABLE public.bookings_publicbookingintent ENABLE ROW LEVEL SECURITY;",
    "DROP POLICY IF EXISTS cc_bookings_publicbookingintent_select ON public.bookings_publicbookingintent;",
    "CREATE POLICY cc_bookings_publicbookingintent_select ON public.bookings_publicbookingintent FOR SELECT USING (public.cc_can_read_org(organization_id));",
    "DROP POLICY IF EXISTS cc_bookings_publicbookingintent_modify ON public.bookings_publicbookingintent;",
    "CREATE POLICY cc_bookings_publicbookingintent_modify ON public.bookings_publicbookingintent FOR ALL USING (public.cc_can_read_org(organization_id)) WITH CHECK (public.cc_can_read_org(organization_id));",
    "ALTER TABLE public.bookings_orgsettings ENABLE ROW LEVEL SECURITY;",
    "DROP POLICY IF EXISTS cc_bookings_orgsettings_select ON public.bookings_orgsettings;",
    "CREATE POLICY cc_bookings_orgsettings_select ON public.bookings_orgsettings FOR SELECT USING (public.cc_can_read_org(organization_id));",
    "DROP POLICY IF EXISTS cc_bookings_orgsettings_modify ON public.bookings_orgsettings;",
    "CREATE POLICY cc_bookings_orgsettings_modify ON public.bookings_orgsettings FOR ALL USING (public.cc_can_manage_org(organization_id)) WITH CHECK (public.cc_can_manage_org(organization_id));",
    "ALTER TABLE public.bookings_auditbooking ENABLE ROW LEVEL SECURITY;",
    "DROP POLICY IF EXISTS cc_bookings_auditbooking_select ON public.bookings_auditbooking;",
    "CREATE POLICY cc_bookings_auditbooking_select ON public.bookings_auditbooking FOR SELECT USING (public.cc_can_manage_org(organization_id));",
    "DROP POLICY IF EXISTS cc_bookings_auditbooking_insert ON public.bookings_auditbooking;",
    "CREATE POLICY cc_bookings_auditbooking_insert ON public.bookings_auditbooking FOR INSERT WITH CHECK (public.cc_can_read_org(organization_id));",
    "DROP POLICY IF EXISTS cc_bookings_auditbooking_modify ON public.bookings_auditbooking;",
    "CREATE POLICY cc_bookings_auditbooking_modify ON public.bookings_auditbooking FOR ALL USING (public.cc_can_manage_org(organization_id)) WITH CHECK (public.cc_can_manage_org(organization_id));",
    "ALTER TABLE public.billing_subscription ENABLE ROW LEVEL SECURITY;",
    "DROP POLICY IF EXISTS cc_billing_subscription_manage ON public.billing_subscription;",
    "CREATE POLICY cc_billing_subscription_manage ON public.billing_subscription FOR ALL USING (public.cc_can_manage_org(organization_id)) WITH CHECK (public.cc_can_manage_org(organization_id));",
    "ALTER TABLE public.billing_paymentmethod ENABLE ROW LEVEL SECURITY;",
    "DROP POLICY IF EXISTS cc_billing_paymentmethod_manage ON public.billing_paymentmethod;",
    "CREATE POLICY cc_billing_paymentmethod_manage ON public.billing_paymentmethod FOR ALL USING (public.cc_can_manage_org(organization_id)) WITH CHECK (public.cc_can_manage_org(organization_id));",
    "ALTER TABLE public.billing_invoicemeta ENABLE ROW LEVEL SECURITY;",
    "DROP POLICY IF EXISTS cc_billing_invoicemeta_manage ON public.billing_invoicemeta;",
    "CREATE POLICY cc_billing_invoicemeta_manage ON public.billing_invoicemeta FOR ALL USING (public.cc_can_manage_org(organization_id)) WITH CHECK (public.cc_can_manage_org(organization_id));",
    "ALTER TABLE public.billing_invoiceactionlog ENABLE ROW LEVEL SECURITY;",
    "DROP POLICY IF EXISTS cc_billing_invoiceactionlog_manage ON public.billing_invoiceactionlog;",
    "CREATE POLICY cc_billing_invoiceactionlog_manage ON public.billing_invoiceactionlog FOR ALL USING (public.cc_can_manage_org((SELECT im.organization_id FROM public.billing_invoicemeta im WHERE im.id = invoice_meta_id))) WITH CHECK (public.cc_can_manage_org((SELECT im.organization_id FROM public.billing_invoicemeta im WHERE im.id = invoice_meta_id)));",
    "ALTER TABLE public.billing_subscriptionchange ENABLE ROW LEVEL SECURITY;",
    "DROP POLICY IF EXISTS cc_billing_subscriptionchange_manage ON public.billing_subscriptionchange;",
    "CREATE POLICY cc_billing_subscriptionchange_manage ON public.billing_subscriptionchange FOR ALL USING (public.cc_can_manage_org(organization_id)) WITH CHECK (public.cc_can_manage_org(organization_id));",
    "ALTER TABLE public.billing_applieddiscount ENABLE ROW LEVEL SECURITY;",
    "DROP POLICY IF EXISTS cc_billing_applieddiscount_manage ON public.billing_applieddiscount;",
    "CREATE POLICY cc_billing_applieddiscount_manage ON public.billing_applieddiscount FOR ALL USING (public.cc_can_manage_org((SELECT s.organization_id FROM public.billing_subscription s WHERE s.id = subscription_id))) WITH CHECK (public.cc_can_manage_org((SELECT s.organization_id FROM public.billing_subscription s WHERE s.id = subscription_id)));",
]


REVERSE_SQL = [
    "DROP POLICY IF EXISTS cc_billing_applieddiscount_manage ON public.billing_applieddiscount;",
    "DROP POLICY IF EXISTS cc_billing_subscriptionchange_manage ON public.billing_subscriptionchange;",
    "DROP POLICY IF EXISTS cc_billing_invoiceactionlog_manage ON public.billing_invoiceactionlog;",
    "DROP POLICY IF EXISTS cc_billing_invoicemeta_manage ON public.billing_invoicemeta;",
    "DROP POLICY IF EXISTS cc_billing_paymentmethod_manage ON public.billing_paymentmethod;",
    "DROP POLICY IF EXISTS cc_billing_subscription_manage ON public.billing_subscription;",
    "DROP POLICY IF EXISTS cc_bookings_auditbooking_modify ON public.bookings_auditbooking;",
    "DROP POLICY IF EXISTS cc_bookings_auditbooking_insert ON public.bookings_auditbooking;",
    "DROP POLICY IF EXISTS cc_bookings_auditbooking_select ON public.bookings_auditbooking;",
    "DROP POLICY IF EXISTS cc_bookings_orgsettings_modify ON public.bookings_orgsettings;",
    "DROP POLICY IF EXISTS cc_bookings_orgsettings_select ON public.bookings_orgsettings;",
    "DROP POLICY IF EXISTS cc_bookings_publicbookingintent_modify ON public.bookings_publicbookingintent;",
    "DROP POLICY IF EXISTS cc_bookings_publicbookingintent_select ON public.bookings_publicbookingintent;",
    "DROP POLICY IF EXISTS cc_bookings_booking_modify ON public.bookings_booking;",
    "DROP POLICY IF EXISTS cc_bookings_booking_select ON public.bookings_booking;",
    "DROP POLICY IF EXISTS cc_bookings_serviceassignment_modify ON public.bookings_serviceassignment;",
    "DROP POLICY IF EXISTS cc_bookings_serviceassignment_select ON public.bookings_serviceassignment;",
    "DROP POLICY IF EXISTS cc_bookings_servicesettingfreeze_modify ON public.bookings_servicesettingfreeze;",
    "DROP POLICY IF EXISTS cc_bookings_servicesettingfreeze_select ON public.bookings_servicesettingfreeze;",
    "DROP POLICY IF EXISTS cc_bookings_memberweeklyavailability_modify ON public.bookings_memberweeklyavailability;",
    "DROP POLICY IF EXISTS cc_bookings_memberweeklyavailability_select ON public.bookings_memberweeklyavailability;",
    "DROP POLICY IF EXISTS cc_bookings_serviceweeklyavailability_modify ON public.bookings_serviceweeklyavailability;",
    "DROP POLICY IF EXISTS cc_bookings_serviceweeklyavailability_select ON public.bookings_serviceweeklyavailability;",
    "DROP POLICY IF EXISTS cc_bookings_weeklyavailability_modify ON public.bookings_weeklyavailability;",
    "DROP POLICY IF EXISTS cc_bookings_weeklyavailability_select ON public.bookings_weeklyavailability;",
    "DROP POLICY IF EXISTS cc_bookings_serviceresource_modify ON public.bookings_serviceresource;",
    "DROP POLICY IF EXISTS cc_bookings_serviceresource_select ON public.bookings_serviceresource;",
    "DROP POLICY IF EXISTS cc_bookings_facilityresource_modify ON public.bookings_facilityresource;",
    "DROP POLICY IF EXISTS cc_bookings_facilityresource_select ON public.bookings_facilityresource;",
    "DROP POLICY IF EXISTS cc_bookings_service_modify ON public.bookings_service;",
    "DROP POLICY IF EXISTS cc_bookings_service_select ON public.bookings_service;",
    "DROP POLICY IF EXISTS cc_accounts_teammembership_modify ON public.accounts_teammembership;",
    "DROP POLICY IF EXISTS cc_accounts_teammembership_select ON public.accounts_teammembership;",
    "DROP POLICY IF EXISTS cc_accounts_team_modify ON public.accounts_team;",
    "DROP POLICY IF EXISTS cc_accounts_team_select ON public.accounts_team;",
    "DROP POLICY IF EXISTS cc_accounts_invite_manage ON public.accounts_invite;",
    "DROP POLICY IF EXISTS cc_accounts_membership_modify ON public.accounts_membership;",
    "DROP POLICY IF EXISTS cc_accounts_membership_select ON public.accounts_membership;",
] + [f"ALTER TABLE public.{table_name} DISABLE ROW LEVEL SECURITY;" for table_name in reversed(RLS_TABLES)] + [
    "DROP FUNCTION IF EXISTS public.cc_can_manage_org(bigint);",
    "DROP FUNCTION IF EXISTS public.cc_can_read_org(bigint);",
    "DROP FUNCTION IF EXISTS public.cc_auth_has_org_access(bigint);",
    "DROP FUNCTION IF EXISTS public.cc_rls_bypass();",
    "DROP FUNCTION IF EXISTS public.cc_current_org_id();",
    "DROP FUNCTION IF EXISTS public.cc_current_user_id();",
]


def _apply_sql(schema_editor, statements):
    if schema_editor.connection.vendor != 'postgresql':
        return

    with schema_editor.connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)


def apply_postgresql_rls(apps, schema_editor):
    _apply_sql(schema_editor, FORWARD_SQL)


def unapply_postgresql_rls(apps, schema_editor):
    _apply_sql(schema_editor, REVERSE_SQL)


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0021_business_cloudflare_status_fields'),
        ('bookings', '0026_service_location_fields'),
        ('billing', '0010_subscription_custom_domain_addon_enabled'),
    ]

    operations = [
        migrations.RunPython(apply_postgresql_rls, unapply_postgresql_rls),
    ]