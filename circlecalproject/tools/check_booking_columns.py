import os, sys, pathlib
sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'circlecalproject.settings')
import django
django.setup()
from django.db import connection
cur = connection.cursor()
cur.execute("PRAGMA table_info('bookings_booking');")
rows = cur.fetchall()
if not rows:
    print('<no bookings_booking table>')
else:
    for r in rows:
        # r layout: cid, name, type, notnull, dflt_value, pk
        print(r[1], r[2])
