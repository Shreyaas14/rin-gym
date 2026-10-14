"""Anonymous data publication and runtime catalog boundaries."""

import copy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from app.market.catalog import Catalog
from app.market.data import Fixture, TIME_ORIGIN, make_payload
from app.market.import_dataset import import_dataset
from app.market.storage import read_catalog, write_catalog, SCHEMA
import pyarrow as pa
import pyarrow.parquet as pq


def source_rows(shift=0):
    # Identity-bearing descriptions are deliberately never interpreted.
    rows = []
    for offset, period, code, score in ((0, 3, 7, [0, 0]), (100, 3, 12, [None, None]),
                                       (200, 3, 0, [1, 0]), (2700, 3, 8, [1, 0]),
                                       (3600, 5, 7, [1, 0]), (3700, 5, 5, [None, None]),
                                       (6400, 5, 8, [1, 0]), (6500, 7, 0, [9, 8]),
                                       (6600, 5, 26, [9, 8])):
        rows.append({'time': 1_700_000_000 + shift + offset, 'period': period, 'type_code': code,
                     'type': 'Private Country Player Fullname', 'team_id': 'private-home-id',
                     'player_id': 'private-player-id', 'sub_player_id': 'private-sub-player-id' if code == 5 else None,
                     'sub_team_id': 'private-home-id', 'home_goals': score[0], 'away_goals': score[1],
                     'x': 33.0, 'y': 66.0, 'location_status': 'present_unverified',
                     'description': 'Full name from Private Country scored the goal'})
    return rows


def payload(source_id='private-match-id', label='Game 001'):
    return make_payload(source_rows(), 'private-home-id', 'private-away-id', source_id, label,
                        [{'t': 1_700_000_000 + 50, 'p': .6}, {'t': 1_700_000_000 + 400, 'p': .7}])


def publish(folder, games):
    write_catalog(games, folder / 'catalog.parquet')


def density_payload(number, active_events=10, half_seconds=2700, extra_events=0):
    """Independent durations/counts make ranking rules visible in small games."""
    game = payload(f'density-{number}', f'Game {number:03}')
    start = TIME_ORIGIN
    second = start + half_seconds + 900
    end = second + half_seconds
    template = game['events'][0]

    def event(when, period, code, typ):
        return {**template, 'time': float(when), 'period': period, 'type_code': code, 'type': typ, 'score': [0, 0]}

    events = [event(start, 3, 7, 'Start Time'), event(start + half_seconds, 3, 8, 'End Time'),
              event(second, 5, 7, 'Start Time'), event(end, 5, 8, 'End Time')]
    events.extend(event(start + 1 + i, 3, 12, 'Attempt at Goal') for i in range(active_events - 4))
    events.extend(event(start - 20 - i, 2, 79, 'Coin Toss') for i in range(extra_events))
    game['events'] = sorted(events, key=lambda e: e['time'])
    return game


