import importlib.util
import tempfile
import unittest
from pathlib import Path


class PersistenceTests(unittest.TestCase):
    def test_persist_and_deduplicate(self):
        self.assertIsNotNone(importlib.util.find_spec('expense_tracker'))
        from expense_tracker.schema import ensure_db
        from expense_tracker.store import insert_transaction, list_transactions
        with tempfile.TemporaryDirectory() as tmp:
            with ensure_db(Path(tmp) / 'nested' / 'expenses.db') as conn:
                tx = {'date': '2026-01-02', 'merchant': 'Example Shop', 'amount': '125.50'}
                self.assertTrue(insert_transaction(conn, tx))
                self.assertFalse(insert_transaction(conn, dict(tx, amount=125.5)))
                rows = list_transactions(conn, '2026-01-02', '2026-01-02')
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]['amount_paise'], 12550)
                self.assertEqual(rows[0]['status'], 'needs_review')
                self.assertEqual(list_transactions(conn, '2026-02-01', '2026-02-28'), [])
                tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertTrue({'transactions', 'merchant_rules', 'review_actions', 'ingestion_runs'} <= tables)
            conn.close()


class CliTests(unittest.TestCase):
    def run_cli(self, *args):
        import subprocess
        import sys
        return subprocess.run([sys.executable, '-m', 'expense_tracker.cli', *map(str, args)],
                              capture_output=True, text=True)

    def test_extreme_amount_exponent_records_failed_import(self):
        import json
        import sqlite3
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'expenses.db'
            data = Path(tmp) / 'input.jsonl'
            tx = dict(date='2026-01-02', merchant='Shop', amount='10')
            data.write_text(json.dumps(tx) + '\n' +
                            json.dumps(dict(tx, amount='1e999999999')) + '\n', encoding='utf-8')
            result = self.run_cli('ingest-json', '--db', db, '--input', data)
            self.assertEqual(result.returncode, 1)
            self.assertNotIn('Traceback', result.stderr)
            with sqlite3.connect(db) as conn:
                self.assertEqual(conn.execute('SELECT count(*) FROM transactions').fetchone()[0], 0)
                self.assertEqual(conn.execute('SELECT status, error_line, inserted_count FROM ingestion_runs').fetchone(),
                                 ('failed', 2, 0))
            conn.close()

    def test_import_list_duplicate_and_rollback(self):
        import json
        import sqlite3
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'data' / 'expenses.db'
            data = Path(tmp) / 'input.jsonl'
            result = self.run_cli('init', '--db', db)
            self.assertEqual(result.returncode, 0, result.stderr)
            tx = {'date': '2026-01-02', 'merchant': 'Example Shop', 'amount': '10.25'}
            data.write_text(json.dumps(tx) + '\n', encoding='utf-8')
            for expected in ({'inserted': 1, 'duplicates': 0}, {'inserted': 0, 'duplicates': 1}):
                result = self.run_cli('ingest-json', '--db', db, '--input', data)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), expected)
            result = self.run_cli('list', '--db', db, '--start', '2026-01-01', '--end', '2026-01-31')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['amount_paise'], 1025)
            data.write_text(json.dumps(dict(tx, amount='20')) + '\nprivate-invalid-content\n', encoding='utf-8')
            result = self.run_cli('ingest-json', '--db', db, '--input', data)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('private-invalid-content', result.stderr)
            with sqlite3.connect(db) as conn:
                self.assertEqual(conn.execute('SELECT count(*) FROM transactions').fetchone()[0], 1)
                self.assertEqual(conn.execute('SELECT status, error_line, inserted_count FROM ingestion_runs ORDER BY id DESC LIMIT 1').fetchone(),
                                 ('failed', 2, 0))
            conn.close()
            result = self.run_cli('list', '--db', db, '--start', '2026-02-01', '--end', '2026-01-01')
            self.assertNotEqual(result.returncode, 0)

    def test_decimal_confidence_and_merchant_rule(self):
        import json
        from expense_tracker.schema import ensure_db
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'expenses.db'
            with ensure_db(db) as conn:
                conn.execute("INSERT INTO merchant_rules(merchant, category) VALUES (?, ?)",
                             ('Example Shop', 'groceries'))
            conn.close()
            data = Path(tmp) / 'input.jsonl'
            data.write_text(json.dumps({'date': '2026-01-02', 'merchant': 'example shop',
                                       'amount': 12.34, 'parser_confidence': 0.9}), encoding='utf-8')
            result = self.run_cli('ingest-json', '--db', db, '--input', data)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = self.run_cli('list', '--db', db, '--start', '2026-01-02', '--end', '2026-01-02')
            row = json.loads(result.stdout)
            self.assertEqual(row['category'], 'groceries')
            self.assertEqual(row['category_source'], 'merchant_rule')
            self.assertEqual(row['status'], 'confirmed')


