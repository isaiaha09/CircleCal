import os
import sys
import pathlib
import django

# Ensure project root is on sys.path so settings import works when run from tools/
sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'circlecalproject.settings')
django.setup()

from django.db import connection

cur = connection.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'accounts_%';")
rows = cur.fetchall()
if not rows:
    print('<no accounts tables found>')
else:
    for r in rows:
        print(r[0])
