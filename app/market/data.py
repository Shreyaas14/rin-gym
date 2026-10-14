"""Anonymous, portable match data and prefix-only replay state.

Runtime needs only the standard library. Dataset ingestion may use DuckDB, but
published payloads contain aliases and a synthetic clock rather than identities.
"""
from bisect import bisect_right
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / 'data/examples/world-cup/czechia-south-africa'
POLICY_VERSION = 'anonymous-match/v1'
PAYLOAD_SCHEMA = 'market-game/v1'
TIME_ORIGIN = 3600
MAJOR = {'Goal!', 'Penalty Goal', 'Own goal', 'Penalty Awarded', 'Red card', 'Second Caution - Red Card'}
TYPE_NAMES = {
    0: 'Goal!', 1: 'Assist', 2: 'Yellow card', 3: 'Red card', 4: 'Second Caution - Red Card',
    5: 'Substitution', 6: 'Penalty Awarded', 7: 'Start Time', 8: 'End Time',
    9: 'Pause Time', 10: 'Resume Time', 12: 'Attempt at Goal', 15: 'Offside',
    16: 'Corner', 17: 'Goal Prevention', 18: 'Foul', 26: 'Match end',
    33: 'Attempt Missed', 34: 'Own goal', 39: 'Goal!', 41: 'Penalty Goal',
    49: 'Attempt Missed', 51: 'Penalty missed', 57: 'Goal Prevention',
    60: 'Penalty missed', 65: 'Penalty missed', 71: 'VAR', 72: 'Penalty Conceded!',
    78: 'Resume', 79: 'Coin Toss', 80: 'Coin Toss', 81: 'Coin Toss', 82: 'Coin Toss',
    83: 'Delay', 85: 'Delay',
}
SAFE_TYPES = set(TYPE_NAMES.values()) | {'Penalty Missed', 'Injury', 'VAR', 'Save', 'Recorded event'}


