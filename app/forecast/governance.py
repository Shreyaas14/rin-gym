"""Local, append-only holdout protocol and prospective prediction receipts.

This prevents accidental reuse through our APIs, not a workspace owner's tampering.
The local clock and submitted source provenance are trusted, not externally attested.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, fields
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3

from rin_gym.paths import workspace_root
from .metrics import scores, summarize, paired_delta, calibration
from .replay import Observation

TARGET = 'regulation_home_draw_away'
SCORING = {'log_loss_clip': 1e-6, 'bootstrap_replicates': 4000, 'seed': 20261012}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError('Expected a timezone-aware ISO timestamp')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError('Expected a timezone-aware ISO timestamp') from exc
    if parsed.tzinfo is None:
        raise ValueError('Timestamps require an explicit timezone')
    return parsed.timestamp()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', value):
        raise ValueError('IDs must use 1–128 letters, digits, dots, colons, underscores or hyphens')
    return value


def exact_keys(value, names):
    if not isinstance(value, dict) or set(value) != set(names):
        raise ValueError('Expected exactly these fields: ' + ', '.join(sorted(names)))


def source_hash(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        raise ValueError('Source hash must be a lowercase SHA-256 hex digest')
    return value


def safe_observation(payload, cutoff, now):
    """Strict allowlist: full replay state/labels/retrospective fields fail closed."""
    exact_keys(payload, ['observation', 'received_utc', 'source_sha256'])
    source_hash(payload['source_sha256'])
    values = payload['observation']
    exact_keys(values, [f.name for f in fields(Observation)])
    for name in ['cutoff', 'latest_event_timestamp', 'elapsed_period_minutes']:
        value = values[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError('Observation numeric fields must be finite')
    for name in ['home_goals', 'away_goals', 'observed_events']:
        if type(values[name]) is not int or values[name] < 0:
            raise ValueError('Observation counts must be nonnegative integers')
    if values['phase'] not in ['pregame', 'first_half', 'halftime', 'second_half']:
        raise ValueError('Only unresolved regulation observations are eligible')
    if values['elapsed_period_minutes'] < 0:
        raise ValueError('Elapsed time must be nonnegative')
    received = timestamp(payload['received_utc'])
    if not (values['latest_event_timestamp'] <= received <= cutoff <= now):
        raise ValueError('Observation must have been received by its cutoff before prediction')
    if values['cutoff'] != cutoff:
        raise ValueError('Observation cutoff differs from the enrolled case')
    return Observation(**values)


def code_fingerprint():
    """Hash executed forecast source; works for dirty checkouts and installed wheels."""
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(__file__).parent.glob('*.py'))}


class Ledger:
    def __init__(self, workspace=None):
        self.workspace = Path(workspace) if workspace is not None else workspace_root()
        self.path = self.workspace / 'data' / 'evals' / 'ledger.sqlite3'

    @contextmanager
    def _connection(self, create=False):
        if create:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        elif not self.path.exists():
            raise ValueError('Evaluation ledger is not initialized; run rin-gym eval init')
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        try:
            db.execute('BEGIN IMMEDIATE')
            if create:
                db.execute('CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY, body TEXT NOT NULL, sha256 TEXT NOT NULL)')
                db.execute("CREATE TRIGGER IF NOT EXISTS no_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT, 'append-only ledger'); END")
                db.execute("CREATE TRIGGER IF NOT EXISTS no_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT, 'append-only ledger'); END")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _events(self, db):
        previous, result = '0' * 64, []
        for seq, body, checksum in db.execute('SELECT seq, body, sha256 FROM events ORDER BY seq'):
            event = json.loads(body)
            if seq != len(result) + 1 or event['previous'] != previous or digest(event) != checksum:
                raise ValueError('Ledger integrity check failed')
            if result and timestamp(event['recorded_utc']) < timestamp(result[-1]['recorded_utc']):
                raise ValueError('Ledger clock moved backwards')
            result.append(event)
            previous = checksum
        return result

    def _append(self, db, events, kind, payload, now=None):
        now = now or utc_now()
        if events and timestamp(now) < timestamp(events[-1]['recorded_utc']):
            raise ValueError('Local clock moved backwards')
        event = {'kind': kind, 'recorded_utc': now, 'payload': payload,
                 'previous': digest(events[-1]) if events else '0' * 64}
        checksum = digest(event)
        db.execute('INSERT INTO events VALUES (?, ?, ?)', (len(events) + 1, canonical(event), checksum))
        return {**event, 'sha256': checksum}

    def events(self):
        with self._connection() as db:
            return self._events(db)

    @staticmethod
    def _one(events, kind):
        return next((e['payload'] for e in events if e['kind'] == kind), None)

    def initialize(self):
        with self._connection(create=True) as db:
            events = self._events(db)
            if events:
                return self.status_from(events)
            self._append(db, events, 'policy', {
                'version': 1, 'target_definition': TARGET,
                'cohort_intent': 'next eligible soccer cohort; enrollment pending fixture selection',
                'historical_benchmark': 'development/regression only; not a fresh holdout',
                'protection': 'local workflow controls; not OS isolation or external timestamp attestation',
            })
            return self.status_from(self._events(db))

    @staticmethod
    def status_from(events):
        cohort = Ledger._one(events, 'cohort')
        return {'state': 'scored' if Ledger._one(events, 'report') else 'enrolled' if cohort else 'pending_enrollment',
                'candidates': [e['payload']['name'] for e in events if e['kind'] == 'candidate'],
                'matches': len(cohort['matches']) if cohort else 0,
                'predictions': sum(e['kind'] == 'prediction' for e in events),
                'resolutions': sum(e['kind'] == 'resolution' for e in events),
                'ledger_sha256': digest(events[-1]) if events else None}

    def register(self, name, artifacts):
        identifier(name)
        paths = sorted({str(Path(p).expanduser().resolve()) for p in artifacts})
        if not paths:
            raise ValueError('Register model/config/prompt artifacts before enrollment')
        hashes = {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in paths}
        with self._connection() as db:
            events = self._events(db)
            if self._one(events, 'cohort'):
                raise ValueError('Candidate registration is locked after cohort enrollment')
            if any(e['kind'] == 'candidate' and e['payload']['name'] == name for e in events):
                raise ValueError('Candidate already registered; use a new versioned name')
            return self._append(db, events, 'candidate', {'name': name, 'artifacts': hashes, 'forecast_code': code_fingerprint()})

    def _known_ids(self):
        path = self.workspace / 'data' / 'manifests' / 'forecast' / 'manifest.json'
        return {str(r['match_id']) for r in json.loads(path.read_text())} if path.exists() else set()

    def enroll(self, cohort):
        """Freeze an explicit future cohort and one primary comparison atomically."""
        exact_keys(cohort, ['name', 'target_definition', 'baseline', 'candidate', 'minimum_games',
                            'noninferiority_margin', 'matches'])
        identifier(cohort['name'])
        if cohort['target_definition'] != TARGET:
            raise ValueError('Only regulation home/draw/away targets are supported')
        if type(cohort['minimum_games']) is not int or cohort['minimum_games'] < 2:
            raise ValueError('Declare minimum_games >= 2 before enrollment')
        margin = cohort['noninferiority_margin']
        if isinstance(margin, bool) or not isinstance(margin, (int, float)) or not math.isfinite(margin) or not 0 <= margin <= 2:
            raise ValueError('Declare a finite Brier noninferiority margin in [0, 2]')
        if not isinstance(cohort['matches'], list) or len(cohort['matches']) < cohort['minimum_games']:
            raise ValueError('The cohort must contain at least minimum_games matches')
        with self._connection() as db:
            events = self._events(db)
            if self._one(events, 'cohort'):
                raise ValueError('Holdout cohort is already frozen')
            registered = {e['payload']['name'] for e in events if e['kind'] == 'candidate'}
            pair = {cohort['baseline'], cohort['candidate']}
            if len(pair) != 2 or not pair <= registered:
                raise ValueError('Register distinct baseline and candidate before enrollment')
            now = utc_now()
            now_t = timestamp(now)
            mids, cids = set(), set()
            known = self._known_ids()
            for match in cohort['matches']:
                exact_keys(match, ['match_id', 'kickoff_utc', 'resolution_not_before_utc', 'cases'])
                mid = identifier(match['match_id'])
                kickoff, resolution = timestamp(match['kickoff_utc']), timestamp(match['resolution_not_before_utc'])
                if mid in mids or mid in known:
                    raise ValueError('Duplicate or previously inspected match cannot enter the holdout')
                mids.add(mid)
                if not now_t < kickoff < resolution:
                    raise ValueError('Enroll future matches before kickoff, with a later resolution boundary')
                if not isinstance(match['cases'], list) or not match['cases']:
                    raise ValueError('Every match needs predeclared prediction cases')
                for case in match['cases']:
                    exact_keys(case, ['case_id', 'observation_cutoff_utc', 'deadline_utc'])
                    cid = identifier(case['case_id'])
                    if cid in cids:
                        raise ValueError('Duplicate case ID')
                    cids.add(cid)
                    if not kickoff <= timestamp(case['observation_cutoff_utc']) < timestamp(case['deadline_utc']) < resolution:
                        raise ValueError('Require kickoff <= cutoff < deadline < resolution boundary')
            payload = {**cohort, 'scoring': SCORING, 'protocol_sha256': digest(cohort), 'scorer_code': code_fingerprint()}
            return self._append(db, events, 'cohort', payload, now)

    @staticmethod
    def _case(cohort, case_id):
        for match in cohort['matches']:
            for case in match['cases']:
                if case['case_id'] == case_id:
                    return match, case
        raise ValueError('Case is not enrolled')

    def record(self, candidate, case_id, payload, probabilities=None, error=None):
        """Receipt time is generated here; callers cannot supply an earlier issue time.

        Invalid/late outputs consume the slot as failures, so retrying cannot erase them.
        Missing slots remain visible in the final coverage report.
        """
        with self._connection() as db:
            events = self._events(db)
            cohort = self._one(events, 'cohort')
            if not cohort or self._one(events, 'report'):
                raise ValueError('Predictions require an enrolled, unscored cohort')
            if candidate not in [cohort['baseline'], cohort['candidate']]:
                raise ValueError('Candidate is outside the frozen comparison')
            match, case = self._case(cohort, case_id)
            if any(e['kind'] == 'prediction' and e['payload']['candidate'] == candidate and e['payload']['case_id'] == case_id for e in events):
                raise ValueError('Prediction slot already recorded; overwrites and retries are forbidden')
            if any(e['kind'] == 'resolution' and e['payload']['match_id'] == match['match_id'] for e in events):
                raise ValueError('Match has already resolved')
            if timestamp(utc_now()) < timestamp(case['observation_cutoff_utc']):
                raise ValueError('Prediction window is not open')
            failure = str(error)[:2000] if error else None
            config = next(e['payload'] for e in events if e['kind'] == 'candidate' and e['payload']['name'] == candidate)
            try:
                if config['forecast_code'] != code_fingerprint():
                    raise ValueError('Forecast code changed after candidate registration')
                for path, checksum in config['artifacts'].items():
                    if hashlib.sha256(Path(path).read_bytes()).hexdigest() != checksum:
                        raise ValueError('Registered candidate artifact changed')
                # Hashing a large model may take time: use the post-validation receipt
                # time rather than reserving an earlier timestamp before the work.
                now = utc_now()
                now_t = timestamp(now)
                safe_observation(payload, timestamp(case['observation_cutoff_utc']), now_t)
                if now_t > timestamp(case['deadline_utc']):
                    raise ValueError('Prediction missed its deadline')
                if not failure:
                    scores([probabilities], [0])
            except (ValueError, TypeError, OSError) as exc:
                failure = str(exc)
            now = utc_now()
            if timestamp(now) > timestamp(case['deadline_utc']):
                failure = 'Prediction missed its deadline'
            record = {'candidate': candidate, 'case_id': case_id, 'match_id': match['match_id'],
                      'status': 'failed' if failure else 'ok', 'error': failure,
                      'probabilities': list(probabilities) if not failure else None,
                      'observation': payload if not failure else None,
                      'candidate_sha256': digest(config)}
            return self._append(db, events, 'prediction', record, now)

    def resolve(self, match_id, target, ended_utc, source_sha256):
        if type(target) is not int or target not in [0, 1, 2]:
            raise ValueError('Target must be integer 0=home, 1=draw, 2=away')
        source_hash(source_sha256)
        with self._connection() as db:
            events = self._events(db)
            cohort = self._one(events, 'cohort')
            if not cohort or self._one(events, 'report'):
                raise ValueError('Resolution requires an enrolled, unscored cohort')
            match = next((m for m in cohort['matches'] if m['match_id'] == match_id), None)
            if not match:
                raise ValueError('Match is not enrolled')
            if any(e['kind'] == 'resolution' and e['payload']['match_id'] == match_id for e in events):
                raise ValueError('Resolution is immutable')
            now = utc_now()
            if not timestamp(match['kickoff_utc']) < timestamp(ended_utc) <= timestamp(now):
                raise ValueError('Resolution must follow the actual end of regulation')
            if timestamp(now) < timestamp(match['resolution_not_before_utc']):
                raise ValueError('Resolution boundary has not passed')
            # A premature match ending makes later forecasts ineligible, not silently valid.
            if any(e['kind'] == 'prediction' and e['payload']['match_id'] == match_id and e['payload']['status'] == 'ok'
                   and timestamp(e['recorded_utc']) >= timestamp(ended_utc) for e in events):
                raise ValueError('A successful prediction was recorded after the declared outcome; adjudicate outside this confirmatory cohort')
            return self._append(db, events, 'resolution', {'match_id': match_id, 'target': target,
                                'ended_utc': ended_utc, 'source_sha256': source_sha256}, now)

    def score(self):
        with self._connection() as db:
            events = self._events(db)
            previous = self._one(events, 'report')
            if previous:
                return previous  # Read the exact frozen result, never re-score after tuning.
            cohort = self._one(events, 'cohort')
            if not cohort:
                raise ValueError('No holdout cohort enrolled')
            if cohort['scorer_code'] != code_fingerprint():
                raise ValueError('Scoring code changed after enrollment; use the frozen environment')
            labels = {e['payload']['match_id']: e['payload']['target'] for e in events if e['kind'] == 'resolution'}
            if set(labels) != {m['match_id'] for m in cohort['matches']}:
                raise ValueError('All enrolled matches must resolve before any scores are released')
            predictions = {(e['payload']['candidate'], e['payload']['case_id']): e['payload']
                           for e in events if e['kind'] == 'prediction'}
            names = [cohort['baseline'], cohort['candidate']]
            cases = [(m['match_id'], c['case_id']) for m in cohort['matches'] for c in m['cases']]
            coverage = {}
            for name in names:
                counts = {'expected': len(cases), 'ok': 0, 'failed': 0, 'missing': 0}
                for _, cid in cases:
                    row = predictions.get((name, cid))
                    counts[row['status'] if row else 'missing'] += 1
                coverage[name] = counts
            common = [(mid, cid) for mid, cid in cases
                      if all(predictions.get((name, cid), {}).get('status') == 'ok' for name in names)]
            metrics, reliability = {}, {}
            if common:
                ids, y = zip(*[(mid, labels[mid]) for mid, _ in common])
                for name in names:
                    p = [predictions[name, cid]['probabilities'] for _, cid in common]
                    metrics[name] = summarize(p, y, ids, cohort['scoring'])
                    reliability[name] = calibration(p, y, ids)
            delta = paired_delta(metrics[names[1]], metrics[names[0]], 'brier', cohort['scoring']) if common else None
            complete = all(c['ok'] == c['expected'] for c in coverage.values())
            enough = len({mid for mid, _ in common}) >= cohort['minimum_games']
            gates = {'complete_coverage': complete, 'minimum_games': enough,
                     'noninferior': bool(complete and enough and delta['ci95'][1] <= cohort['noninferiority_margin']),
                     'improved': bool(complete and enough and delta['ci95'][1] < 0)}
            sensitivity = {}
            if common and len({mid for mid, _ in common}) > 1:
                a = metrics[names[1]]['brier_by_game']
                b = metrics[names[0]]['brier_by_game']
                sensitivity = {mid: sum(a[k] - b[k] for k in a if k != mid) / (len(a) - 1) for mid in a}
            report = {'schema_version': 1, 'cohort': cohort, 'coverage': coverage,
                      'metrics_common_cases_only': metrics, 'calibration_common_cases_only': reliability,
                      'paired_brier_delta': delta, 'leave_one_match_out_delta': sensitivity,
                      'gates': gates, 'ledger_head_before_scoring': digest(events[-1]),
                      'interpretation': 'Incomplete coverage blocks promotion; common-case metrics are descriptive. Match bootstrap does not account for shared-team dependence. Local timestamps and source attestations are trusted.'}
            self._append(db, events, 'report', report)
            return report


def assert_development_matches(match_ids, workspace=None):
    """Always reserve enrolled games, including after the confirmatory readout."""
    ledger = Ledger(workspace)
    if not ledger.path.exists():
        return
    cohort = ledger._one(ledger.events(), 'cohort')
    protected = {m['match_id'] for m in cohort['matches']} if cohort else set()
    overlap = protected & {str(mid) for mid in match_ids}
    if overlap:
        raise ValueError('Protected holdout games cannot enter preparation, fitting, selection, or retrospective evaluation: ' + ', '.join(sorted(overlap)))
