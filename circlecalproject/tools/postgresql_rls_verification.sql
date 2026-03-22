-- Confirm the app is using PostgreSQL and identify the connected DB role.
select current_database() as database_name, current_user as db_role;

-- Confirm both RLS and FORCE RLS are enabled on representative tenant tables.
select
    n.nspname as schema_name,
    c.relname as table_name,
    c.relrowsecurity as rls_enabled,
    c.relforcerowsecurity as force_rls_enabled
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public'
  and c.relname in (
      'accounts_membership',
      'bookings_booking',
      'billing_subscription'
  )
order by c.relname;

-- Confirm both RLS migrations are recorded.
select app, name, applied
from django_migrations
where app = 'accounts'
  and name in ('0022_postgresql_rls', '0023_force_postgresql_rls')
order by applied;

-- Inspect ownership for the protected tables. If the app role owns the tables,
-- FORCE RLS is required for true DB-enforced isolation.
select
    c.relname as table_name,
    pg_get_userbyid(c.relowner) as table_owner
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public'
  and c.relname in (
      'accounts_membership',
      'bookings_booking',
      'billing_subscription'
  )
order by c.relname;

-- Inspect the current policies on representative tables.
select schemaname, tablename, policyname, permissive, roles, cmd, qual, with_check
from pg_policies
where schemaname = 'public'
  and tablename in (
      'accounts_membership',
      'bookings_booking',
      'billing_subscription'
  )
order by tablename, policyname;