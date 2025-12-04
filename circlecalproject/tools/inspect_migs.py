import sqlite3
con = sqlite3.connect('db.sqlite3')
cur = con.cursor()
cur.execute("select app, name, applied from django_migrations where app='bookings' order by applied")
rows = cur.fetchall()
print('bookings migrations:')
for r in rows:
    print(r)
con.close()
