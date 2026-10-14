"""Replay, valuation boundary, and human trading behavior."""

import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from app.market.agents import Valuations
from app.market.data import Fixture, timestamp
from app.market.session import Session


class SessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = Fixture()

    def setUp(self):
        self.session = Session(self.fixture, Valuations(self.fixture.source_hash))
        self.counter = 0

    def command(self, action, **kwargs):
        self.counter += 1
        body = {'epoch': self.session.epoch, 'command_id': str(self.counter), 'action': action, **kwargs}
        self.session.command(body)
        return body

    def assert_conservation(self):
        accounts = self.session.snapshot()['exchange']['accounts'].values()
        self.assertEqual(sum(a['cash'] for a in accounts), 300_000)
        self.assertEqual(sum(a['position'] for a in accounts), 0)
        self.assertEqual(sum(a['pnl'] for a in accounts), 0)
        self.assertTrue(all(a['available'] >= 0 for a in accounts))

    def test_opening_book_is_local_and_two_sided(self):
        state = self.session.snapshot()
        self.assertFalse(state['playing'])
        self.assertEqual(state['mode'], 'local')
        self.assertTrue(state['exchange']['bids'])
        self.assertTrue(state['exchange']['asks'])
        self.assertEqual(state['exchange']['trades'], [])
        self.assertTrue(all(a['source'] == 'Poisson baseline v1' for a in state['agents']))
        self.assert_conservation()

    def test_human_yes_and_no_use_actual_maker_liquidity(self):
        for outcome in ('YES', 'NO'):
            with self.subTest(outcome=outcome):
                self.setUp()
                book = self.session.snapshot()['exchange']
                maker = book['asks' if outcome == 'YES' else 'bids'][0]
                price = maker['price'] if outcome == 'YES' else 100 - maker['price']
                self.command('order', outcome=outcome, side='buy', price=price, size=3, tif='IOC')
                fills = [t for t in self.session.exchange.trades if t['taker'] == 'user']
                self.assertEqual(len(fills), 1)
                self.assertEqual((fills[0]['price'], fills[0]['size'], fills[0]['maker']),
                                 (maker['price'], 3, maker['owner']))
                self.assertEqual(self.session.exchange.accounts['user'].position, 3 if outcome == 'YES' else -3)
                self.assert_conservation()

    def test_opening_depth_supports_a_multi_level_paper_fill(self):
        book = self.session.snapshot()['exchange']
        for side in ('bids', 'asks'):
            self.assertGreaterEqual(len({o['price'] for o in book[side]}), 4)
            self.assertGreaterEqual(sum(o['remaining'] for o in book[side]), 100)
        self.command('order', outcome='YES', side='buy', price=99, size=35, tif='IOC')
        fills = [t for t in self.session.exchange.trades if t['taker'] == 'user']
        self.assertEqual(sum(t['size'] for t in fills), 35)
        self.assertGreater(len({t['price'] for t in fills}), 1)
        self.assert_conservation()

    def test_frequent_rounds_include_events_and_exclude_halftime(self):
        fixture = self.fixture
        self.assertIn(fixture.kickoff + 15, fixture.rounds)
        self.assertFalse(any(fixture.half_end < t < fixture.second_start for t in fixture.rounds))
        events = [e for e in fixture.events if e['type'] == 'Attempt at Goal'
                  and e['period'] == 3 and fixture.kickoff < e['time'] < fixture.half_end]
        self.assertTrue(events)
        self.assertTrue(all(e['time'] in fixture.rounds for e in events))
        initial_round = self.session.round_number
        self.session.advance(fixture.movie_at(fixture.kickoff + 30) + 1e-7)
        self.assertGreaterEqual(self.session.round_number - initial_round, 2)
        self.assert_conservation()

    def test_cancel_and_close_apply_to_the_human_position(self):
        self.command('order', outcome='YES', side='buy', price=1, size=2, tif='GTC')
        order = next(o for o in self.session.exchange.orders.values() if o.owner == 'user')
        self.command('cancel', order_id=order.id)
        self.assertEqual(order.status, 'cancelled')
        self.command('order', outcome='YES', side='buy', price=99, size=3, tif='IOC')
        self.command('close')
        self.assertEqual(self.session.exchange.accounts['user'].position, 0)
        self.assert_conservation()

    def test_close_is_partial_when_book_has_limited_depth(self):
        self.command('order', outcome='YES', side='buy', price=99, size=3, tif='IOC')
        self.session.exchange.cancel_all()
        self.session.exchange.submit('anchor', 'buy', 45, 1)
        self.command('close')
        self.assertEqual(self.session.exchange.accounts['user'].position, 2)
        self.assertIn('1 shares filled', self.session.message)
        self.assert_conservation()

    def test_command_retries_cannot_duplicate_orders_and_reset_changes_epoch(self):
        body = self.command('order', outcome='YES', side='buy', price=99, size=3, tif='IOC')
        state = copy.deepcopy(self.session.exchange.__dict__)
        self.session.command(body)
        self.assertEqual(state, self.session.exchange.__dict__)
        old_epoch = self.session.epoch
        self.command('reset')
        self.assertNotEqual(old_epoch, self.session.epoch)
        self.assertEqual(self.session.exchange.accounts['user'].cash, 100_000)
        self.assertEqual(self.session.exchange.accounts['user'].position, 0)
        with self.assertRaises(ValueError):
            self.session.command(body)
        self.assertEqual(self.session.exchange.trades, [])

    def test_round_refresh_expires_user_orders(self):
        self.command('order', outcome='YES', side='buy', price=1, size=2, tif='GTC')
        order = next(o for o in self.session.exchange.orders.values() if o.owner == 'user')
        self.command('step')
        self.assertEqual(order.status, 'cancelled')
        self.assertFalse(any(o.owner == 'user' and o.status == 'open' for o in self.session.exchange.orders.values()))
        self.assertEqual(self.session.snapshot()['exchange']['accounts']['user']['reserved'], 0)

    def test_empty_book_has_no_fabricated_fill(self):
        self.session.exchange.cancel_all()
        self.command('order', outcome='YES', side='buy', price=99, size=3, tif='IOC')
        state = self.session.snapshot()
        self.assertEqual(state['exchange']['trades'], [])
        self.assertEqual(state['exchange']['accounts']['user']['position'], 0)
        self.assertIsNone(state['market']['midpoint'])
        self.assertTrue(state['market']['mark_stale'])

    def test_full_replay_settles_at_explicit_regulation_boundary(self):
        self.command('order', outcome='YES', side='buy', price=99, size=3, tif='IOC')
        terminal = next(e for e in self.fixture.raw if e['Period'] == 5 and e['Type'] == 8)
        self.assertEqual(timestamp(terminal['Timestamp']), self.fixture.end)
        self.assertFalse(any(e['type'] == 'Match end' for e in self.fixture.events))
        self.session.advance(self.fixture.duration)
        state = self.session.snapshot()
        self.assertTrue(state['exchange']['settled'])
        self.assertEqual(state['replay']['timestamp'], self.fixture.end)
        score = state['replay']['score']
        self.assertEqual(state['exchange']['outcome'], int(score[0] > score[1]))
        self.assertEqual(state['exchange']['orders'], [])
        self.assertTrue(all(a['position'] == 0 for a in state['exchange']['accounts'].values()))
        self.assert_conservation()

    def test_observation_excludes_future_events_identity_and_odds(self):
        fixture = copy.deepcopy(self.fixture)
        when = fixture.kickoff + 60
        before = fixture.observation(when)
        for index, event in enumerate(fixture.events):
            if event['time'] > when:
                fixture.events[index] = {**event, 'type': 'FUTURE_SECRET', 'team': 'away'}
                fixture.raw[index] = {**fixture.raw[index], 'HomeGoals': 99, 'AwayGoals': 98}
        fixture.prices = [{'t': when + 1, 'p': .999}]
        fixture.price_times = [when + 1]
        self.assertEqual(before, fixture.observation(when))
        serialized = json.dumps(before)
        for forbidden in ('FUTURE_SECRET', 'Czechia', 'South Africa', 'Timestamp', 'EventId', 'Polymarket', '1897106'):
            self.assertNotIn(forbidden, serialized)
        self.assertEqual(set(before), {'period', 'minute', 'score', 'counts', 'recent_events', 'pre_match_expected_goals'})
        self.assertTrue(all(p['time'] <= when for p in self.fixture.reference(when)['reference']))

    def test_failed_provider_leaves_no_quotes_and_retry_recovers(self):
        class Provider:
            live_available, recorded, model = True, None, 'test-model'
            fail = True

            def evaluate(self, state, mode):
                return {agent: {'value': None if self.fail else .5, 'model': self.model,
                                'error': 'Unavailable' if self.fail else None, 'snapshot_id': 'test'}
                        for agent in ('anchor', 'pulse')}

        provider = Provider()
        self.session = Session(self.fixture, provider, 'live')
        self.assertTrue(self.session.error)
        self.assertFalse(self.session.playing)
        self.assertEqual(self.session.exchange.snapshot()['orders'], [])
        with self.assertRaises(ValueError):
            self.command('order', outcome='YES', side='buy', price=99, size=1, tif='IOC')
        provider.fail = False
        self.command('retry')
        self.assertIsNone(self.session.error)
        self.assertTrue(self.session.exchange.snapshot()['bids'])
        self.assertTrue(self.session.exchange.snapshot()['asks'])

    def test_retry_without_failed_valuation_is_rejected(self):
        round_number = self.session.round_number
        exchange = copy.deepcopy(self.session.exchange.__dict__)
        with self.assertRaises(ValueError):
            self.command('retry')
        self.assertEqual(self.session.round_number, round_number)
        self.assertEqual(self.session.exchange.__dict__, exchange)

    def test_partial_provider_retry_preserves_human_limits_and_round_priority(self):
        class Provider:
            live_available, recorded, model = True, None, 'test-model'
            fail = True

            def evaluate(self, state, mode):
                return {agent: {'value': None if self.fail and agent == 'pulse' else .5,
                                'model': self.model, 'error': 'Unavailable' if self.fail and agent == 'pulse' else None,
                                'snapshot_id': 'test'} for agent in ('anchor', 'pulse')}

        provider = Provider()
        self.session = Session(self.fixture, provider, 'live')
        self.assertIsNone(self.session.error)
        self.command('order', outcome='YES', side='buy', price=1, size=2, tif='GTC')
        human = next(o for o in self.session.exchange.orders.values() if o.owner == 'user')
        anchor_orders = [o.id for o in self.session.exchange.orders.values() if o.owner == 'anchor' and o.status == 'open']
        round_number = self.session.round_number
        provider.fail = False
        self.command('retry')
        self.assertEqual(self.session.round_number, round_number)
        self.assertEqual(human.status, 'open')
        self.assertTrue(all(self.session.exchange.orders[order_id].status == 'open' for order_id in anchor_orders))
        self.assertTrue(any(o.owner == 'pulse' and o.status == 'open' for o in self.session.exchange.orders.values()))
        self.assertTrue(all(a['error'] is None for a in self.session.agents.values()))
        self.assert_conservation()


class ValuationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = Fixture()
        cls.state = cls.fixture.observation(cls.fixture.kickoff)

    def test_recording_requires_fixture_hash_and_model_compatibility(self):
        snapshot_id = hashlib.sha256(json.dumps(self.state, sort_keys=True).encode()).hexdigest()
        recording = {'schema': 'market-valuations/v1', 'source_hash': self.fixture.source_hash,
                     'model': 'jev-1.13.0', 'valuations': {snapshot_id: {
                         agent: {'value': .6, 'model': 'jev-1.13.0'} for agent in ('anchor', 'pulse')}}}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'recording.json'
            path.write_text(json.dumps(recording))
            values = Valuations(self.fixture.source_hash, path).evaluate(self.state, 'recorded')
            self.assertTrue(all(v['value'] == .6 and v['error'] is None for v in values.values()))
            with self.assertRaises(ValueError):
                Valuations('different-fixture', path)
            with self.assertRaises(ValueError):
                Valuations(self.fixture.source_hash, path, model='different-model')
            recording['model'] = 'different-model'
            path.write_text(json.dumps(recording))
            values = Valuations(self.fixture.source_hash, path, model='different-model').evaluate(self.state, 'recorded')
            self.assertTrue(all(v['value'] is None and v['error'] for v in values.values()))

    def live_evaluate(self, answer=None, error=None):
        requests = []

        class Response(io.BytesIO):
            pass

        class Opener:
            def open(self, request, timeout):
                requests.append((request, timeout))
                if error is not None:
                    raise error
                return Response(json.dumps(answer).encode())

        with patch.dict('os.environ', {'TYPESAFE_API_KEY': 'fake-key-for-test-only'}), patch(
                'app.market.agents.urllib.request.build_opener', return_value=Opener()):
            values = Valuations(self.fixture.source_hash).evaluate(self.state, 'live')
        return values, requests

    def test_live_request_uses_anonymous_state_and_noul_shape(self):
        answer = {'model': 'jev-1.13.0', 'answers': {'home_win': {'type': 'noul', 'noul': .62}}}
        values, requests = self.live_evaluate(answer)
        self.assertEqual(len(requests), 2)
        self.assertTrue(all(v['value'] == .62 and not v['error'] for v in values.values()))
        for request, timeout in requests:
            self.assertEqual(request.full_url, 'https://api.typesafe.ai/v1/systemone')
            self.assertEqual(request.method, 'POST')
            self.assertEqual(timeout, 10)
            payload = json.loads(request.data)
            self.assertEqual(payload['state'], self.state)
            self.assertEqual(payload['questions']['home_win']['type'], 'noul')
            self.assertEqual(payload['model'], 'jev-1.13.0')
            self.assertNotIn('fake-key-for-test-only', json.dumps(payload))
        self.assertNotIn('fake-key-for-test-only', json.dumps(values))

    def test_live_rejects_out_of_bounds_boolean_wrong_type_and_wrong_version(self):
        for probability in (-.1, 1.1, True, float('nan')):
            answer = {'model': 'jev-1.13.0', 'answers': {'home_win': {'type': 'noul', 'noul': probability}}}
            values, _ = self.live_evaluate(answer)
            self.assertTrue(all(v['value'] is None and v['error'] for v in values.values()))
        for model, kind in (('other-version', 'noul'), ('jev-1.13.0', 'choice')):
            answer = {'model': model, 'answers': {'home_win': {'type': kind, 'noul': .5}}}
            values, _ = self.live_evaluate(answer)
            self.assertTrue(all(v['value'] is None and v['error'] for v in values.values()))

    def test_provider_http_error_does_not_disclose_response_or_credentials(self):
        secret = 'fake-provider-response-sensitive-content'
        error = urllib.error.HTTPError('https://api.typesafe.ai', 401, secret, {}, io.BytesIO(secret.encode()))
        values, _ = self.live_evaluate(error=error)
        self.assertNotIn(secret, json.dumps(values))
        self.assertNotIn('fake-key-for-test-only', json.dumps(values))
        self.assertTrue(all('HTTP 401' in v['error'] for v in values.values()))


if __name__ == '__main__':
    unittest.main()