def timestamp(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc).timestamp() if value.tzinfo is None else value.timestamp()
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def content_hash(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def anonymous_id(source_id):
    return 'g' + hashlib.sha256((POLICY_VERSION + ':' + str(source_id)).encode()).hexdigest()[:16]


def description(event):
    labels = []
    if event.get('team') in ('home', 'away'):
        labels.append('Team A' if event['team'] == 'home' else 'Team B')
    if event.get('player'):
        labels.append('Player ' + event['player'])
    if event.get('sub_player'):
        labels.append('Player ' + event['sub_player'])
    labels.append(event['type'])
    return ' · '.join(labels)


def make_payload(rows, home_id, away_id, source_id, label='Game 001', prices=None):
    """Normalize structured source rows; never publish source commentary.

    Rows use time, period, type_code, type, team_id, player_id, home_goals,
    away_goals, x, y, and location_status. IDs are consumed only to assign aliases.
    """
    rows = sorted(rows, key=lambda row: timestamp(row['time']))
    kickoff = next(timestamp(row['time']) for row in rows
                   if row['period'] == 3 and row['type_code'] == 7)
    ends = [timestamp(row['time']) for row in rows if row['period'] == 5 and row['type_code'] == 8]
    if len(ends) != 1:
        raise ValueError('Game requires exactly one regulation-end event')
    aliases = {'home': {}, 'away': {}}
    events = []
    for row in rows:
        when = timestamp(row['time'])
        if when > ends[0]:
            continue
        team = 'home' if row.get('team_id') == home_id else 'away' if row.get('team_id') == away_id else None
        def alias(side, source_player):
            if side is None or source_player is None:
                return None
            key = str(source_player)
            players = aliases[side]
            if key not in players:
                players[key] = ('A' if side == 'home' else 'B') + f'{len(players) + 1:02}'
            return players[key]
        player = alias(team, row.get('player_id'))
        sub_side = ('home' if row.get('sub_team_id') == home_id else
                    'away' if row.get('sub_team_id') == away_id else team)
        sub_player = alias(sub_side, row.get('sub_player_id'))
        typ = row.get('type')
        typ = typ if typ in SAFE_TYPES else TYPE_NAMES.get(row['type_code'], 'Recorded event')
        x, y = row.get('x'), row.get('y')
        status = row.get('location_status')
        # Only an explicitly meaningful source location may be placed on pitch.
        valid = (status in ('valid', 'observed', 'provided', 'present_unverified')
                 and type(x) in (int, float) and type(y) in (int, float)
                 and math.isfinite(x) and math.isfinite(y)
                 and 0 <= x <= 100 and 0 <= y <= 100 and (x, y) != (0, 0))
        event = {'id': f'e{len(events) + 1:04}', 'time': round(TIME_ORIGIN + when - kickoff, 6),
                 'period': row['period'], 'type_code': row['type_code'], 'type': typ,
                 'team': team, 'player': player, 'sub_player': sub_player,
                 'score': [row.get('home_goals'), row.get('away_goals')],
                 'x': x if valid else None, 'y': y if valid else None,
                 'location_status': 'observed' if valid else 'unavailable'}
        events.append(event)
    shifted_prices = [{'t': round(TIME_ORIGIN + timestamp(p['t']) - kickoff, 6), 'p': p['p']}
                      for p in (prices or []) if timestamp(p['t']) <= ends[0]]
    payload = {'schema': PAYLOAD_SCHEMA, 'policy_version': POLICY_VERSION,
               'id': anonymous_id(source_id), 'label': label, 'kickoff': TIME_ORIGIN,
               'events': events, 'prices': sorted(shifted_prices, key=lambda p: p['t'])}
    return payload


class Fixture:
    """Load a portable payload with ``Fixture.from_payload(payload)``.

    ``Fixture()`` retains the original demo fixture as an anonymous fallback.
    ``source_hash`` hashes the anonymous policy and complete normalized payload.
    """
    def __init__(self, folder=FIXTURE, payload=None):
        if payload is None:
            raw = (Path(folder) / 'fifa_400021440_timeline.json').read_bytes()
            source = json.loads(raw)['Event']
            # The fixture lacks a match row. Infer its two sides only from
            # structured goal score changes, never names or free-text commentary.
            side_ids, previous = {}, [0, 0]
            for event in sorted(source, key=lambda e: timestamp(e['Timestamp'])):
                score = [event.get('HomeGoals'), event.get('AwayGoals')]
                if event['Type'] in (0, 41):
                    for i in (0, 1):
                        if score[i] is not None and score[i] > previous[i]:
                            side_ids[i] = event.get('IdTeam')
                previous = [score[i] if score[i] is not None else previous[i] for i in (0, 1)]
            if len(side_ids) != 2:
                raise ValueError('Fallback requires identifiable structured home and away goals')
            rows = [{'time': e['Timestamp'], 'period': e['Period'], 'type_code': e['Type'],
                     'type': e['TypeLocalized'][0]['Description'], 'team_id': e.get('IdTeam'),
                     'player_id': e.get('IdPlayer'), 'sub_player_id': e.get('IdSubPlayer'),
                     'sub_team_id': e.get('IdSubTeam'), 'home_goals': e.get('HomeGoals'),
                     'away_goals': e.get('AwayGoals'), 'x': e.get('PositionX'), 'y': e.get('PositionY'),
                     'location_status': 'provided' if 'PositionX' in e and 'PositionY' in e else 'unavailable'}
                    for e in source]
            prices = json.loads((Path(folder) / 'fifa_prices_1897106.json').read_text())['history']
            payload = make_payload(rows, side_ids[0], side_ids[1], hashlib.sha256(raw).hexdigest(), prices=prices)
        self._load(payload)

    @classmethod
    def from_payload(cls, payload):
        return cls(payload=payload)

    def _load(self, payload):
        if (not isinstance(payload, dict) or payload.get('schema') != PAYLOAD_SCHEMA
                or payload.get('policy_version') != POLICY_VERSION or payload.get('kickoff') != TIME_ORIGIN):
            raise ValueError('Unsupported anonymous game payload')
        if (not isinstance(payload.get('id'), str) or len(payload['id']) != 17
                or payload['id'][0] != 'g' or any(c not in '0123456789abcdef' for c in payload['id'][1:])):
            raise ValueError('Invalid anonymous game ID')
        label = payload.get('label')
        if not isinstance(label, str) or not label.startswith('Game ') or not label[5:].isdigit():
            raise ValueError('Invalid anonymous game label')
        self.game_id, self.label = payload['id'], label
        self.question = 'Will Team A win in regulation time?'
        self.source_hash = content_hash(payload)
        self.kickoff = TIME_ORIGIN
        self.raw, self.events = [], []
        for index, source in enumerate(sorted(payload['events'], key=lambda e: e['time'])):
            typ, team, player = source['type'], source.get('team'), source.get('player')
            if typ not in SAFE_TYPES or team not in ('home', 'away', None):
                raise ValueError('Invalid anonymous event type or side')
            for player_alias in (player, source.get('sub_player')):
                if player_alias is not None and (not isinstance(player_alias, str) or len(player_alias) < 3
                                                or player_alias[0] not in 'AB' or not player_alias[1:].isdigit()):
                    raise ValueError('Invalid anonymous player alias')
            when = source['time']
            if type(when) not in (int, float) or not math.isfinite(when):
                raise ValueError('Invalid event clock')
            score = source['score']
            if not isinstance(score, list) or len(score) != 2 or any(v is not None and (type(v) is not int or v < 0) for v in score):
                raise ValueError('Invalid structured score')
            x, y = source.get('x'), source.get('y')
            if source.get('location_status') != 'observed' or not (
                    type(x) in (int, float) and type(y) in (int, float)
                    and 0 <= x <= 100 and 0 <= y <= 100 and (x, y) != (0, 0)):
                x = y = None
            event = {'id': f'e{index + 1:04}', 'time': when, 'period': source['period'],
                     'type': typ, 'team': team, 'player': player, 'sub_player': source.get('sub_player'), 'x': x, 'y': y,
                     'minute': '', 'description': ''}
            event['description'] = description(event)
            self.events.append(event)
            self.raw.append({'EventId': event['id'], 'Timestamp': datetime.fromtimestamp(when, timezone.utc).isoformat(),
                             'Period': event['period'], 'Type': source['type_code'],
                             'TypeLocalized': [{'Description': typ}], 'HomeGoals': score[0], 'AwayGoals': score[1]})
        ends = [e['time'] for e, r in zip(self.events, self.raw) if r['Period'] == 5 and r['Type'] == 8]
        if len(ends) != 1:
            raise ValueError('Game requires one explicit regulation end')
        self.end = ends[0]
        if any(e['time'] > self.end for e in self.events):
            raise ValueError('Anonymous payload includes post-regulation events')
        self.start = self.kickoff - 3
        self.times = [e['time'] for e in self.events]
        self.boundaries = [(e['period'], e['type'], e['time']) for e in self.events if e['type'] in ('Start Time', 'End Time')]
        self.half_end = next(t for period, typ, t in self.boundaries if period == 3 and typ == 'End Time')
        self.second_start = next(t for period, typ, t in self.boundaries if period == 5 and typ == 'Start Time')
        if not self.kickoff < self.half_end < self.second_start < self.end:
            raise ValueError('Invalid period boundaries')
        for event in self.events:
            if event['time'] >= self.second_start:
                minute, boundary = 45 + (event['time'] - self.second_start) / 60, 90
            elif event['time'] <= self.half_end:
                minute, boundary = max(0, (event['time'] - self.kickoff) / 60), 45
            else:
                minute, boundary = 45, 45
            whole = int(minute)
            event['minute'] = (f'{boundary}+{whole - boundary}' if whole > boundary else str(whole)) + "'"
        # Terminal state must carry an explicit score, not a final match-row label.
        terminal_raw = next(r for r in self.raw if r['Period'] == 5 and r['Type'] == 8)
        if terminal_raw['HomeGoals'] is None or terminal_raw['AwayGoals'] is None:
            raise ValueError('Regulation-end score is missing')
        rounds = {self.kickoff}
        for a, b in ((self.kickoff, self.half_end), (self.second_start, self.end)):
            rounds.update(range(int(a) + 15, int(b), 15))
            rounds.add(a)
        rounds.update(e['time'] for e in self.events
                      if (e['period'] == 3 and self.kickoff <= e['time'] <= self.half_end)
                      or (e['period'] == 5 and self.second_start <= e['time'] < self.end))
        self.rounds = sorted(t for t in rounds if self.start <= t < self.end)
        self.segments = []
        movie, previous = 0., self.start
        for when in sorted({e['time'] for e in self.events if e['time'] >= self.start}):
            span = max(.12, min(3.4, (when - previous) / 110))
            self.segments.append((movie, movie + span, previous, when))
            movie += span
            hold = 2.8 if any(e['time'] == when and e['type'] in MAJOR for e in self.events) else .65
            self.segments.append((movie, movie + hold, when, when))
            movie += hold
            previous = when
        self.duration = movie
        self.prices = sorted(payload.get('prices', []), key=lambda p: p['t'])
        if any(type(p['p']) not in (int, float) or not math.isfinite(p['p']) or not 0 <= p['p'] <= 1
               or type(p['t']) not in (int, float) or not math.isfinite(p['t']) for p in self.prices):
            raise ValueError('Invalid reference price')
        self.price_times = [p['t'] for p in self.prices]

    def time_at(self, movie):
        for a, b, start, end in self.segments:
            if movie <= b:
                return start + (end - start) * max(0, (movie - a) / (b - a))
        return self.end

    def movie_at(self, when):
        for a, b, start, end in self.segments:
            if when <= end:
                return a + (b - a) * (when - start) / (end - start) if end > start else a
        return self.duration

    def activity_metrics(self):
        """Observed event density during active regulation, including stoppage.

        Count period 3/5 events only inside that half's actual source boundaries.
        Halftime, pre-match, extra time, and shootouts do not enter either term.
        Boundary start/end events count because they are recorded replay events.
        """
        active_minutes = ((self.half_end - self.kickoff) + (self.end - self.second_start)) / 60
        active_event_count = sum(
            (event['period'] == 3 and self.kickoff <= event['time'] <= self.half_end)
            or (event['period'] == 5 and self.second_start <= event['time'] <= self.end)
            for event in self.events
        )
        return {'active_event_count': active_event_count, 'active_minutes': active_minutes,
                'event_density': active_event_count / active_minutes}

    def state(self, when):
        n = bisect_right(self.times, when)
        past, score = self.events[:n], [0, 0]
        group, group_time = [], None
        for event in self.raw[:n] + [None]:
            when_key = event['Timestamp'] if event else None
            if group and when_key != group_time:
                for i, key in enumerate(('HomeGoals', 'AwayGoals')):
                    known = [row[key] for row in group if row[key] is not None]
                    if known:
                        score[i] = max(known)
                group = []
            if event:
                group.append(event)
                group_time = when_key
        if when >= self.end:
            phase = 'Full time'
        elif when >= self.second_start:
            phase = 'Second half'
        elif when >= self.half_end:
            phase = 'Half time'
        elif when >= self.kickoff:
            phase = 'First half'
        else:
            phase = 'Kickoff'
        minute = (45 + (when - self.second_start) / 60 if when >= self.second_start else
                  45 if when >= self.half_end else max(0, (when - self.kickoff) / 60))
        clock = f'{int(minute):02}:{int((minute % 1) * 60):02}'
        return {'timestamp': when, 'utc': f'Replay {clock}', 'clock': clock, 'phase': phase,
                'score': score, 'events': past, 'current': past[-1] if past else None,
                'progress': max(0, min(1, (when - self.start) / (self.end - self.start))),
                'teams': {'home': 'Team A', 'away': 'Team B'},
                'game': {'id': self.game_id, 'label': self.label}, 'time_origin': TIME_ORIGIN}

    def observation(self, when):
        state = self.state(when)
        clean = [{'age_seconds': round(when - e['time'], 2), 'side': e['team'], 'type': e['type']}
                 for e in state['events'] if e['time'] >= self.start]
        counts = {side: dict(Counter(e['type'] for e in clean if e['side'] == side)) for side in ('home', 'away')}
        return {'period': state['phase'], 'minute': int(state['clock'].split(':')[0]), 'score': state['score'],
                'counts': counts, 'recent_events': [e for e in clean if e['age_seconds'] <= 600],
                'pre_match_expected_goals': {'home': 1.6, 'away': 1.1}}

    def reference(self, when):
        seen = self.prices[:bisect_right(self.price_times, when)]
        latest = seen[-1] if seen else None
        age = when - latest['t'] if latest else None
        history = []
        for price in seen:
            if price['t'] < self.start - 90:
                continue
            if history and price['t'] - history[-1]['time'] > 90:
                history.append({'time': history[-1]['time'] + 90, 'price': None})
            history.append({'time': price['t'], 'price': price['p'] * 100})
        if latest and age > 90:
            history.append({'time': latest['t'] + 90, 'price': None})
        return {'reference': history, 'reference_age': age,
                'reference_price': latest['p'] * 100 if latest and age <= 90 else None}