class AnonymousFixtureTests(unittest.TestCase):
    def test_structured_templates_replace_all_names_ids_dates_and_commentary(self):
        game = payload()
        fixture = Fixture.from_payload(game)
        serialized = json.dumps({'payload': game, 'state': fixture.state(fixture.end)})
        for secret in ('Private Country', 'Fullname', 'Full name', 'private-home-id', 'private-player-id',
                       'private-match-id', '2023-', '1700000000'):
            self.assertNotIn(secret, serialized)
        self.assertEqual(fixture.kickoff, TIME_ORIGIN)
        self.assertEqual(fixture.state(fixture.end)['teams'], {'home': 'Team A', 'away': 'Team B'})
        self.assertIn('Player A01', fixture.events[0]['description'])
        self.assertIn('Player A02', next(e for e in fixture.events if e['type'] == 'Substitution')['description'])
        self.assertEqual(fixture.events[0]['id'], 'e0001')
        self.assertFalse(any(e['type'] == 'Match end' for e in fixture.events))
        self.assertEqual(fixture.state(fixture.end)['score'], [1, 0])

    def test_future_events_and_prices_do_not_change_prefix_observations(self):
        game = payload()
        fixture = Fixture.from_payload(game)
        changed = copy.deepcopy(game)
        changed['events'][-1]['score'] = [99, 98]
        changed['prices'][-1]['p'] = .99
        changed_fixture = Fixture.from_payload(changed)
        when = fixture.kickoff + 150
        self.assertEqual(fixture.observation(when), changed_fixture.observation(when))
        self.assertEqual(fixture.state(when)['score'], [0, 0])
        self.assertEqual(fixture.reference(when), changed_fixture.reference(when))
        self.assertTrue(all(point['time'] <= when for point in fixture.reference(when)['reference']))

    def test_null_scores_carry_and_simultaneous_score_rows_use_group_max(self):
        rows = source_rows()
        rows.insert(3, {**rows[2], 'home_goals': 0, 'away_goals': None, 'type_code': 1})
        fixture = Fixture.from_payload(make_payload(rows, 'private-home-id', 'private-away-id', 'x'))
        self.assertEqual(fixture.state(fixture.kickoff + 201)['score'], [1, 0])
        self.assertEqual(fixture.state(fixture.kickoff + 3701)['score'], [1, 0])

    def test_placeholder_ambiguous_and_missing_locations_never_render(self):
        rows = source_rows()
        for index, status in enumerate(('placeholder', 'ambiguous_zero', 'missing')):
            rows[index]['location_status'] = status
        fixture = Fixture.from_payload(make_payload(rows, 'private-home-id', 'private-away-id', 'x'))
        self.assertTrue(all(e['x'] is None and e['y'] is None for e in fixture.events[:3]))
        self.assertEqual((fixture.events[3]['x'], fixture.events[3]['y']), (33, 66))

    def test_default_fixture_and_policy_hash_are_anonymous(self):
        fixture = Fixture()
        self.assertEqual(fixture.kickoff, TIME_ORIGIN)
        self.assertEqual(fixture.state(fixture.end)['time_origin'], TIME_ORIGIN)
        serialized = json.dumps(fixture.state(fixture.end))
        for forbidden in ('Czechia', 'South Africa', '2026-', '400021440', '43995', '43883'):
            self.assertNotIn(forbidden, serialized)
        game = payload()
        changed = copy.deepcopy(game)
        changed['events'][1]['x'] += 1
        self.assertNotEqual(Fixture.from_payload(game).source_hash, Fixture.from_payload(changed).source_hash)
        changed['policy_version'] = 'old-policy'
        with self.assertRaises(ValueError):
            Fixture.from_payload(changed)


