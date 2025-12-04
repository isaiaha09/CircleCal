import sqlite3
con=sqlite3.connect('db.sqlite3')
cur=con.cursor()
cur.execute('''CREATE TABLE IF NOT EXISTS "bookings_serviceweeklyavailability" (
    "id" integer NOT NULL PRIMARY KEY AUTOINCREMENT,
    "weekday" smallint unsigned NOT NULL CHECK ("weekday" >= 0),
    "start_time" time NOT NULL,
    "end_time" time NOT NULL,
    "is_active" bool NOT NULL,
    "service_id" bigint NOT NULL REFERENCES "bookings_service" ("id") DEFERRABLE INITIALLY DEFERRED
);
''')
cur.execute('CREATE INDEX IF NOT EXISTS "bookings_se_service_d8998b_idx" ON "bookings_serviceweeklyavailability" ("service_id", "weekday");')
con.commit()
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
print('tables now:', sorted([r[0] for r in cur.fetchall()]))
cur.execute("SELECT sql FROM sqlite_master WHERE name='bookings_serviceweeklyavailability'")
print('create stmt:', cur.fetchone()[0])
con.close()
