"""Read the replay database and publish one anonymous Parquet catalog.

DuckDB is an optional ingestion dependency. Neither the web server nor the
portable catalog imports it. Parquet uses the existing PyArrow dependency.
Source databases are opened read-only.
"""

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path

from .data import Fixture, POLICY_VERSION, TYPE_NAMES, anonymous_id, make_payload
from .storage import write_catalog


def _require_polymarket(connection):
    """Reject other venues before the Polymarket-specific reference import.

    Original replay databases predate venue tags, so absent tags remain valid.
    Once a database declares a venue, every declaration must be Polymarket.
    """
    columns = set(connection.execute('''
        SELECT table_name, column_name FROM information_schema.columns
        WHERE table_schema = 'main'
    ''').fetchall())

    def check(value):
        if value is not None and (not isinstance(value, str) or value.lower() != 'polymarket'):
            raise ValueError('This demo importer supports Polymarket reference data only')

    if ('replay_metadata', 'metadata') in columns:
        for (raw,) in connection.execute('SELECT metadata FROM replay_metadata').fetchall():
            if raw is None:
                continue
            try:
                metadata = json.loads(raw) if isinstance(raw, str) else raw
                if not isinstance(metadata, dict):
                    raise ValueError('Replay metadata must be a JSON object')
                check(metadata.get('market_venue'))
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError('Invalid replay venue metadata') from exc
    for table in ('matches', 'events', 'markets', 'prices'):
        for column in ('venue', 'market_venue'):
            if (table, column) in columns:
                for (value,) in connection.execute(f'SELECT DISTINCT {column} FROM {table}').fetchall():
                    check(value)


def import_dataset(database, output):
    try:
        import duckdb
    except ImportError as exc:
        raise ValueError('Dataset import requires the optional duckdb package') from exc
    database, output = Path(database), Path(output)
    if not database.is_file():
        raise ValueError('Replay database was not found')
    if output.exists():
        raise ValueError('Output already contains a catalog; choose a new output file')
    connection = duckdb.connect(str(database), read_only=True)
    try:
        _require_polymarket(connection)
        matches = connection.execute('''
            SELECT match_id, home_team_id, away_team_id, regulation_home_score, regulation_away_score
            FROM matches WHERE timeline_status = 'ok'
        ''').fetchall()
        events = connection.execute('''
            SELECT match_id, event_time_utc, period_code, event_type_code, event_type_label,
                   team_id, player_id, sub_player_id, sub_team_id, home_score, away_score,
                   x_raw, y_raw, location_status
            FROM events ORDER BY event_time_utc, source_index
        ''').fetchall()
        markets = connection.execute('''
            SELECT match_id, market_id, token.token_id
            FROM markets CROSS JOIN UNNEST(tokens) AS t(token)
            WHERE market_family = 'moneyline' AND hda_outcome = 'home'
              AND token.outcome = 'Yes' AND token.status = 'included'
        ''').fetchall()
        prices = connection.execute('''
            WITH selected AS MATERIALIZED (
                SELECT m.match_id, m.market_id, token.token_id AS token_id
                FROM markets m CROSS JOIN UNNEST(m.tokens) AS t(token)
                WHERE m.market_family = 'moneyline' AND m.hda_outcome = 'home'
                  AND token.outcome = 'Yes' AND token.status = 'included'
            )
            SELECT p.match_id, p.timestamp, p.price
            FROM prices p JOIN selected s ON p.match_id = s.match_id
                 AND p.market_id = s.market_id AND p.token_id = s.token_id
            ORDER BY p.timestamp
        ''').fetchall()
    finally:
        connection.close()
    grouped_events, grouped_prices, token_counts = defaultdict(list), defaultdict(list), Counter()
    for match_id, _market_id, _token_id in markets:
        token_counts[match_id] += 1
    columns = ('time', 'period', 'type_code', 'type', 'team_id', 'player_id', 'sub_player_id',
               'sub_team_id', 'home_goals', 'away_goals', 'x', 'y', 'location_status')
    for match_id, *values in events:
        row = dict(zip(columns, values))
        if row['time'] is None:
            raise ValueError('Source dataset includes an event without a timestamp')
        grouped_events[match_id].append(row)
    for match_id, when, price in prices:
        if (type(when) not in (int, float) or not math.isfinite(when)
                or type(price) not in (int, float) or not math.isfinite(price) or not 0 <= price <= 1):
            raise ValueError('Source dataset includes an invalid reference price')
        grouped_prices[match_id].append({'t': when, 'p': price})
    bundles = []
    unknown, event_count, price_count = 0, 0, 0
    for number, (match_id, home_id, away_id, home_score, away_score) in enumerate(
            sorted(matches, key=lambda row: anonymous_id(row[0])), 1):
        if home_id is None or away_id is None or home_id == away_id:
            raise ValueError('Source match has invalid structured team identities')
        if token_counts[match_id] != 1 or not grouped_prices[match_id]:
            raise ValueError('Source game requires exactly one home-win YES reference token')
        payload = make_payload(grouped_events[match_id], home_id, away_id, match_id,
                               label=f'Game {number:03}', prices=grouped_prices[match_id])
        fixture = Fixture.from_payload(payload)
        if fixture.state(fixture.end)['score'] != [home_score, away_score]:
            raise ValueError('Structured regulation score disagrees with the source period-end event')
        unknown += sum(e['type_code'] not in TYPE_NAMES for e in payload['events'])
        bundles.append(payload)
        event_count += len(payload['events'])
        price_count += len(payload['prices'])
    if not bundles or len({game['id'] for game in bundles}) != len(bundles):
        raise ValueError('Source dataset has no games or duplicate game identities')
    write_catalog(bundles, output)
    return {'games': len(bundles), 'events': event_count, 'reference_points': price_count,
            'unrecognized_event_types': unknown, 'policy_version': POLICY_VERSION}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Publish an anonymous replay Parquet catalog from a read-only database')
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = import_dataset(args.database, args.output)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, sort_keys=True))


if __name__ == '__main__':
    main()
