"""Read-only feasibility audit of existing development artifacts; no inference."""
from collections import Counter
import hashlib
import itertools
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
REPO = ROOT / 'rin-gym-forecast-only'
sys.path.insert(0, str(REPO))
from app.trade_env.dataset import snapshot
from app.trade_env.tasks import read_tasks
from experiments.forecast_only.core import guard
import pyarrow.parquet as pq


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit():
    source = REPO / 'artifacts/earlier-run-risk/tasks.jsonl.gz'
    tasks = [t for t in read_tasks(source)
             if t['split'] == 'val' and not t['demo'] and t['position'] == 'flat']
    ids = sorted({t['match_id'] for t in tasks})
    ledger = ROOT / 'rin-gym-simulation/data/evals/ledger.sqlite3'
    before = guard(ledger, ids)
    data = snapshot(REPO / 'data/raw/release-data-2026-10-13-v1', 'fifa-markets')
    # Project only development schedule/team metadata, never read test outcomes.
    matches = pq.read_table(data / 'matches.parquet',
        columns=['match_id', 'home_team_id', 'away_team_id', 'scheduled_kickoff_utc'],
        filters=[('match_id', 'in', ids)]).to_pylist()
    assert len(matches) == len(ids) == 72
    teams = {r['match_id']: {r['home_team_id'], r['away_team_id']} for r in matches}
    grouped = {}
    for t in tasks:
        grouped.setdefault(t['decision'], []).append(t)
    pairs = list(itertools.combinations(ids, 2))
    kickoff_groups = Counter(r['scheduled_kickoff_utc'].isoformat() for r in matches)
    timing = {}
    for decision, rows in sorted(grouped.items()):
        choices = list(itertools.combinations(rows, 2))
        timing[decision] = {
            'matches': len(rows), 'phase_matched_pairs': len(choices),
            'phase_matched_triples': sum(1 for _ in itertools.combinations(rows, 3)),
            'pairs_with_decision_gap_at_most_s': {
                str(tol): sum(abs(a['t'] - b['t']) <= tol for a, b in choices)
                for tol in [0, 30, 60, 90]},
        }
    old = ROOT / 'training-results/run-3-results/forecast-rules-run-3/repeated-validation/matches.jsonl'
    saved = [json.loads(line) for line in old.read_text().splitlines()]
    assert len(saved) == 216 and {r['match_id'] for r in saved} == set(ids)
    assert all(r['split'] == 'val' and not r['demo'] for r in saved)
    steps = [s for r in saved for s in r['steps']]
    pilot_path = REPO / 'artifacts/forecast-only/predictions.jsonl'
    predictions = [json.loads(line) for line in pilot_path.read_text().splitlines()]
    pilot_tasks = {t['id']: t for t in read_tasks(REPO / 'artifacts/forecast-only/tasks.jsonl.gz')}
    assert all(pilot_tasks[p['task_id']]['split'] == 'val' for p in predictions)
    pilot = {}
    for model in sorted({p['model'] for p in predictions}):
        rows = [p for p in predictions if p['model'] == model]
        pilot[model] = {'responses': len(rows), 'invalid_probs': sum(p['probs'] is None for p in rows)}
    after = guard(ledger, ids)
    assert after == before
    return {
        'scope': 'Read-only development feasibility audit; no model experiment or inference.',
        'sources_sha256': {str(p.relative_to(ROOT)): sha(p) for p in [source, old, pilot_path]},
        'holdout_before_after': before,
        'matches': len(ids), 'unique_flat_decisions': len(tasks),
        'distinct_match_pairs': len(pairs),
        'distinct_pairs_without_shared_team': sum(not teams[a] & teams[b] for a, b in pairs),
        'scheduled_kickoff_group_sizes': dict(sorted(Counter(kickoff_groups.values()).items())),
        'decision_timing': timing,
        'saved_earlier_run': {'repeats': 3, 'game_replays': len(saved),
            'responses': len(steps), 'valid_responses': sum(s['valid'] for s in steps),
            'responses_with_probs': sum(s['probs'] is not None for s in steps),
            'next_goal_labeled_flat_decisions': sum(t['scoring']['forecast']['next_goal'] is not None for t in tasks)},
        'forecast_only_pilot': pilot,
        'timing_note': 'Decision proximity is a candidate count, not proof of a common live information set. Tasks omit original quote timestamps; rebuild common-cutoff observations from the frozen release before execution claims.',
    }


if __name__ == '__main__':
    result = audit()
    destination = Path(__file__).with_name('feasibility.json')
    destination.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
