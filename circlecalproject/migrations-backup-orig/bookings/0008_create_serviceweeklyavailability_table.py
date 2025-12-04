from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("bookings", "0007_orgsettings_refund_policy"),
    ]

    operations = [
        migrations.RunSQL(
            sql='''
            CREATE TABLE IF NOT EXISTS "bookings_serviceweeklyavailability" (
                "id" integer NOT NULL PRIMARY KEY AUTOINCREMENT,
                "weekday" smallint unsigned NOT NULL CHECK ("weekday" >= 0),
                "start_time" time NOT NULL,
                "end_time" time NOT NULL,
                "is_active" bool NOT NULL,
                "service_id" bigint NOT NULL REFERENCES "bookings_service" ("id") DEFERRABLE INITIALLY DEFERRED
            );
            CREATE INDEX IF NOT EXISTS "bookings_se_service_d8998b_idx" ON "bookings_serviceweeklyavailability" ("service_id", "weekday");
            ''',
            reverse_sql='''
            DROP INDEX IF EXISTS "bookings_se_service_d8998b_idx";
            DROP TABLE IF EXISTS "bookings_serviceweeklyavailability";
            ''',
        )
    ]
