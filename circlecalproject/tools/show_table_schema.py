import sqlite3,sys
name=sys.argv[1]
con=sqlite3.connect('db.sqlite3')
cur=con.cursor()
cur.execute("PRAGMA table_info('%s')"%name)
print('columns:')
for r in cur.fetchall():
    print(r)
cur.execute("SELECT sql FROM sqlite_master WHERE name=?",(name,))
res=cur.fetchone()
print('\ncreate statement:')
print(res[0] if res else 'N/A')
con.close()
