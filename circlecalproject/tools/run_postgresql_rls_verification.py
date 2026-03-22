from __future__ import annotations

import argparse
import os
from pathlib import Path

import psycopg


DEFAULT_SQL_PATH = Path(__file__).with_name('postgresql_rls_verification.sql')


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument('--dsn', help='PostgreSQL connection string. Defaults to DATABASE_URL.')
    parser.add_argument('--sql-file', default=str(DEFAULT_SQL_PATH), help='Path to the SQL file to execute.')
    return parser.parse_args()


def iter_statements(sql_text: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    for line in sql_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('--'):
            continue
        current.append(line)
        if stripped.endswith(';'):
            statements.append('\n'.join(current).strip())
            current = []
    if current:
        statements.append('\n'.join(current).strip())
    return statements


def format_row(row: tuple) -> str:
    return ' | '.join('NULL' if value is None else str(value) for value in row)


def main() -> int:
    args = parse_args()
    dsn = (args.dsn or os.getenv('DATABASE_URL') or '').strip()
    if not dsn:
        raise SystemExit('DATABASE_URL is not set. Pass --dsn or set DATABASE_URL in the shell first.')

    sql_path = Path(args.sql_file)
    if not sql_path.exists():
        raise SystemExit(f'SQL file not found: {sql_path}')

    statements = iter_statements(sql_path.read_text(encoding='utf-8'))
    if not statements:
        raise SystemExit(f'No SQL statements found in: {sql_path}')

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cursor:
            for index, statement in enumerate(statements, start=1):
                cursor.execute(statement)
                print(f'\n=== Statement {index} ===')
                print(statement)
                if cursor.description:
                    headers = [column.name for column in cursor.description]
                    print(' | '.join(headers))
                    print('-+-'.join('-' * len(header) for header in headers))
                    for row in cursor.fetchall():
                        print(format_row(row))
                else:
                    print('(no rows)')

    return 0


if __name__ == '__main__':
    raise SystemExit(main())