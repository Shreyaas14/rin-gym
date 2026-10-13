"""Prefix-only observations: labels and future period boundaries stay outside the API."""
from __future__ import annotations

import bisect
from dataclasses import dataclass
import numpy as np
from .data import FIFA, RAW, epoch, read, score_batches


@dataclass(frozen=True)
class Observation:
    cutoff: float
    home_goals: int
    away_goals: int
    phase: str
    elapsed_period_minutes: float
    observed_events: int
    latest_event_timestamp: float


class Replay:
    def __init__(self, events):
        self.events = events
        self.batches = list(score_batches(events))
        self.times = [b[0] for b in self.batches]
        if self.times != sorted(set(self.times)):
            raise ValueError('Event batches must be ordered and unique')

    def observe(self, cutoff: float) -> Observation:
        count = bisect.bisect_right(self.times, cutoff)
        hg = ag = observed = 0
        phase = 'pregame'
        period_start = None
        latest = float('-inf')
        # No feature access to an event beyond the cutoff, even for clock computation.
        for timestamp, h, a, batch in self.batches[:count]:
            hg, ag, latest = h, a, timestamp
            observed += len(batch)
            for event in batch:
                kind = event['TypeLocalized'][0]['Description']
                if kind == 'Start Time' and event['Period'] in [3, 5]:
                    phase = 'first_half' if event['Period'] == 3 else 'second_half'
                    period_start = timestamp
                elif kind == 'End Time' and event['Period'] in [3, 5]:
                    phase = 'halftime' if event['Period'] == 3 else 'finished'
                    period_start = None
        elapsed = max(0.0, (cutoff - period_start) / 60) if period_start is not None else 0.0
        return Observation(cutoff, hg, ag, phase, elapsed, observed, latest)


def asof(history, cutoff, max_age):
    """Return the last sample at or before cutoff. Stale/missing values stay missing."""
    idx = bisect.bisect_right([p['t'] for p in history], cutoff) - 1
    if idx < 0:
        return None
    point = history[idx]
    age = cutoff - point['t']
    if age < 0 or age > max_age:
        return None
    return point


def market_vector(histories, cutoff, max_age):
    points = [asof(h, cutoff, max_age) for h in histories]
    if any(p is None for p in points):
        return None
    raw = np.array([p['p'] for p in points], dtype=float)
    mass = float(raw.sum())
    if mass <= 0 or not np.all(np.isfinite(raw)) or np.any(raw < 0) or np.any(raw > 1):
        return None
    # Independent binary markets are made coherent explicitly; retain raw values.
    return {'probabilities': raw / mass, 'raw': raw, 'mass': mass, 'times': [p['t'] for p in points], 'max_age': max(cutoff - p['t'] for p in points)}


def load_game(row):
    from .governance import assert_development_matches
    assert_development_matches([row['match_id']])
    events = read(FIFA / f"fifa_{row['match_id']}_timeline.json")['Event']
    histories = [read(RAW / m['file'])['history'] for m in row['markets']]
    return Replay(events), histories


def build_cases(manifest, config):
    from .governance import assert_development_matches
    # Check the complete input before reading even the first game's state or labels.
    # Materialize iterators so validation cannot consume the evaluation input.
    manifest = list(manifest)
    assert_development_matches(row['match_id'] for row in manifest)
    cases, exclusions = [], []
    for row in manifest:
        if row['status'] != 'ok':
            continue
        replay, histories = load_game(row)
        kickoff = epoch(row['kickoff_utc'])
        end = epoch(row['regulation_end_utc'])
        cutoff = kickoff - config['pregame_cutoff_before_actual_kickoff_seconds']
        prior = market_vector(histories, cutoff, config['pregame_price_max_age_seconds'])
        if prior is None:
            exclusions.append({'match_id': row['match_id'], 'split': row['split'], 'reason': 'missing_or_stale_pregame_prices'})
            continue
        t = kickoff + config['snapshot_burn_in_seconds']
        skipped = eligible = 0
        while t < end:
            now = replay.observe(t)
            # Phase is queried at the decision time, rather than taking a future clock boundary as input.
            if now.phase in ['first_half', 'second_half']:
                eligible += 1
                market = market_vector(histories, t, config['price_max_age_seconds'])
                if market is None:
                    skipped += 1
                else:
                    observations = {delay: replay.observe(t - delay) for delay in config['observation_delay_seconds']}
                    cases.append({'match_id': row['match_id'], 'split': row['split'], 'decision_time': t, 'target': row['target'], 'prior': prior, 'market': market, 'observations': observations, 'phase': now.phase, 'phase_minute': now.elapsed_period_minutes})
            t += config['snapshot_interval_seconds']
        exclusions.append({'match_id': row['match_id'], 'split': row['split'], 'reason': 'snapshot_coverage', 'eligible': eligible, 'stale_or_missing': skipped, 'included': eligible - skipped})
    return cases, exclusions
