import tempfile
import unittest
from pathlib import Path

from expense_tracker.schema import ensure_db
from expense_tracker.store import insert_transaction, list_transactions


class Phase2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'expenses.db'
        self.conn = ensure_db(self.path)
        self.addCleanup(self.conn.close)
        self.tx = dict(date='2026-01-02', merchant='Example Shop', amount='10')

    def test_migrate_legacy_database_and_reopen(self):
        insert_transaction(self.conn, self.tx)
        self.conn.commit()
        # Recreate the Phase 1 layout independently of the current schema.
        columns = [row[1] for row in self.conn.execute('PRAGMA table_info(transactions)')]
        for name in ('raw_category', 'parser_reason'):
            if name in columns:
                self.conn.execute('ALTER TABLE transactions DROP COLUMN ' + name)
        self.conn.execute('PRAGMA user_version = 0')
        self.conn.commit()
        for _ in range(2):
            conn = ensure_db(self.path)
            try:
                self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0], 2)
                row = list_transactions(conn, '2026-01-01', '2026-01-31')[0]
                self.assertEqual(row['merchant'], self.tx['merchant'])
                self.assertIsNone(row['raw_category'])
                self.assertIsNone(row['parser_reason'])
                self.assertFalse(insert_transaction(conn, self.tx))
            finally:
                conn.close()

    def test_trusted_metadata_persists_and_rules_win(self):
        from expense_tracker.classify import classify_transaction
        from expense_tracker.models import source_hash
        tx = dict(self.tx, category='travel', category_source='importer',
                  category_confidence=0.9, raw_category='Transport',
                  parser_reason='matched amount', parser_confidence=0.8)
        result = classify_transaction(self.conn, tx)
        self.assertEqual(result['category'], 'travel')
        self.assertEqual(result['status'], 'confirmed')
        self.assertEqual(result['category_confidence'], 0.9)
        self.assertTrue(insert_transaction(self.conn, tx))
        row = list_transactions(self.conn, tx['date'], tx['date'])[0]
        for key, value in result.items():
            self.assertEqual(row[key], value)
        self.assertEqual(source_hash(self.tx), source_hash(tx))
        self.conn.execute("INSERT INTO merchant_rules(merchant, category) VALUES (?, ?)",
                          ('Example Shop', 'groceries'))
        result = classify_transaction(self.conn, tx)
        self.assertEqual((result['category'], result['category_source'],
                          result['category_confidence']), ('groceries', 'merchant_rule', 1.0))
        self.assertEqual(result['raw_category'], 'Transport')
        self.assertEqual(classify_transaction(self.conn, dict(tx, parser_confidence=0.79))
                         ['review_reason'], 'low_parser_confidence')

    def test_metadata_validation_and_unknown_category(self):
        from expense_tracker.classify import classify_transaction
        from expense_tracker.models import normalize_transaction
        tx = dict(self.tx, parser_confidence=1, category='travel',
                  category_source='importer', category_confidence=0.9)
        for changes in ({'category': ''}, {'category_source': 123},
                        {'category_confidence': True}, {'category_confidence': float('nan')},
                        {'category_confidence': float('inf')}, {'category_confidence': -0.1},
                        {'category_confidence': 1.1}, {'category_confidence': 10 ** 400},
                        {'raw_category': []},
                        {'parser_reason': 12}):
            with self.subTest(changes=changes):
                for function in (normalize_transaction, lambda value: classify_transaction(self.conn, value)):
                    with self.assertRaises(ValueError):
                        function(dict(tx, **changes))
        with self.assertRaises(ValueError):
            normalize_transaction(dict(self.tx, category='travel'))
        for changes in ({'category': 'uncategorized'}, {'category': 'unknown'},
                        {'category_source': 'default'}):
            result = classify_transaction(self.conn, dict(tx, **changes))
            self.assertEqual(result['review_reason'], 'unknown_merchant')
            self.assertEqual(result['status'], 'needs_review')
        result = classify_transaction(self.conn, dict(tx, parser_confidence=0.5))
        self.assertEqual(result['review_reason'], 'low_parser_confidence')

    def test_review_list_is_bounded_and_newest_first(self):
        from expense_tracker import store
        self.assertTrue(hasattr(store, 'list_review_transactions'))
        self.assertEqual(store.list_review_transactions(self.conn), [])
        for number in range(55):
            insert_transaction(self.conn, dict(self.tx, source_id=str(number)))
        insert_transaction(self.conn, dict(self.tx, source_id='confirmed',
                           category='travel', category_source='importer', category_confidence=1))
        rows = store.list_review_transactions(self.conn)
        self.assertEqual([row['source_id'] for row in rows], ['54', '53', '52', '51', '50'])
        self.assertEqual(len(store.list_review_transactions(self.conn, 50)), 50)
        self.assertEqual(len(store.list_review_transactions(self.conn, 1)), 1)
        for limit in (0, -1, 51, True, 1.5, '5', None):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                store.list_review_transactions(self.conn, limit)

    def test_review_cli_and_metadata_import_privacy(self):
        import json
        import subprocess
        import sys

        def cli(*args):
            return subprocess.run([sys.executable, '-m', 'expense_tracker.cli',
                                   *map(str, args)], capture_output=True, text=True)

        data = Path(self.tmp.name) / 'input.jsonl'
        tx = dict(self.tx, category='travel', category_source='importer',
                  category_confidence=0.9, parser_confidence=0.5,
                  raw_category='Transport', parser_reason='uncertain amount')
        data.write_text(json.dumps(tx) + '\n', encoding='utf-8')
        result = cli('ingest-json', '--db', self.path, '--input', data)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = cli('review', '--db', self.path, '--limit', 1)
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['parser_reason'], 'uncertain amount')
        for limit in ('0', '51', 'invalid-private-value'):
            result = cli('review', '--db', self.path, '--limit', limit)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, '')
            self.assertNotIn(limit, result.stderr)
        data.write_text(json.dumps(dict(tx, category_confidence='invalid-private-value')),
                        encoding='utf-8')
        result = cli('ingest-json', '--db', self.path, '--input', data)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn('invalid-private-value', result.stderr)
