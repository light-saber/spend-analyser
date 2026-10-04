"""Explicit local JSONL ingestion and querying."""
import argparse
import json
import sqlite3
import sys
from contextlib import closing
from decimal import Decimal

from .schema import ensure_db
from .store import insert_transaction, list_transactions, list_review_transactions


def ingest_json(conn, input_path):
    with conn:
        run_id = conn.execute("INSERT INTO ingestion_runs(status) VALUES ('running')").lastrowid
    inserted = duplicates = 0
    line_number = None
    try:
        with conn:
            with open(input_path, encoding='utf-8') as stream:
                for line_number, line in enumerate(stream, 1):
                    if not line.strip():
                        continue
                    tx = json.loads(line, parse_float=Decimal)
                    if insert_transaction(conn, tx, ingestion_run_id=run_id):
                        inserted += 1
                    else:
                        duplicates += 1
            conn.execute("""
                UPDATE ingestion_runs SET status='completed', completed_at=CURRENT_TIMESTAMP,
                inserted_count=?, duplicate_count=? WHERE id=?
            """, (inserted, duplicates, run_id))
    except (ValueError, TypeError, OSError, sqlite3.Error):
        with conn:
            conn.execute("""
                UPDATE ingestion_runs SET status='failed', completed_at=CURRENT_TIMESTAMP,
                error_line=? WHERE id=?
            """, (line_number, run_id))
        location = f" at line {line_number}" if line_number is not None else ""
        raise ValueError(f"import failed{location}; no transactions imported") from None
    return dict(inserted=inserted, duplicates=duplicates)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('init', 'ingest-json', 'list', 'review'):
        command = commands.add_parser(name)
        command.add_argument('--db', required=True)
        if name == 'ingest-json':
            command.add_argument('--input', required=True)
        elif name == 'review':
            command.add_argument('--limit', default='5')
        elif name == 'list':
            command.add_argument('--start', required=True)
            command.add_argument('--end', required=True)
    args = parser.parse_args(argv)
    try:
        with closing(ensure_db(args.db)) as conn:
            if args.command == 'init':
                print('Database initialized.')
            elif args.command == 'ingest-json':
                print(json.dumps(ingest_json(conn, args.input)))
            elif args.command == 'review':
                for row in list_review_transactions(conn, int(args.limit)):
                    print(json.dumps(row, ensure_ascii=False))
            else:
                for row in list_transactions(conn, args.start, args.end):
                    print(json.dumps(row, ensure_ascii=False))
    except (ValueError, TypeError, OSError, sqlite3.Error):
        print('Error: operation failed; check input, dates, and file access. '
              'Failed imports roll back all transaction changes.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
