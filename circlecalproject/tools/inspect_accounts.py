import sqlite3
con = sqlite3.connect('db.sqlite3')
cur = con.cursor()
cur.execute("select app, name, applied from django_migrations where app='accounts' order by applied")
rows = cur.fetchall()
print('accounts migrations:')
for r in rows:
    print(r)
con.close()
