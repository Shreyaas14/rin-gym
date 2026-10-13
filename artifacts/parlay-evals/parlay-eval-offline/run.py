"""Offline product forecasts and declared synthetic payouts; no inference APIs."""
import gzip
import hashlib
import itertools
import json
from pathlib import Path
import random
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
SEED = 20261013
CLIP = 1e-6
HAIRCUT = .95
MAX_RETURN = 20.
MIN_EV = .05


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def vector(p):
    if p is None:
        return np.full(3, np.nan)
    a = np.array([p[s] for s in SIDES], dtype=float)
    if not np.isfinite(a).all() or (a < 0).any() or (a > 1).any() or abs(a.sum()-1) > 1e-6:
        raise ValueError('Invalid cached normalized probability vector')
    return a / a.sum()


def joint(p):
    """Rows are tickets; shape (tickets, legs, three outcomes)."""
    out = np.ones((len(p), 1))
    for leg in range(p.shape[1]):
        out = (out[:, :, None] * p[:, leg, None, :]).reshape(len(p), -1)
    return out


def select(probabilities, market, haircut=HAIRCUT):
    """Decision depends only on forecast and declared price schedule, never labels."""
    returns = np.minimum(MAX_RETURN, haircut / np.maximum(market, CLIP))
    ev = probabilities * returns - 1
    chosen = np.argmax(ev, axis=1)
    valid = np.isfinite(probabilities).all(axis=1)
    entered = valid & (ev[np.arange(len(ev)), chosen] > MIN_EV)
    return chosen, entered, returns


def evaluate(p, m, labels):
    pj, mj = joint(p), joint(m)
    n, k = p.shape[:2]
    actual = sum(labels[:, i] * 3**(k-i-1) for i in range(k))
    ix = np.arange(n)
    valid = np.isfinite(pj).all(axis=1)
    loss = np.where(valid, np.sum(pj**2, axis=1)-2*pj[ix, actual]+1, 2.)
    log = np.where(valid, -np.log(np.maximum(pj[ix, actual], CLIP)), -np.log(CLIP))
    favorite = np.argmax(np.nan_to_num(pj, nan=-1), axis=1)
    market_favorite = np.argmax(mj, axis=1)
    q = pj[ix, market_favorite]
    y = (actual == market_favorite).astype(float)
    common_brier = np.where(valid, (q-y)**2, 1.)
    picked, entered, returns = select(pj, mj)
    profit = np.where(entered, returns[ix, picked]*(picked == actual)-1, 0.)
    sensitivity = {}
    for haircut in [1., .95, .90]:
        side, yes, ds = select(pj, mj, haircut)
        pnl = np.where(yes, ds[ix, side]*(side == actual)-1, 0.)
        sensitivity[str(haircut)] = {'selected_tickets': int(yes.sum()),
            'return_per_dollar_staked': float(pnl.sum()/yes.sum()) if yes.any() else None}
    bins = []
    for low, high in zip([0, .2, .4, .6, .8], [.2, .4, .6, .8, 1.00000001]):
        mask = valid & (q >= low) & (q < high)
        bins.append({'range': [low, min(high, 1)], 'count': int(mask.sum()),
            'predicted': float(q[mask].mean()) if mask.any() else None,
            'observed': float(y[mask].mean()) if mask.any() else None})
    metrics = {'cases': n, 'valid_fraction': float(valid.mean()),
        'joint_brier_including_invalid': float(loss.mean()),
        'joint_log_loss_including_invalid': float(log.mean()),
        'joint_brier_valid_only': float(loss[valid].mean()) if valid.any() else None,
        'most_likely_ticket_hit_rate_valid_only': float((favorite[valid] == actual[valid]).mean()) if valid.any() else None,
        'most_likely_ticket_probability_valid_only': float(pj[ix[valid], favorite[valid]].mean()) if valid.any() else None,
        'common_market_favorite_binary_brier_including_invalid': float(common_brier.mean()),
        'common_market_favorite_calibration_valid_only': bins,
        'hypothetical_payout_sensitivity': sensitivity}
    rows = {'valid': valid, 'brier': loss, 'log_loss': log,
        'common_ticket_brier': common_brier, 'selected': entered, 'synthetic_profit_per_ticket': profit}
    return metrics, rows