class ValidationTests(unittest.TestCase):
    def test_extreme_amount_exponents_raise_value_error(self):
        from expense_tracker.models import normalize_transaction
        for amount in ('1e999999999', '1e-999999999'):
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                normalize_transaction(dict(date='2026-01-02', merchant='Shop', amount=amount))

    def test_amount_conversion_preserves_exact_precision(self):
        from expense_tracker.models import normalize_transaction
        tx = dict(date='2026-01-02', merchant='Shop')
        with self.assertRaises(ValueError):
            normalize_transaction(dict(tx, amount='1.00000000000000000000000000001'))
        self.assertEqual(normalize_transaction(dict(tx, amount='1.00000000000000000000000000000'))['amount_paise'], 100)
        self.assertEqual(normalize_transaction(dict(tx, amount='92233720368547758.07'))['amount_paise'], 9223372036854775807)

    def test_hash_identity(self):
        from expense_tracker.models import source_hash
        tx = dict(date='2026-01-02', merchant='Example Shop', amount='12.30')
        self.assertEqual(source_hash(tx), source_hash(dict(reversed(list(tx.items())))))
        self.assertEqual(source_hash(tx), source_hash(dict(tx, amount=12.3, parser_confidence=0.5)))
        self.assertEqual(len(source_hash(tx)), 64)
        self.assertNotEqual(source_hash(dict(tx, source_id='record-a')),
                            source_hash(dict(tx, source_id='record-b')))
        self.assertEqual(source_hash(dict(tx, source_id='record-a')),
                         source_hash(dict(tx, source_id='record-a', amount='25')))

    def test_invalid_transactions(self):
        from expense_tracker.models import normalize_transaction
        tx = dict(date='2026-01-02', merchant='Example Shop', amount='12.30')
        for changes in [
            {'date': '2026-02-30'}, {'date': '20260102'}, {'merchant': ''},
            {'amount': '0'}, {'amount': '-1'}, {'amount': '0.001'},
            {'amount': 'NaN'}, {'amount': 'Infinity'}, {'amount': True},
            {'amount': '92233720368547758.08'}, {'currency': 'USD'},
            {'parser_confidence': 1.1}, {'parser_confidence': False},
            {'parser_confidence': float('nan')}, {'source_id': ''},
            {'source_hash': 'untrusted'}, {'source': ''},
        ]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                normalize_transaction(dict(tx, **changes))

    def test_low_confidence_requires_review_and_constraints_propagate(self):
        import sqlite3
        from expense_tracker.schema import ensure_db
        from expense_tracker.store import insert_transaction, list_transactions
        with tempfile.TemporaryDirectory() as tmp:
            conn = ensure_db(Path(tmp) / 'expenses.db')
            try:
                with conn:
                    conn.execute("INSERT INTO merchant_rules(merchant, category) VALUES ('Shop', 'groceries')")
                    tx = dict(date='2026-01-02', merchant='Shop', amount='10', parser_confidence=0.2)
                    self.assertTrue(insert_transaction(conn, tx))
                    row = list_transactions(conn, '2026-01-02', '2026-01-02')[0]
                    self.assertEqual(row['review_reason'], 'low_parser_confidence')
                    self.assertEqual(row['status'], 'needs_review')
                    with self.assertRaises(sqlite3.IntegrityError):
                        insert_transaction(conn, dict(tx, amount='20'), ingestion_run_id=999)
            finally:
                conn.close()
