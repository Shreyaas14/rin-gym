"""Game changes reset paper state without crossing source or identity boundaries."""
import copy
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from app.market.agents import Valuations, local_value
from app.market.audit import verify
from app.market.data import Fixture, make_payload
from app.market.session import Session
from app.market.server import MarketServer


def example_game(source_id, draw=False):
    def row(at, period, code, goals, side=None, player=None):
        return {'time': at, 'period': period, 'type_code': code,
                'team_id': side, 'player_id': player, 'home_goals': goals[0],
                'away_goals': goals[1], 'x': None, 'y': None,
                'description': 'SECRET_COUNTRY SECRET_PLAYER', 'location_status': 'missing'}
    rows = [row(1000, 3, 7, [0, 0]), row(2000, 3, 0, [1, 0], 'private-a', 'private-player'),
            row(3700, 3, 8, [1, 0]), row(4600, 5, 7, [1, 0])]
    if draw:
        rows.append(row(5000, 5, 0, [1, 1], 'private-b', 'private-player-b'))
    rows.append(row(7300, 5, 8, [1, int(draw)]))
    return Fixture.from_payload(make_payload(rows, 'private-a', 'private-b', source_id,
                                            label='Game 002' if draw else 'Game 001'))


class TestCatalog:
    __test__ = False

    def __init__(self):
        self.fixtures = [example_game('private-match-one'), example_game('private-match-two', True)]
        self.default_id = self.fixtures[0].game_id

    def list_games(self):
        return [{'id': f.game_id, 'label': f.label, 'event_count': len(f.events),
                 'has_reference': bool(f.prices)} for f in self.fixtures]

    def load(self, game_id):
        for fixture in self.fixtures:
            if fixture.game_id == game_id:
                return fixture
        raise ValueError('Unknown game selection')


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.catalog = TestCatalog()
        fixture = self.catalog.fixtures[0]
        self.session = Session(fixture, Valuations(fixture.source_hash), catalog=self.catalog)
        self.command_number = 0

    def command(self, action, **parameters):
        self.command_number += 1
        command = {'epoch': self.session.epoch, 'command_id': str(self.command_number),
                   'action': action, **parameters}
        self.session.command(command)
        return command

    def test_switch_clears_positions_orders_history_and_prior_epoch(self):
        prior_command = self.command('order', outcome='YES', side='buy', price=99, size=3, tif='IOC')
        self.command('order', outcome='YES', side='buy', price=1, size=2, tif='GTC')
        self.command('play')
        old_epoch, old_hash = self.session.epoch, self.session.fixture.source_hash
        selected = self.catalog.fixtures[1]
        self.command('select_game', game_id=selected.game_id)
        state = self.session.snapshot()
        self.assertNotEqual(state['epoch'], old_epoch)
        self.assertNotEqual(self.session.fixture.source_hash, old_hash)
        self.assertFalse(state['playing'])
        self.assertEqual(state['game_id'], selected.game_id)
        self.assertEqual(state['games'], self.catalog.list_games())
        self.assertEqual(state['replay']['score'], [0, 0])
        self.assertEqual(state['exchange']['accounts']['user']['cash'], 100000)
        self.assertEqual(state['exchange']['accounts']['user']['position'], 0)
        self.assertFalse(any(o['owner'] == 'user' for o in state['exchange']['orders']))
        self.assertEqual(state['exchange']['trades'], [])
        with self.assertRaises(ValueError):
            self.session.command(prior_command)
        self.assertTrue(verify(self.session.export())['valid'])

    def test_unknown_selection_leaves_existing_game_and_balance_intact(self):
        self.command('order', outcome='YES', side='buy', price=99, size=1, tif='IOC')
        before = copy.deepcopy(self.session.snapshot())
        for game_id in ('missing', '../../source.json', None, 123):
            with self.subTest(game_id=game_id), self.assertRaises(ValueError):
                self.command('select_game', game_id=game_id)
            self.assertEqual(self.session.snapshot(), before)

    def test_each_game_settles_from_its_own_regulation_score(self):
        for game, outcome in zip(self.catalog.fixtures, (1, 0)):
            self.command('select_game', game_id=game.game_id)
            self.session.advance(game.duration)
            self.assertEqual(self.session.exchange.outcome, outcome)
            self.assertEqual(self.session.cursor, game.end)
            self.assertTrue(verify(self.session.export())['valid'])

    def test_public_state_and_export_use_only_anonymous_labels(self):
        for game in self.catalog.fixtures:
            self.command('select_game', game_id=game.game_id)
            self.session.advance(game.duration)
            document = json.dumps(self.session.export())
            for secret in ('SECRET_COUNTRY', 'SECRET_PLAYER', 'private-a', 'private-b',
                           'private-player', 'private-match-one', 'private-match-two'):
                self.assertNotIn(secret, document)
            state = self.session.snapshot()
            self.assertEqual(state['replay']['teams'], {'home': 'Team A', 'away': 'Team B'})
            self.assertEqual(state['market']['question'], 'Will Team A win in regulation time?')
            self.assertIn('A01', document)

    def test_recorded_mode_rejects_a_different_game_without_mutating(self):
        self.session.mode = 'recorded'
        before = copy.deepcopy(self.session.snapshot())
        with self.assertRaisesRegex(ValueError, 'recording belongs to another game'):
            self.command('select_game', game_id=self.catalog.fixtures[1].game_id)
        self.assertEqual(self.session.snapshot(), before)

    def test_live_mode_is_preserved_and_provider_hash_changes(self):
        class Provider:
            live_available, recorded, model = True, None, 'test-model'

            def __init__(self, source_hash):
                self.source_hash, self.receipts = source_hash, []

            def evaluate(self, state, mode):
                return {agent: {'value': .5, 'model': self.model, 'snapshot_id': 'test', 'error': None}
                        for agent in ('anchor', 'pulse')}

        first = self.catalog.fixtures[0]
        self.session = Session(first, Provider(first.source_hash), mode='live', catalog=self.catalog)
        with patch('app.market.session.Valuations', side_effect=lambda source_hash, model: Provider(source_hash)):
            self.command('select_game', game_id=self.catalog.fixtures[1].game_id)
        self.assertEqual(self.session.mode, 'live')
        self.assertEqual(self.session.valuations.source_hash, self.catalog.fixtures[1].source_hash)
        self.assertFalse(self.session.playing)

    def test_dataset_second_caution_counts_as_a_dismissal(self):
        state = self.catalog.fixtures[0].observation(self.catalog.fixtures[0].kickoff)
        baseline = local_value(state, 'anchor')
        state['counts']['home']['Second Caution - Red Card'] = 1
        self.assertLess(local_value(state, 'anchor'), baseline)
        state['counts']['home'].clear()
        state['counts']['away']['Second Caution - Red Card'] = 1
        self.assertGreater(local_value(state, 'anchor'), baseline)

    def test_recorded_startup_selects_matching_available_game(self):
        selected = self.catalog.fixtures[1]
        with tempfile.TemporaryDirectory() as folder:
            recording = Path(folder) / 'recording.json'
            recording.write_text(json.dumps({'source_hash': selected.source_hash}))
            with patch('app.market.server.Catalog', return_value=self.catalog), \
                    patch('app.market.server.ThreadingHTTPServer.__init__'):
                server = MarketServer(recorded=recording)
            self.assertEqual(server.fixture.game_id, selected.game_id)

    def test_unavailable_recording_rejected_before_socket_binding(self):
        with tempfile.TemporaryDirectory() as folder:
            recording = Path(folder) / 'recording.json'
            recording.write_text(json.dumps({'source_hash': 'not-in-the-selected-games'}))
            with patch('app.market.server.Catalog', return_value=self.catalog), \
                    patch('app.market.server.ThreadingHTTPServer.__init__') as bind:
                with self.assertRaisesRegex(ValueError, 'available selected game'):
                    MarketServer(recorded=recording)
                bind.assert_not_called()


class SelectionHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = TestCatalog()
        cls.server = MarketServer(('127.0.0.1', 0))
        cls.server.catalog = cls.catalog
        cls.server.fixture = cls.catalog.fixtures[0]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def request(self, path='/api/state', command=None, cookie=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        headers = {'Content-Type': 'application/json'}
        if cookie:
            headers['Cookie'] = cookie
        try:
            connection.request('POST' if command else 'GET', path,
                               body=json.dumps(command) if command else None, headers=headers)
            response = connection.getresponse()
            return response.status, response.getheader('Set-Cookie'), json.load(response)
        finally:
            connection.close()

    def test_http_picker_selection_and_stale_command_rejection(self):
        status, _, catalog = self.request('/api/games')
        self.assertEqual(status, 200)
        self.assertEqual(catalog['games'], self.catalog.list_games())
        _, cookie, old = self.request()
        cookie = cookie.split(';')[0]
        command = {'epoch': old['epoch'], 'command_id': 'choose-second', 'action': 'select_game',
                   'game_id': self.catalog.fixtures[1].game_id}
        status, _, selected = self.request('/api/command', command, cookie)
        self.assertEqual(status, 200)
        self.assertEqual(selected['game_id'], command['game_id'])
        self.assertFalse(selected['playing'])
        self.assertNotEqual(selected['epoch'], old['epoch'])
        status, _, stale = self.request('/api/command', {**command, 'action': 'order',
            'outcome': 'YES', 'side': 'buy', 'price': 99, 'size': 1, 'tif': 'IOC'}, cookie)
        self.assertEqual(status, 400)
        self.assertIn('Session changed', stale['command_error'])
        self.assertEqual(stale['exchange']['accounts']['user']['position'], 0)
        status, _, exported = self.request('/api/journal', cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(exported['source_hash'], self.catalog.fixtures[1].source_hash)
        self.assertEqual(exported['game_id'], command['game_id'])
        self.assertTrue(verify(exported)['valid'])

    def test_http_unknown_game_preserves_session_and_hides_source_paths(self):
        _, cookie, state = self.request()
        status, _, result = self.request('/api/command', {'epoch': state['epoch'],
            'command_id': 'bad-game', 'action': 'select_game', 'game_id': '../../private.json'}, cookie.split(';')[0])
        self.assertEqual(status, 400)
        self.assertEqual(result['game_id'], state['game_id'])
        self.assertEqual(result['epoch'], state['epoch'])
        for secret in ('private-a', 'private-b', 'SECRET_COUNTRY', 'SECRET_PLAYER', '../../private.json'):
            self.assertNotIn(secret, json.dumps(result))


if __name__ == '__main__':
    unittest.main()
