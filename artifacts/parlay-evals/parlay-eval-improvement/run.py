"""Fit cheap two-leg forecast corrections using disjoint development folds."""
from collections import Counter
import hashlib
import itertools
import json
from pathlib import Path
import sys

import numpy as np
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[2]
REPO = ROOT / 'rin-gym-forecast-only'
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))
from app.trade_env.dataset import snapshot
from app.trade_env.tasks import read_tasks
from experiments.forecast_only.core import guard

SIDES = ('home', 'draw', 'away')
SEEDS = [20261013, 20261014, 20261015]
TEMPERATURES = [.5, .75, 1., 1.25, 1.5, 2.]
DRAW_MULTIPLIERS = [.75, 1., 1.25]
WEIGHTS = [0., .1, .25, .5, 1.]
FAMILIES = {
    'market': [(0., 1., 1.)],
    'model': [(1., 1., 1.)],
    'calibrated_market': [(0., t, d) for t in TEMPERATURES for d in DRAW_MULTIPLIERS],
    'calibrated_model': [(1., t, d) for t in TEMPERATURES for d in DRAW_MULTIPLIERS],
    'blend': [(w, 1., 1.) for w in WEIGHTS],
    'calibrated_blend': [(w, t, d) for w in WEIGHTS for t in TEMPERATURES for d in DRAW_MULTIPLIERS],
}


def transform(model, market, params):
    """No labels, outcomes or future prices reach the forecast transformation."""
    w, temperature, draw_multiplier = params
    p = w*model+(1-w)*market
    logs = np.log(np.maximum(p, 1e-8))/temperature
    logs[..., 1] += np.log(draw_multiplier)
    p = np.exp(logs-logs.max(axis=-1, keepdims=True))
    return p/p.sum(axis=-1, keepdims=True)


def indices(members, teams):
    pairs = [(a, b) for a, b in itertools.combinations(members, 2) if not teams[a] & teams[b]]
    return np.array(pairs, dtype=int)


def scores(p, labels, pairs, market):
    """Loss per repeat and pair; nine-cell independent result joint."""
    a, b = pairs.T
    pa, pb = p[:, a], p[:, b]
    qa = np.take_along_axis(pa, np.broadcast_to(labels[a][None, :, None], (p.shape[0], len(a), 1)), -1)[..., 0]
    qb = np.take_along_axis(pb, np.broadcast_to(labels[b][None, :, None], (p.shape[0], len(b), 1)), -1)[..., 0]
    brier = (pa**2).sum(-1)*(pb**2).sum(-1)-2*qa*qb+1
    log = -np.log(np.maximum(qa*qb, 1e-6))
    picks = np.argmax(p, -1)
    hit = (picks[:, a] == labels[a]) & (picks[:, b] == labels[b])
    favorites = np.argmax(market[0], -1)
    fq = (np.take_along_axis(pa, np.broadcast_to(favorites[a][None, :, None], (p.shape[0], len(a), 1)), -1)[..., 0]
          * np.take_along_axis(pb, np.broadcast_to(favorites[b][None, :, None], (p.shape[0], len(b), 1)), -1)[..., 0])
    fy = ((favorites[a] == labels[a]) & (favorites[b] == labels[b])).astype(float)
    return {'brier': brier, 'log_loss': log, 'favorite_hit': hit.astype(float), 'common_ticket_brier': (fq-fy)**2}


def choose(model, market, labels, train_pairs, candidates):
    # Stable tie preference: favor identity calibration and less model weight.
    ranked = sorted(candidates, key=lambda c: (c[0], abs(c[1]-1), abs(c[2]-1)))
    best, best_loss = None, float('inf')
    for params in ranked:
        loss = scores(transform(model, market, params), labels, train_pairs, market)['brier'].mean()
        if loss < best_loss-1e-12:
            best, best_loss = params, float(loss)
    return best, best_loss


