"""Inspect SQLite schema for privacy policy cross-check.

Prints tables and columns only (no row data).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
DB_PATH = BASE_DIR / "db.sqlite3"

# Tables that typically contain user or customer data.
LIKELY_PERSONAL_TABLE_PREFIXES = (
    "auth_",
    "accounts_",
    "billing_",
    "bookings_",
    "calendar_app_",
    "django_admin_log",
    "axes_",
)


def main() -> None:
    con = sqlite3.connect(str(DB_PATH))
    try:
        cur = con.cursor()
        tables = [
            r[0]
            for r in cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        ]

        def columns_for(table: str) -> list[dict[str, object]]:
            cols = []
            for r in cur.execute(f"PRAGMA table_info('{table}')").fetchall():
                # r = (cid, name, type, notnull, dflt_value, pk)
                cols.append({"name": r[1], "type": r[2], "notnull": bool(r[3]), "pk": bool(r[5])})
            return cols

        likely_tables = [
            t
            for t in tables
            if t.startswith(LIKELY_PERSONAL_TABLE_PREFIXES)
            or t
            in (
                "auth_user",
                "django_admin_log",
                "django_session",
            )
        ]

        schema = {t: columns_for(t) for t in likely_tables}

        print(
            json.dumps(
                {
                    "db_path": str(DB_PATH),
                    "table_count": len(tables),
                    "likely_personal_table_count": len(likely_tables),
                    "likely_personal_tables": likely_tables,
                    "schema": schema,
                },
                indent=2,
                sort_keys=True,
            )
        )
    finally:
        con.close()


if __name__ == "__main__":
    main()