def main():
    ledger = ROOT / 'rin-gym-simulation/data/evals/ledger.sqlite3'
    earlier_tasks = REPO / 'artifacts/earlier-run-risk/tasks.jsonl.gz'
    earlier_saved = ROOT / 'training-results/run-3-results/forecast-rules-run-3/repeated-validation/matches.jsonl'
    pilot_tasks = REPO / 'artifacts/forecast-only/tasks.jsonl.gz'
    pilot_saved = REPO / 'artifacts/forecast-only/predictions.jsonl'
    tasks = read_tasks(earlier_tasks)
    assert all(t['split'] == 'val' and not t['demo'] for t in tasks)
    at = {(t['match_id'], t['decision']): t for t in tasks if t['position'] == 'flat'}
    ids = sorted({t['match_id'] for t in tasks})
    before = guard(ledger, ids)
    metadata = pq.read_table(snapshot(REPO / 'data/raw/release-data-2026-10-13-v1', 'fifa-markets') / 'matches.parquet',
        columns=['match_id', 'home_team_id', 'away_team_id'], filters=[('match_id', 'in', ids)]).to_pylist()
    teams = {r['match_id']: {r['home_team_id'], r['away_team_id']} for r in metadata}
    assert len(teams) == len(ids) == 72
    saved = [json.loads(x) for x in earlier_saved.read_text().splitlines()]
    old = {}
    for r in saved:
        assert r['split'] == 'val' and not r['demo']
        for s in r['steps']:
            if s['decision'] == 'h1+15':
                assert s['task_id'].endswith(':flat')
            key = (r['match_id'], s['decision'], r['repeat'])
            assert key not in old
            old[key] = vector(s['probs'] if s['valid'] else None)
    pt = {t['id']: t for t in read_tasks(pilot_tasks)}
    pred = [json.loads(x) for x in pilot_saved.read_text().splitlines()]
    final_name, = {r['model'] for r in pred if r['model'] != 'base'}
    pilot = {}
    for r in pred:
        t = pt[r['task_id']]
        assert t['split'] == 'val' and not t['demo'] and t['position'] == 'flat'
        assert t['match_id'] in ids
        reference = at[(t['match_id'], t['decision'])]
        assert reference['t'] == t['t']
        for field in ['result', 'market_probs', 'shown_prices']:
            assert reference['scoring'][field] == t['scoring'][field]
        key = (t['match_id'], t['decision'], 'base' if r['model'] == 'base' else 'final')
        assert key not in pilot
        pilot[key] = vector(r['probs'])
    pids = sorted({key[0] for key in pilot})

    # Define all membership and selection rules before scoring outcomes.
    designs = []
    for cohort, mids, phases, reps, models in [
        ('run3', ids, ['h1+15'], range(3), ['model', 'market']),
        ('pilot', pids, ['h1+15'], range(1), ['base', 'final', 'market']),
        ('pilot_phase_diagnostic', pids, ['h1+30', 'ht', 'h2+15', 'h2+30'], range(1), ['base', 'final', 'market'])]:
        for k in [1, 2, 3] if cohort != 'pilot_phase_diagnostic' else [2]:
            combos = [c for c in itertools.combinations(mids, k)
                      if all(not teams[a] & teams[b] for a, b in itertools.combinations(c, 2))]
            assert combos
            designs.append((cohort, phases, list(reps), models, k, combos))
    shuffled = ids[:]
    random.Random(SEED).shuffle(shuffled)
    disjoint = []
    while len(shuffled) >= 2:
        a = shuffled.pop(0)
        b = next((b for b in shuffled if not teams[a] & teams[b]), None)
        if b is not None:
            shuffled.remove(b)
            disjoint.append(tuple(sorted([a, b])))
    assert len({x for c in disjoint for x in c}) == 2*len(disjoint)
    protocol = {'scope': 'Retrospective development product-forecast experiment; no direct joint model calls.',
        'seed': SEED, 'primary_phase': 'h1+15', 'leg_counts': [1, 2, 3],
        'cohort_sizes': {'run3': len(ids), 'pilot': len(pids)},
        'exclude_shared_team': True, 'independence_assumption': True,
        'log_clip': CLIP, 'invalid_joint_brier': 2, 'invalid_binary_brier': 1,
        'invalid_log_loss': float(-np.log(CLIP)), 'invalid_execution': 'abstain',
        'synthetic_pricing': {'decimal_return': 'min(20, haircut / normalized_market_product_probability)',
            'haircuts': [1, .95, .90], 'primary_haircut': HAIRCUT,
            'selector': 'maximize q*D-1 across outcome tickets; enter only if >0.05; one ticket per fixture combination',
            'stake': 'Equal ticket weighting; ROI is per dollar staked, not total executable portfolio P&L.'},
        'repeats': 'Use same repeat index for all legs; average repeats within combination before comparing or bootstrapping.',
        'uncertainty': '4000 bootstrap draws over seeded disjoint run3 pairs, h1+15 only; overlapping all-combination metrics descriptive.',
        'disjoint_pairs': disjoint, 'source_hashes': {str(p.relative_to(ROOT)): fingerprint(p)
            for p in [earlier_tasks, earlier_saved, pilot_tasks, pilot_saved, Path(__file__)]},
        'holdout_before': before}
    (OUT / 'protocol.json').write_text(json.dumps(protocol, indent=2)+'\n')
    results = []
    disjoint_rows = {}
    with gzip.open(OUT / 'cases.jsonl.gz', 'wt') as handle:
        for cohort, phases, reps, models, k, combos in designs:
            rows = [(c, phase, rep) for c in combos for phase in phases for rep in reps]
            m = np.array([[vector(at[(mid, phase)]['scoring']['market_probs']) for mid in c] for c, phase, rep in rows])
            labels = np.array([[SIDES.index(at[(mid, phase)]['scoring']['result']) for mid in c] for c, phase, rep in rows])
            model_data = {}
            for model in models:
                if model == 'market':
                    p = m
                elif cohort == 'run3':
                    p = np.array([[old[(mid, phase, rep)] for mid in c] for c, phase, rep in rows])
                else:
                    p = np.array([[pilot[(mid, phase, model)] for mid in c] for c, phase, rep in rows])
                metrics, detail = evaluate(p, m, labels)
                model_data[model] = detail
                metrics.update(cohort=cohort, model=model, legs=k, unique_fixture_combinations=len(combos), phases=phases)
                results.append(metrics)
                if cohort == 'run3' and k == 2:
                    disjoint_rows[model] = np.array([detail['brier'][i*3:i*3+3].mean()
                        for i, c in enumerate(combos) if c in disjoint])
            common = np.logical_and.reduce([model_data[model]['valid'] for model in models])
            for entry in results[-len(models):]:
                entry['joint_brier_on_all_models_common_valid'] = float(model_data[entry['model']]['brier'][common].mean()) if common.any() else None
                entry['all_models_common_valid_fraction'] = float(common.mean())
            for i, (c, phase, rep) in enumerate(rows):
                handle.write(json.dumps({'cohort': cohort, 'legs': k, 'matches': c, 'phase': phase, 'repeat': rep,
                    'models': {model: {key: value[i].item() for key, value in detail.items()}
                               for model, detail in model_data.items()}}, allow_nan=False)+'\n')
    delta = disjoint_rows['model']-disjoint_rows['market']
    rng = np.random.default_rng(SEED)
    draws = rng.integers(0, len(delta), (4000, len(delta)))
    after = guard(ledger, ids)
    assert before == after
    summary = {'cost_usd': 0, 'holdout_before_after': after, 'results': results,
        'disjoint_pairs_uncertainty': {'pairs': len(delta), 'mean_model_minus_market_brier': float(delta.mean()),
            'bootstrap_95': np.quantile(delta[draws].mean(axis=1), [.025, .975]).tolist(),
            'limitation': 'One seeded disjoint development pairing; shared teams across pairs may remain dependent.'}}
    (OUT / 'summary.json').write_text(json.dumps(summary, indent=2, allow_nan=False)+'\n')
    print(json.dumps({'cost_usd': 0, 'primary_results': [r for r in results if r['cohort'] != 'pilot_phase_diagnostic'],
        'disjoint_pairs_uncertainty': summary['disjoint_pairs_uncertainty']}, indent=2))


if __name__ == '__main__':
    main()
