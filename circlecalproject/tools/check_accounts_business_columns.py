import os, sys, pathlib
sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'circlecalproject.settings')
import django
django.setup()
from django.db import connection
cur = connection.cursor()
cur.execute("PRAGMA table_info('accounts_business');")
rows = cur.fetchall()
if not rows:
    print('<no accounts_business table>')
else:
    for r in rows:
        print(r[1], r[2])
