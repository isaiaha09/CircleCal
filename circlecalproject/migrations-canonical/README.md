This folder contains a proposed canonical migration set for review.

What this is
- `migrations-canonical/` contains suggested `0001_initial.py` files for apps where migration history had placeholders/merges. These files are NOT applied to the repo; they are a working proposal you can review.

Why we prepared this
- The repo had several placeholder/merge migration files and in-place RunSQL repairs. The canonical set collapses the app schema into an initial migration reflecting the current models.

How to review
1. Inspect the files under `migrations-canonical/` (per-app subfolders). The files are copied from the currently applied model state.
2. If you approve the canonical set, the recommended steps to apply (manual, careful):

PowerShell commands (run from project root):

```powershell
# 1) Backup current migrations and DB (already done in migrations-backup/)
mkdir migrations-backup-approved -Force
# copy existing migrations into backups if you want an extra copy
# 2) Remove or move existing app migrations that will be replaced
#    e.g. move bookings and accounts migrations to backup
Move-Item -Path .\bookings\migrations\*.py -Destination .\migrations-backup\bookings\ -Force
Move-Item -Path .\accounts\migrations\*.py -Destination .\migrations-backup\accounts\ -Force

# 3) Copy canonical files into app migrations folders (do NOT overwrite __init__.py)
Copy-Item -Path .\migrations-canonical\bookings\* -Destination .\bookings\migrations\ -Force
Copy-Item -Path .\migrations-canonical\accounts\* -Destination .\accounts\migrations\ -Force

# 4) OPTIONAL: remove db.sqlite3 and run migrations on a fresh DB locally to validate
Remove-Item -Force db.sqlite3 -ErrorAction SilentlyContinue
python manage.py migrate --noinput
python manage.py test
```

Notes & risks
- Rewriting migrations is a breaking change for active clones. After committing canonical migrations you should coordinate with teammates. Consumers with an existing DB will need to:
  - either re-create their DB from scratch and run migrations
  - or run `manage.py migrate --fake` carefully to align with the new migration names

If you want, I can prepare a PR with these canonical files and a proposed commit message, or I can apply the changes directly in the repo (move files and commit) after you explicitly approve.