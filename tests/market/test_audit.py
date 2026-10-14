"""Independent journal replay verifies the authoritative book and ledger."""

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from app.market.agents import Valuations
from app.market.audit import verify
from app.market.data import Fixture
from app.market.engine import Exchange
from app.market.session import Session


class AuditTests(unittest.TestCase):
    def setUp(self):
        fixture = Fixture()
        self.session = Session(fixture, Valuations(fixture.source_hash))
        self.session.command({'epoch': self.session.epoch, 'command_id': 'buy', 'action': 'order',
                              'outcome': 'YES', 'side': 'buy', 'price': 99, 'size': 3, 'tif': 'IOC'})

    def test_open_session_reproduces_positions_reservations_and_book(self):
        export = self.session.export()
        original = copy.deepcopy(export)
        report = verify(export)
        self.assertEqual(report['valid'], True)
        self.assertGreater(report['fills'], 0)
        self.assertFalse(report['settled'])
        self.assertEqual(report['total_cash'], 300_000)
        self.assertEqual(export, original)

    def test_complete_session_replays_through_regulation_settlement(self):
        self.session.advance(self.session.fixture.duration)
        report = verify(self.session.export())
        self.assertTrue(report['settled'])
        self.assertGreater(report['orders'], 100)
        self.assertGreater(report['fills'], 0)
        self.assertEqual(report['total_cash'], 300_000)

    def test_tampered_fill_cash_book_and_missing_events_are_rejected(self):
        original = self.session.export()
        fill_index = next(i for i, e in enumerate(original['exchange']) if e['type'] == 'fill')
        variants = []
        changed = copy.deepcopy(original)
        changed['exchange'][fill_index]['price'] += 1
        variants.append(changed)
        changed = copy.deepcopy(original)
        changed['state']['exchange']['accounts']['user']['cash'] += 1
        variants.append(changed)
        changed = copy.deepcopy(original)
        changed['state']['exchange']['bids'][0]['remaining'] += 1
        variants.append(changed)
        changed = copy.deepcopy(original)
        del changed['exchange'][fill_index]
        variants.append(changed)
        changed = copy.deepcopy(original)
        changed['exchange'] = changed['exchange'][:-1]
        variants.append(changed)
        for document in variants:
            with self.subTest(document=document['exchange'][-1]):
                with self.assertRaises(ValueError):
                    verify(document)

    def test_standalone_fill_and_invalid_json_shapes_are_rejected(self):
        original = self.session.export()
        fill = next(e for e in original['exchange'] if e['type'] == 'fill')
        malformed = [None, [], {}, {'schema': 'wrong'},
                     {**original, 'exchange': [fill]}, {**original, 'exchange': [None]},
                     {**original, 'state': []}, {**original, 'exchange': 'not-a-list'}]
        for document in malformed:
            with self.subTest(document=document):
                with self.assertRaises(ValueError):
                    verify(document)

    @staticmethod
    def engine_export(exchange, history=None):
        book = exchange.snapshot()
        midpoint = ((book['bids'][0]['price'] + book['asks'][0]['price']) / 2
                    if book['bids'] and book['asks'] else None)
        history = history or []
        mark = midpoint
        if mark is None:
            mark = next((p['midpoint'] for p in reversed(history) if p['midpoint'] is not None), None)
        return {'schema': 'market-session/v1', 'exchange': exchange.journal,
                'state': {'market': {'midpoint': midpoint, 'history': history},
                          'exchange': exchange.snapshot(mark=mark)}}

    def test_both_outcomes_and_automatic_ioc_selftrade_cancellations(self):
        for outcome in (0, 1):
            exchange = Exchange()
            exchange.submit('anchor', 'sell', 62, 5)
            exchange.submit('user', 'buy', 62, 8, 'IOC')
            exchange.submit('pulse', 'sell', 65, 2)
            exchange.submit('pulse', 'buy', 66, 2)  # incoming self-match cancellation
            exchange.submit('anchor', 'buy', 40, 2)
            exchange.settle(outcome)
            report = verify(self.engine_export(exchange))
            self.assertTrue(report['settled'])
            self.assertEqual(report['total_cash'], 300_000)
            self.assertEqual(report['fills'], 1)

    def test_owner_and_refresh_cancel_reasons_share_the_same_ledger_effect(self):
        exchange = Exchange()
        exchange.submit('anchor', 'buy', 40, 2)
        exchange.cancel_all()
        export = self.engine_export(exchange)
        self.assertTrue(verify(export)['valid'])
        export['exchange'][-1]['reason'] = 'owner'
        self.assertTrue(verify(export)['valid'])
        export['exchange'][-1]['reason'] = 'self_trade'
        with self.assertRaises(ValueError):
            verify(export)

    def test_empty_book_uses_last_valid_mark_and_ignores_runtime_trade_time(self):
        self.session.exchange.cancel_all()
        export = self.session.export()
        self.assertIsNone(export['state']['market']['midpoint'])
        self.assertTrue(verify(export)['valid'])
        export['state']['exchange']['trades'][0]['time'] = 'runtime-only'
        self.assertTrue(verify(export)['valid'])

    def test_boolean_sequence_cannot_impersonate_integer_one(self):
        export = copy.deepcopy(self.session.export())
        export['exchange'][0]['sequence'] = True
        with self.assertRaises(ValueError):
            verify(export)

    def test_cli_reads_export_and_returns_machine_readable_report(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'journal.json'
            path.write_text(json.dumps(self.session.export()))
            result = subprocess.run([sys.executable, '-m', 'app.market.audit', str(path)],
                                    capture_output=True, text=True, check=True)
            self.assertTrue(json.loads(result.stdout)['valid'])
            path.write_text('{}')
            result = subprocess.run([sys.executable, '-m', 'app.market.audit', str(path)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertFalse(json.loads(result.stderr)['valid'])


if __name__ == '__main__':
    unittest.main()
