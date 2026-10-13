"""Offline registered calibration analysis; no inference or protected outcomes."""
import collections
import gzip
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from experiments.forecast_only.core import guard

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'artifacts/eight-directions/d3_calibration'
LABELS = ['home', 'draw', 'away']
EDGES = [0, .2, .4, .6, .8, 1.000000001]
SEED = 20261013


def read_jsonl(path):
    opener = gzip.open if str(path).endswith('.gz') else open
    with opener(path, 'rt') as stream:
        return [json.loads(line) for line in stream]


def extract(record, tolerance):
    if record.get('truncated'):
        return None, 'truncated', None
    def unique(pairs):
        d = {}
        for k, v in pairs:
            if k in d:
                raise ValueError('duplicate_key')
            d[k] = v
        return d
    try:
        obj = json.loads(record['completion'], object_pairs_hook=unique)
        p = obj['probs']
        if not isinstance(p, dict) or set(p) != set(LABELS):
            return None, 'keys', None
        values = [p[o] for o in LABELS]
        if any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in values):
            return None, 'range', None
        total = math.fsum(values)
        if total <= 0 or abs(total - 1) > tolerance:
            return None, 'sum', total
        return np.array(values) / total, None, total
    except (ValueError, TypeError, KeyError):
        return None, 'json_or_duplicate_or_schema', None


def temperature(p, t):
    if p is None:
        return None
    z = np.log(np.clip(p, 1e-6, 1)) / t
    e = np.exp(z - z.max())
    return e / e.sum()


def scores(row, p):
    if p is None:
        return {'brier': 2., 'log_loss': -math.log(1e-6), 'valid_rate': 0.}
    target = np.eye(3)[row['y']]
    return {'brier': float(np.square(p - target).sum()), 'log_loss': -math.log(max(1e-6, p[row['y']])), 'valid_rate': 1.}


def cluster(rows, key='p', valid_only=False):
    groups = collections.defaultdict(list)
    for row in rows:
        if not valid_only or row[key] is not None:
            groups[row['mid']].append(scores(row, row[key]))
    return {mid: {k: float(np.mean([r[k] for r in values])) for k in ['brier', 'log_loss', 'valid_rate']} for mid, values in groups.items()}


def metric_summary(rows, key='p'):
    all_scores = cluster(rows, key)
    valid_scores = cluster(rows, key, True)
    return {'matches': len(all_scores), 'decisions': len(rows), 'valid_decisions': sum(r[key] is not None for r in rows),
            'invalid_inclusive': {k: float(np.mean([r[k] for r in all_scores.values()])) for k in ['brier', 'log_loss', 'valid_rate']},
            'valid_only': {'matches': len(valid_scores), **{k: float(np.mean([r[k] for r in valid_scores.values()])) if valid_scores else None for k in ['brier', 'log_loss']}}}


def paired(rows, left, right):
    a, b = cluster(rows, left), cluster(rows, right)
    ids = sorted(set(a) & set(b))
    rng = np.random.default_rng(SEED)
    idx = rng.integers(0, len(ids), (4000, len(ids)))
    out = {'matches': len(ids), 'orientation': 'left minus right; negative loss is better'}
    for metric in ['brier', 'log_loss', 'valid_rate']:
        d = np.array([a[m][metric] - b[m][metric] for m in ids])
        out[metric] = {'mean': float(d.mean()), 'match_bootstrap_95': np.quantile(d[idx].mean(axis=1), [.025, .975]).tolist()}
    return out


def fit(rows):
    valid = [r for r in rows if r['p'] is not None]
    def objective(t):
        temp_rows = [{**r, 'q': temperature(r['p'], t)} for r in valid]
        m = cluster(temp_rows, 'q')
        return float(np.mean([r['log_loss'] for r in m.values()]))
    lo, hi = .25, 8.
    ratio = (math.sqrt(5) - 1) / 2
    for _ in range(80):
        a, b = hi - ratio * (hi - lo), lo + ratio * (hi - lo)
        if objective(a) < objective(b):
            hi = b
        else:
            lo = a
    candidates = [.25, (lo + hi) / 2, 8.]
    t = min(candidates, key=objective)
    return {'temperature': t, 'fit_objective_log_loss': objective(t), 'fit_valid_matches': len({r['mid'] for r in valid}), 'fit_valid_decisions': len(valid), 'at_search_boundary': t in [.25, 8.]}