class CatalogTests(unittest.TestCase):
    def test_unique_selection_and_public_metadata_hide_paths_scores_and_dates(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            games = [payload(), payload('second-private-match', 'Game 002')]
            publish(folder, games)
            catalog = Catalog(folder / 'catalog.parquet')
            listed = catalog.list_games()
            self.assertEqual(len(listed), 2)
            self.assertNotEqual(listed[0]['id'], listed[1]['id'])
            self.assertEqual(catalog.default_id, min(game['id'] for game in games))
            fields = {'id', 'label', 'event_count', 'has_reference', 'rank',
                      'active_event_count', 'active_minutes', 'event_density'}
            self.assertTrue(all(set(item) == fields for item in listed))
            for game in games:
                self.assertEqual(catalog.load(game['id']).game_id, game['id'])
            for invalid in ('../../etc/passwd', 'unknown-game', None, []):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    catalog.load(invalid)

    def test_density_ranking_can_reverse_total_event_count(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            slow = density_payload(1, active_events=100, half_seconds=3600)
            dense = density_payload(2, active_events=90, half_seconds=2700)
            publish(folder, [slow, dense])
            catalog = Catalog(folder / 'catalog.parquet')
            first, second = catalog.list_games()
            self.assertEqual(first['id'], dense['id'])
            self.assertEqual(first['event_density'], 1)
            self.assertEqual(first['active_minutes'], 90)
            self.assertEqual(first['active_event_count'], 90)
            self.assertGreater(second['event_count'], first['event_count'])
            self.assertLess(second['event_density'], first['event_density'])
            self.assertEqual(catalog.default_id, dense['id'])

    def test_active_metrics_exclude_prematch_halftime_and_other_periods(self):
        game = density_payload(3, active_events=12, half_seconds=3000, extra_events=7)
        template = game['events'][0]
        second = TIME_ORIGIN + 3000 + 900
        game['events'].extend([
            {**template, 'time': TIME_ORIGIN + 3100, 'period': 4},  # halftime
            {**template, 'time': TIME_ORIGIN + 10, 'period': 7},  # ET code during window
            {**template, 'time': TIME_ORIGIN + 20, 'period': 9},  # shootout code
            {**template, 'time': TIME_ORIGIN + 3101, 'period': 3},  # outside own half
            {**template, 'time': second - 1, 'period': 5},
        ])
        fixture = Fixture.from_payload(game)
        metrics = fixture.activity_metrics()
        self.assertEqual(metrics['active_event_count'], 12)
        self.assertEqual(metrics['active_minutes'], 100)  # stoppage included; halftime omitted
        self.assertEqual(metrics['event_density'], .12)

    def test_top_ten_only_rejects_known_eleventh_and_report_includes_all(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            games = [density_payload(i, active_events=20 + i) for i in range(1, 13)]
            publish(folder, games)
            catalog = Catalog(folder / 'catalog.parquet')
            selected = catalog.list_games()
            self.assertEqual(catalog.candidate_count, 12)
            self.assertEqual(len(selected), 10)
            self.assertEqual([item['rank'] for item in selected], list(range(1, 11)))
            self.assertEqual(selected[0]['id'], games[-1]['id'])
            report = catalog.ranking_report()
            self.assertEqual(len(report), 12)
            self.assertEqual(sum(item['selected'] for item in report), 10)
            self.assertTrue(all(set(item) == set(selected[0]) | {'selected'} for item in report))
            for item in report[10:]:
                with self.assertRaises(ValueError):
                    catalog.load(item['id'])
            for item in selected:
                self.assertEqual(catalog.load(item['id']).game_id, item['id'])

    def test_equal_density_ties_use_counts_then_opaque_id(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            same_a = density_payload(4, active_events=10, half_seconds=3000)
            same_b = density_payload(5, active_events=10, half_seconds=3000)
            twice = density_payload(6, active_events=20, half_seconds=6000)
            # All density=.1, so the 20-event game wins the first tie.
            publish(folder, [same_b, same_a, twice])
            listed = Catalog(folder / 'catalog.parquet').list_games()
            self.assertEqual(listed[0]['id'], twice['id'])
            self.assertEqual([item['id'] for item in listed[1:]], sorted([same_a['id'], same_b['id']]))

    def test_parquet_payloads_preserve_hashes_and_cannot_spoof_metrics(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            games = [density_payload(7, active_events=20), density_payload(8, active_events=30)]
            publish(folder, games)
            path = folder / 'catalog.parquet'
            self.assertEqual(read_catalog(path), games)
            for game, restored in zip(games, read_catalog(path)):
                self.assertEqual(Fixture.from_payload(game).source_hash, Fixture.from_payload(restored).source_hash)
            self.assertEqual(Catalog(path).default_id, games[1]['id'])
            self.assertNotIn('event_density', pq.read_schema(path).names)
            games[0]['event_density'] = 999
            with self.assertRaisesRegex(ValueError, 'conversion changes'):
                write_catalog(games, folder / 'spoofed.parquet')

    def test_ranked_fixture_instances_do_not_share_mutable_events(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            game = density_payload(9)
            publish(folder, [game])
            catalog = Catalog(folder / 'catalog.parquet')
            first = catalog.load(game['id'])
            first.events[0]['type'] = 'Edited externally'
            self.assertEqual(catalog.load(game['id']).events[0]['type'], 'Start Time')

    def test_cached_catalog_detects_changes_and_missing_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            game = payload()
            publish(folder, [game])
            path = folder / 'catalog.parquet'
            catalog = Catalog(path)
            path.write_bytes(path.read_bytes() + b' ')
            with self.assertRaises(ValueError):
                catalog.load(game['id'])
            path.unlink()
            with self.assertRaises(ValueError):
                catalog.load(game['id'])
            with self.assertRaisesRegex(ValueError, 'not found'):
                Catalog(path)

    def test_corrupt_wrong_schema_and_payload_hash_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'bad.parquet'
            path.write_bytes(b'not parquet')
            with self.assertRaises(ValueError):
                Catalog(path)
            pq.write_table(pa.table({'unrecognized': [1]}), path)
            with self.assertRaisesRegex(ValueError, 'schema'):
                Catalog(path)
            game = payload()
            table = pa.Table.from_pylist([{**game, 'payload_sha256': '0' * 64}], schema=SCHEMA)
            pq.write_table(table, path)
            with self.assertRaisesRegex(ValueError, 'content hash'):
                Catalog(path)

    def test_empty_duplicate_and_existing_publications_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            path = folder / 'catalog.parquet'
            with self.assertRaises(ValueError):
                write_catalog([], path)
            publish(folder, [payload()])
            original = path.read_bytes()
            with self.assertRaises(ValueError):
                publish(folder, [payload('other')])
            self.assertEqual(path.read_bytes(), original)
            duplicate = folder / 'duplicate.parquet'
            write_catalog([payload(), payload()], duplicate)
            with self.assertRaisesRegex(ValueError, 'Duplicate'):
                Catalog(duplicate)



@unittest.skipUnless(importlib.util.find_spec('duckdb'), 'Optional duckdb ingestion dependency is not installed')
class DatasetImportTests(unittest.TestCase):
    def test_non_polymarket_venue_tags_are_rejected_before_publication(self):
        import duckdb
        cases = ('metadata', 'market_venue_column', 'price_venue_column', 'mixed_venue_column')
        for tag in cases:
            with self.subTest(tag=tag), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                database, output = root / 'source.duckdb', root / 'published.parquet'
                connection = duckdb.connect(str(database))
                if tag == 'metadata':
                    connection.execute('CREATE TABLE replay_metadata(metadata JSON)')
                    connection.execute('INSERT INTO replay_metadata VALUES (?)', [json.dumps({'market_venue': 'kalshi'})])
                elif tag == 'market_venue_column':
                    connection.execute('CREATE TABLE markets(market_venue VARCHAR)')
                    connection.execute("INSERT INTO markets VALUES ('kalshi')")
                else:
                    connection.execute('CREATE TABLE prices(venue VARCHAR)')
                    connection.execute("INSERT INTO prices VALUES ('kalshi')")
                    if tag == 'mixed_venue_column':
                        connection.execute("INSERT INTO prices VALUES ('polymarket')")
                connection.close()
                original_hash = hashlib.sha256(database.read_bytes()).hexdigest()
                with self.assertRaisesRegex(ValueError, 'Polymarket reference data only'):
                    import_dataset(database, output)
                self.assertFalse(output.exists())
                self.assertEqual(hashlib.sha256(database.read_bytes()).hexdigest(), original_hash)

    def test_two_actual_database_rows_publish_distinct_portable_anonymous_games(self):
        import duckdb
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database, output = root / 'source.duckdb', root / 'published.parquet'
            connection = duckdb.connect(str(database))
            connection.execute('CREATE TABLE matches(match_id VARCHAR, home_team_id VARCHAR, away_team_id VARCHAR, regulation_home_score INT, regulation_away_score INT, timeline_status VARCHAR)')
            connection.execute('CREATE TABLE events(match_id VARCHAR, event_time_utc TIMESTAMPTZ, period_code INT, event_type_code INT, event_type_label VARCHAR, team_id VARCHAR, player_id VARCHAR, sub_player_id VARCHAR, sub_team_id VARCHAR, home_score INT, away_score INT, x_raw DOUBLE, y_raw DOUBLE, location_status VARCHAR, source_index INT)')
            connection.execute('CREATE TABLE markets(match_id VARCHAR, market_id VARCHAR, market_family VARCHAR, hda_outcome VARCHAR, tokens STRUCT(token_id VARCHAR,outcome VARCHAR,status VARCHAR)[])')
            connection.execute('CREATE TABLE prices(match_id VARCHAR,market_id VARCHAR,token_id VARCHAR,timestamp DOUBLE,price DOUBLE)')
            for number in (1, 2):
                match_id = f'private-source-{number}'
                connection.execute('INSERT INTO matches VALUES (?,?,?,?,?,?)', [match_id, 'private-home-id', 'private-away-id', 1, 0, 'ok'])
                for index, row in enumerate(source_rows(shift=number * 86400)):
                    connection.execute('INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                                       [match_id, datetime.fromtimestamp(row['time'], timezone.utc), row['period'], row['type_code'], row['type'],
                                        row['team_id'], row['player_id'], row['sub_player_id'], row['sub_team_id'], row['home_goals'], row['away_goals'],
                                        row['x'], row['y'], row['location_status'], index])
                connection.execute("INSERT INTO markets VALUES (?,?,'moneyline','home',[{'token_id':'private-token','outcome':'Yes','status':'included'}])", [match_id, 'private-market'])
                connection.execute('INSERT INTO prices VALUES (?,?,?,?,?)', [match_id, 'private-market', 'private-token', 1_700_000_000 + number * 86400 + 100, .6])
            connection.close()
            original_hash = hashlib.sha256(database.read_bytes()).hexdigest()
            report = import_dataset(database, output)
            self.assertEqual(report['games'], 2)
            self.assertEqual(report['unrecognized_event_types'], 0)
            self.assertEqual(hashlib.sha256(database.read_bytes()).hexdigest(), original_hash)
            catalog = Catalog(output)
            self.assertEqual(len(catalog.list_games()), 2)
            for item in catalog.list_games():
                fixture = catalog.load(item['id'])
                self.assertEqual(fixture.kickoff, TIME_ORIGIN)
                self.assertEqual(fixture.state(fixture.end)['score'], [1, 0])
            published = json.dumps(read_catalog(output))
            for forbidden in ('private-', 'Fullname', 'Private Country', '2023-'):
                self.assertNotIn(forbidden, published)
            # An import may not replace a live Parquet catalog.
            manifest_before = output.read_bytes()
            with self.assertRaises(ValueError):
                import_dataset(database, output)
            self.assertEqual(output.read_bytes(), manifest_before)


if __name__ == '__main__':
    unittest.main()
