import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from expense_tracker import classify


class JevTests(unittest.TestCase):
    def test_rules_win_before_jev_and_thresholds_gate_results(self):
        self.assertTrue(hasattr(classify, 'classify_merchant'))
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / 'cache.db'
            with patch('expense_tracker.classify.request_jev') as request:
                for merchant, category in [('Swiggy', 'Food/Groceries'),
                                           ('Zerodha', 'Invest/transfer')]:
                    result = classify.classify_merchant(merchant, cache_path=cache)
                    self.assertEqual((result['category'], result['category_source'],
                                      result['category_confidence'], result['status']),
                                     (category, 'rule', 1.0, 'confirmed'))
                request.assert_not_called()

                for confidence, status in [(0.85, 'confirmed'), (0.68, 'needs_review')]:
                    request.return_value = dict(type='choice', choice='Shopping', confidence=confidence)
                    result = classify.classify_merchant('Mystery ' + str(confidence), cache_path=cache)
                    self.assertEqual((result['category'], result['category_source'], result['status']),
                                     ('Shopping', 'jev', status))

                request.return_value = None
                result = classify.classify_merchant('Unavailable', cache_path=cache)
                self.assertEqual((result['category'], result['category_source'], result['status']),
                                 ('Other', 'unknown', 'needs_review'))

    def test_ledger_thresholds(self):
        from expense_tracker.schema import ensure_db
        from expense_tracker.store import insert_transaction
        with tempfile.TemporaryDirectory() as tmp:
            conn = ensure_db(Path(tmp) / 'ledger.db')
            self.addCleanup(conn.close)
            for i, (confidence, source, category, status) in enumerate([
                (.85, 'jev', 'Shopping', 'confirmed'),
                (.68, 'jev', 'Shopping', 'needs_review'),
                (.59, 'unknown', 'Other', 'needs_review')]):
                insert_transaction(conn, dict(date='2026-10-06', merchant='Mystery', amount='10',
                    source_id=str(i), category='Shopping', category_source='jev', category_confidence=confidence))
                row = conn.execute('SELECT * FROM transactions WHERE source_id=?', (str(i),)).fetchone()
                self.assertEqual((row['category'], row['category_source'], row['status']), (category, source, status))

    def test_cache_validation_and_privacy(self):
        from expense_tracker.jev import CATEGORIES
        with tempfile.TemporaryDirectory() as tmp, patch('expense_tracker.classify.request_jev') as request:
            cache = Path(tmp) / 'cache.db'
            request.return_value = dict(type='choice', choice='Shopping', confidence=.8)
            classify.classify_merchant('Unique Merchant', cache_path=cache)
            classify.classify_merchant(' unique merchant ', cache_path=cache)
            self.assertEqual(request.call_count, 1)
            self.assertEqual(cache.stat().st_mode & 0o777, 0o600)

            classify.classify_merchant('OTP 123456\nsecret body', cache_path=cache)
            self.assertEqual(request.call_count, 1)
            classify.classify_merchant('Unique 123456', cache_path=cache)
            self.assertNotIn('123456', request.call_args.args[0])

            for i, answer in enumerate([
                {}, [], dict(type='noul', choice='Shopping', confidence=.9),
                dict(type='choice', choice=[], confidence=.9),
                *[dict(type='choice', choice='Shopping', confidence=c)
                  for c in (True, '0.9', float('nan'), float('inf'), -1, 1.1)],
                dict(type='choice', choice='Shopping', confidence=.9, probabilities={'bad': 1}),
                dict(type='choice', choice='Shopping', confidence=.9, probabilities={c: 0 for c in CATEGORIES}),
            ]):
                request.return_value = answer
                result = classify.classify_merchant('Invalid merchant ' + str(i), cache_path=cache)
                self.assertEqual((result['category'], result['status']), ('Other', 'needs_review'))

    def test_request_contract_and_failure(self):
        from expense_tracker.jev import request_jev
        answer = dict(type='choice', choice='Shopping', confidence=.85)
        with patch.dict(os.environ, {'TYPESAFE_API_KEY': 'test-only'}), patch(
                'expense_tracker.jev.urllib.request.urlopen',
                return_value=io.BytesIO(json.dumps({'answers': {'category': answer}}).encode())) as http:
            self.assertEqual(request_jev('merchant'), answer)
            request = http.call_args.args[0]
            self.assertEqual(json.loads(request.data)['state'], {'merchant': 'merchant'})
            self.assertEqual(json.loads(request.data)['questions']['category']['type'], 'choice')

        with patch.dict(os.environ, {'TYPESAFE_API_KEY': ''}), patch(
                'expense_tracker.jev.urllib.request.urlopen') as http:
            self.assertIsNone(request_jev('merchant'))
            http.assert_not_called()

        with patch.dict(os.environ, {'TYPESAFE_API_KEY': 'test-only'}), patch(
                'expense_tracker.jev.urllib.request.urlopen', side_effect=OSError('private response')):
            self.assertIsNone(request_jev('merchant'))

    def test_parser_confidence_overrides_rule(self):
        from expense_tracker.schema import ensure_db
        with tempfile.TemporaryDirectory() as tmp:
            conn = ensure_db(Path(tmp) / 'ledger.db')
            self.addCleanup(conn.close)
            row = classify.classify_transaction(conn, dict(merchant='Swiggy', parser_confidence=.5))
            self.assertEqual((row['category_source'], row['category_confidence'], row['status']),
                             ('rule', 1.0, 'needs_review'))