def reliability(rows, key):
    valid = [r for r in rows if r[key] is not None]
    counts = collections.Counter(r['mid'] for r in valid)
    bins = []
    for side in range(3):
        for lo, hi in zip(EDGES[:-1], EDGES[1:]):
            members = [r for r in valid if lo <= r[key][side] < hi]
            weights = np.array([1 / counts[r['mid']] for r in members])
            bins.append({'outcome': LABELS[side], 'bin': [lo, min(hi, 1)], 'matches': len({r['mid'] for r in members}), 'decisions': len(members),
                         'mean_probability': float(np.average([r[key][side] for r in members], weights=weights)) if members else None,
                         'observed_frequency': float(np.average([r['y'] == side for r in members], weights=weights)) if members else None})
    top_bins, ece, acc = [], 0., 0.
    total_weight = len(counts)
    for lo, hi in zip(EDGES[:-1], EDGES[1:]):
        members = [r for r in valid if lo <= max(r[key]) < hi]
        w = np.array([1 / counts[r['mid']] for r in members])
        conf = float(np.average([max(r[key]) for r in members], weights=w)) if members else None
        correct = float(np.average([int(np.argmax(r[key])) == r['y'] for r in members], weights=w)) if members else None
        top_bins.append({'bin': [lo, min(hi, 1)], 'matches': len({r['mid'] for r in members}), 'decisions': len(members), 'confidence': conf, 'accuracy': correct})
        if members:
            ece += w.sum() / total_weight * abs(conf - correct)
            acc += w.sum() / total_weight * correct
    # Class-wise AUROC is a secondary discrimination diagnostic, with equal match weight.
    auc = {}
    for side in range(3):
        pos, neg = [r for r in valid if r['y'] == side], [r for r in valid if r['y'] != side]
        numer, denom = 0., 0.
        for p in pos:
            for n in neg:
                weight = 1 / counts[p['mid']] / counts[n['mid']]
                numer += weight * ((p[key][side] > n[key][side]) + .5 * (p[key][side] == n[key][side]))
                denom += weight
        auc[LABELS[side]] = numer / denom if denom else None
    return {'valid_matches': len(counts), 'top_class_ece': ece, 'top_class_accuracy': acc, 'class_auroc': auc, 'class_bins': bins, 'top_class_bins': top_bins, 'note': 'Valid-only, each match has equal aggregate weight; descriptive fixed-bin diagnostic, no ECE optimization.'}


def slices(rows, key='p'):
    groups = {}
    for outcome in LABELS:
        groups['result/' + outcome] = [r for r in rows if LABELS[r['y']] == outcome]
    for phase in ['h1+15', 'h1+30', 'ht', 'h2+15', 'h2+30']:
        groups['phase/' + phase] = [r for r in rows if r['phase'] == phase]
    for lo, hi in zip(EDGES[:-1], EDGES[1:]):
        label = f'{lo:.1f}-{min(hi,1):.1f}'
        groups['top_probability/' + label] = [r for r in rows if r[key] is not None and lo <= max(r[key]) < hi]
        groups['displayed_price_of_forecast_argmax/' + label] = [r for r in rows if r[key] is not None and lo <= r['shown'][int(np.argmax(r[key]))] < hi]
    return {name: metric_summary(members, key) for name, members in groups.items() if members}