def run():
    task_path = REPO / 'artifacts/earlier-run-risk/tasks.jsonl.gz'
    saved_path = ROOT / 'training-results/run-3-results/forecast-rules-run-3/repeated-validation/matches.jsonl'
    tasks = [t for t in read_tasks(task_path) if t['decision'] == 'h1+15' and t['position'] == 'flat']
    assert len(tasks) == 72 and all(t['split'] == 'val' and not t['demo'] for t in tasks)
    tasks.sort(key=lambda t: t['match_id'])
    ids = [t['match_id'] for t in tasks]
    lookup = {mid: i for i, mid in enumerate(ids)}
    ledger = ROOT / 'rin-gym-simulation/data/evals/ledger.sqlite3'
    before = guard(ledger, ids)
    metadata_path = snapshot(REPO / 'data/raw/release-data-2026-10-13-v1', 'fifa-markets') / 'matches.parquet'
    rows = pq.read_table(metadata_path, columns=['match_id', 'home_team_id', 'away_team_id'], filters=[('match_id', 'in', ids)]).to_pylist()
    by_id = {r['match_id']: {r['home_team_id'], r['away_team_id']} for r in rows}
    teams = [by_id[mid] for mid in ids]
    labels = np.array([SIDES.index(t['scoring']['result']) for t in tasks])
    market = np.array([[t['scoring']['market_probs'][s] for s in SIDES] for t in tasks])
    market = np.broadcast_to(market[None], (3, 72, 3)).copy()
    model = np.full((3, 72, 3), np.nan)
    for line in saved_path.read_text().splitlines():
        row = json.loads(line)
        assert row['split'] == 'val' and not row['demo']
        step, = [s for s in row['steps'] if s['decision'] == 'h1+15']
        assert step['valid'] and step['task_id'].endswith(':flat')
        model[row['repeat'], lookup[row['match_id']]] = [step['probs'][s] for s in SIDES]
    assert np.isfinite(model).all() and np.allclose(model.sum(-1), 1)
    designs = []
    for seed in SEEDS:
        folds = [list(map(int, a)) for a in np.array_split(np.random.default_rng(seed).permutation(72), 6)]
        for fold, test in enumerate(folds):
            train = sorted(set(range(72))-set(test))
            assert not set(train) & set(test)
            train_pairs, test_pairs = indices(train, teams), indices(test, teams)
            assert len(train_pairs) and len(test_pairs)
            designs.append((seed, fold, train, test, train_pairs, test_pairs))
    protocol = {'purpose': 'Improve two-leg joint probability quality over raw and calibrated market baselines using existing saved forecasts.',
        'scope': 'Retrospective development cross-validation, NOT untouched holdout evaluation.',
        'phase': 'h1+15', 'primary_partition_seed': SEEDS[0], 'partition_sensitivity_seeds': SEEDS[1:],
        'folds': 6, 'matches': 72, 'repeats': 3,
        'families': FAMILIES, 'primary_recipe': 'calibrated_blend', 'primary_control': 'calibrated_market',
        'primary_metric': 'mean two-leg nine-cell Brier, repeat-averaged within fixture pair',
        'selection': 'Minimize repeat-averaged Brier across training-only pairs; deterministic identity/low-weight tie preference.',
        'split_unit': 'Whole match; both test-ticket legs must be in the same test fold; no shared-team pairs.',
        'repeat_use': 'Never split repeats of one game across fit/evaluation; match repeat index across legs.',
        'calibration': 'Temperature and draw multiplier after convex model/market blend; no new LLM inference.',
        'prices': 'None: this scores probabilities, not realized quote-based P&L.',
        'uncertainty': 'Fold deltas and sensitivity partitions; descriptive bootstrap of test-only disjoint pairs; overlapping fit folds and shared teams remain limitations.',
        'fold_membership': [{'seed': seed, 'fold': fold, 'fit_matches': [ids[i] for i in train],
            'evaluation_matches': [ids[i] for i in test]} for seed, fold, train, test, _, _ in designs],
        'sources_sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [task_path, saved_path, metadata_path, Path(__file__)]}, 'holdout_before': before}
    (OUT / 'protocol.json').write_text(json.dumps(protocol, indent=2)+'\n')
    receipts, results, cases = [], [], []
    for seed, fold, train, test, train_pairs, test_pairs in designs:
        details = {}
        # Only fit-fold labels reach parameter selection.
        train_labels = labels.copy()
        train_labels[test] = -999
        for name, candidates in FAMILIES.items():
            params, fit_loss = choose(model, market, train_labels, train_pairs, candidates)
            details[name] = scores(transform(model, market, params), labels, test_pairs, market)
            receipts.append({'seed': seed, 'fold': fold, 'recipe': name, 'model_weight': params[0],
                'temperature': params[1], 'draw_multiplier': params[2], 'fit_brier': fit_loss,
                'evaluation_brier': float(details[name]['brier'].mean()),
                'training_pairs': len(train_pairs), 'evaluation_pairs': len(test_pairs)})
        for i, (a, b) in enumerate(test_pairs):
            cases.append({'seed': seed, 'fold': fold, 'matches': [ids[a], ids[b]],
                'scores': {name: {k: float(v[:, i].mean()) for k, v in values.items()} for name, values in details.items()}})
    for seed in SEEDS:
        seed_cases = [r for r in cases if r['seed'] == seed]
        for name in FAMILIES:
            metrics = {metric: float(np.mean([r['scores'][name][metric] for r in seed_cases]))
                       for metric in ['brier', 'log_loss', 'favorite_hit', 'common_ticket_brier']}
            metrics.update(seed=seed, recipe=name, fixture_pairs=len(seed_cases))
            results.append(metrics)
    # Descriptive, matched evaluation subset with no repeated match membership.
    used, disjoint = set(), []
    for row in sorted([r for r in cases if r['seed'] == SEEDS[0]], key=lambda r: (r['fold'], r['matches'])):
        if not used & set(row['matches']):
            disjoint.append(row); used.update(row['matches'])
    uncertainty = {}
    for control in ['market', 'calibrated_market']:
        delta = np.array([r['scores']['calibrated_blend']['brier']-r['scores'][control]['brier'] for r in disjoint])
        draw = np.random.default_rng(SEEDS[0]).integers(0, len(delta), (4000, len(delta)))
        uncertainty[control] = {'pairs': len(delta), 'mean_brier_delta': float(delta.mean()),
            'descriptive_pair_bootstrap_95': np.quantile(delta[draw].mean(1), [.025, .975]).tolist()}
    after = guard(ledger, ids)
    assert after == before
    summary = {'cost_usd': 0, 'holdout_before_after': after, 'results': results,
        'parameter_receipts': receipts, 'disjoint_pair_uncertainty': uncertainty,
        'chosen_model_weights_primary': dict(Counter(str(r['model_weight']) for r in receipts if r['seed'] == SEEDS[0] and r['recipe'] == 'calibrated_blend')),
        'note': 'Every recipe is selected without its evaluation-fold outcomes. These historical games were previously inspected, and the original LLM development reused them.'}
    (OUT / 'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    (OUT / 'cases.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in cases))
    print(json.dumps({'results': results, 'chosen_model_weights_primary': summary['chosen_model_weights_primary'],
        'disjoint_pair_uncertainty': uncertainty}, indent=2))


if __name__ == '__main__':
    run()
