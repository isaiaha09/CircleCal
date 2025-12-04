import sqlite3
con=sqlite3.connect('db.sqlite3')
cur=con.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = sorted([r[0] for r in cur.fetchall()])
print('tables:')
for t in tables:
    print(' -', t)
# check for bookings_serviceweeklyavailability
if 'bookings_serviceweeklyavailability' in tables:
    print('\nbookings_serviceweeklyavailability exists')
else:
    print('\nbookings_serviceweeklyavailability is MISSING')
con.close()