def main():
    protocol = json.loads((OUT / 'protocol.json').read_text())
    setup = json.loads((ROOT / 'research/eight-directions/setup.json').read_text())
    before = guard(setup['guard_ledger'], protocol['cohort_ids'])
    assert before == protocol['guard_before']
    tasks = {t['id']: t for t in read_jsonl(ROOT / 'inputs/earlier-run-risk/tasks.jsonl.gz')}
    pilot_tasks = {t['id']: t for t in read_jsonl(ROOT / 'inputs/forecast-only/tasks.jsonl.gz')}
    raw = [('closed_loop', r, tasks) for r in read_jsonl(ROOT / 'inputs/closed-loop-risk/predictions.jsonl')]
    raw += [('pilot', r, pilot_tasks) for r in read_jsonl(ROOT / 'inputs/forecast-only/predictions.jsonl')]
    ids = sorted({r['task_id'].split(':')[0] for _, r, _ in raw})
    assert ids == protocol['cohort_ids'] and set(ids) <= set(setup['allowed_match_ids'])
    results = {'scope': protocol['design'], 'arms': {}, 'input_checksums': {}, 'validity_definition': protocol['validity'], 'protocol_sha256': hashlib.sha256((OUT / 'protocol.json').read_bytes()).hexdigest()}
    receipts = []
    for source, r, task_map in raw:
        assert r['task_id'] in task_map
        t = task_map[r['task_id']]
        if source == 'pilot':
            assert t['match_id'] in protocol['pilot_cohort_ids']
        label = r.get('arm', r.get('model'))
        arm = source + '/' + ('final' if str(label).startswith('tinker://') else label)
        for mode, tolerance in [('strict', 1e-6), ('tolerance_0.05', .05)]:
            p, error, total = extract(r, tolerance)
            row = {'arm': arm, 'mode': mode, 'mid': t['match_id'], 'phase': t['decision'], 'repeat': r.get('repeat', 0), 'task_id': r['task_id'], 'p': p, 'market': np.array([t['scoring']['market_probs'][o] for o in LABELS]), 'shown': np.array([t['scoring']['shown_prices'][o] for o in LABELS]), 'y': LABELS.index(t['scoring']['result']), 'error': error, 'sum_before_normalization': total, 'recorded_native_valid': r.get('valid', r.get('probs') is not None)}
            receipts.append(row)
    for arm in sorted({r['arm'] for r in receipts}):
        result = {}
        for mode in ['strict', 'tolerance_0.05']:
            rows = [r for r in receipts if r['arm'] == arm and r['mode'] == mode]
            if arm.startswith('closed_loop/'):
                fit_ids = set(protocol['chronological_partition']['fit_match_ids'])
                fit_rows = [r for r in rows if r['mid'] in fit_ids]
                assess = [r for r in rows if r['mid'] not in fit_ids]
                fitted = fit(fit_rows)
                for r in rows:
                    r['calibrated'] = temperature(r['p'], fitted['temperature'])
                fitting = {'raw': metric_summary(fit_rows), 'calibrated': metric_summary(fit_rows, 'calibrated')}
            else:
                assess, fitted, fitting = rows, None, None
            value = {'fit': fitted, 'fit_metrics': fitting, 'assessment_match_ids': sorted({r['mid'] for r in assess}), 'raw': metric_summary(assess), 'market': metric_summary(assess, 'market'), 'raw_minus_market': paired(assess, 'p', 'market'), 'raw_valid_only_minus_market_same_rows': paired([r for r in assess if r['p'] is not None], 'p', 'market'), 'raw_calibration_and_discrimination': reliability(assess, 'p'), 'raw_slices': slices(assess), 'validity_all_rows': {'matches': len({r['mid'] for r in rows}), 'decisions': len(rows), 'probability_vector_valid': sum(r['p'] is not None for r in rows), 'native_recorded_valid': sum(r['recorded_native_valid'] for r in rows), 'invalid_reasons': dict(collections.Counter(r['error'] for r in rows if r['error']))}}
            if fitted:
                value.update({'calibrated': metric_summary(assess, 'calibrated'), 'calibrated_minus_raw': paired(assess, 'calibrated', 'p'), 'calibrated_minus_market': paired(assess, 'calibrated', 'market'), 'calibrated_valid_only_minus_market_same_rows': paired([r for r in assess if r['p'] is not None], 'calibrated', 'market'), 'calibrated_calibration_and_discrimination': reliability(assess, 'calibrated'), 'calibrated_slices': slices(assess, 'calibrated')})
                assert all(r['p'] is None or int(np.argmax(r['p'])) == int(np.argmax(r['calibrated'])) for r in rows)
            result[mode] = value
        results['arms'][arm] = result
    # Paired pilot model comparison stays inside that prompt/model family and reused cohort.
    pilot_a = {(r['mid'], r['phase']): r for r in receipts if r['arm'] == 'pilot/base' and r['mode'] == 'strict'}
    pilot_b = {(r['mid'], r['phase']): r for r in receipts if r['arm'] == 'pilot/final' and r['mode'] == 'strict'}
    assert pilot_a.keys() == pilot_b.keys()
    joined = [{**pilot_a[k], 'final': pilot_b[k]['p']} for k in pilot_a]
    results['pilot_strict_final_minus_base'] = paired(joined, 'final', 'p')
    common = [r for r in joined if r['p'] is not None and r['final'] is not None]
    results['pilot_strict_common_valid_final_minus_base'] = paired(common, 'final', 'p')
    results['pilot_strict_common_valid_counts'] = {'matches': len({r['mid'] for r in common}), 'decisions': len(common)}
    tol_a = {(r['mid'], r['phase']): r for r in receipts if r['arm'] == 'pilot/base' and r['mode'] == 'tolerance_0.05'}
    tol_b = {(r['mid'], r['phase']): r for r in receipts if r['arm'] == 'pilot/final' and r['mode'] == 'tolerance_0.05'}
    assert tol_a.keys() == tol_b.keys()
    tol_joined = [{**tol_a[k], 'final': tol_b[k]['p']} for k in tol_a]
    results['pilot_tolerance_final_minus_base'] = paired(tol_joined, 'final', 'p')
    results['closed_loop_trajectory_coverage'] = {arm: {'repeat_counts': dict(collections.Counter(r['repeat'] for r in receipts if r['arm'] == arm and r['mode'] == 'strict')), 'phase_counts': dict(collections.Counter(r['phase'] for r in receipts if r['arm'] == arm and r['mode'] == 'strict'))} for arm in ['closed_loop/original', 'closed_loop/conservative']}
    for file in protocol['inputs']:
        results['input_checksums'][file] = hashlib.sha256((ROOT / file).read_bytes()).hexdigest()
    after = guard(setup['guard_ledger'], ids)
    assert after == before
    results['classification'] = {'calibration': 'promising_retrospective', 'market_comparison': 'negative', 'forecast_only_pilot_probability_improvement': 'inconclusive'}
    results['limitations'] = ['Repeatedly inspected development matches; no protected assessment', 'Match bootstrap ignores shared-team dependence', 'Small reused 16-match pilot', 'Native recorded validity and uniform probability audit differ', 'Scalar calibration does not establish profitability']
    results['guard_before'] = before
    results['guard_after'] = after
    (OUT / 'guard_after.json').write_text(json.dumps(after, indent=2) + '\n')
    (OUT / 'results.json').write_text(json.dumps(results, indent=2, allow_nan=False) + '\n')
    with (OUT / 'analysis_receipts.jsonl').open('w') as stream:
        for row in receipts:
            stream.write(json.dumps({k: v.tolist() if isinstance(v, np.ndarray) else v for k, v in row.items()}, allow_nan=False) + '\n')
    for arm, modes in results['arms'].items():
        for mode, r in modes.items():
            print(arm, mode, 'fit', r['fit'], 'raw', r['raw']['invalid_inclusive'], 'market', r['market']['invalid_inclusive'], 'calibrated', r.get('calibrated', {}).get('invalid_inclusive'), 'delta', r.get('calibrated_minus_raw'))
    print('pilot paired', results['pilot_strict_final_minus_base'], 'common valid', results['pilot_strict_common_valid_final_minus_base'])


if __name__ == '__main__':
    main()
