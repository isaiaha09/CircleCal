import sqlite3
con = sqlite3.connect('db.sqlite3')
cur = con.cursor()
cur.execute("PRAGMA table_info('accounts_business')")
rows = cur.fetchall()
for r in rows:
    print(r)
con.close()
