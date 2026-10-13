"""Independent scalar checks against saved source responses and labels."""
from collections import Counter, defaultdict
import gzip
import hashlib
import itertools
import json
import math
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'rin-gym-forecast-only'))
from app.trade_env.tasks import read_tasks
from experiments.forecast_only.core import guard

SIDES = ('home', 'draw', 'away')
protocol = json.loads((OUT / 'protocol.json').read_text())
summary = json.loads((OUT / 'summary.json').read_text())
for path, checksum in protocol['source_hashes'].items():
    assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == checksum
tasks = {(t['match_id'], t['decision']): t for t in read_tasks(ROOT / 'rin-gym-forecast-only/artifacts/earlier-run-risk/tasks.jsonl.gz') if t['position'] == 'flat'}
old = {}
for line in (ROOT / 'training-results/run-3-results/forecast-rules-run-3/repeated-validation/matches.jsonl').read_text().splitlines():
    r = json.loads(line)
    for s in r['steps']:
        old[r['match_id'], s['decision'], r['repeat']] = s['probs'] if s['valid'] else None
pt = {t['id']: t for t in read_tasks(ROOT / 'rin-gym-forecast-only/artifacts/forecast-only/tasks.jsonl.gz')}
pilot = {}
for line in (ROOT / 'rin-gym-forecast-only/artifacts/forecast-only/predictions.jsonl').read_text().splitlines():
    r = json.loads(line); t = pt[r['task_id']]
    pilot[t['match_id'], t['decision'], 'base' if r['model'] == 'base' else 'final'] = r['probs']

counts = Counter()
totals = defaultdict(lambda: defaultdict(float))
sample_counts = Counter()
invalid_counts = Counter()
selected_counts = Counter()
rng = random.Random(20261013)
checked = 0
with gzip.open(OUT / 'cases.jsonl.gz', 'rt') as handle:
    for line in handle:
        r = json.loads(line)
        for model, detail in r['models'].items():
            key = (r['cohort'], r['legs'], model)
            counts[key] += 1
            for metric in ['brier', 'log_loss']:
                totals[key][metric] += detail[metric]
            invalid_counts[key] += not detail['valid']
            selected_counts[key] += detail['selected']
            # Random source-based checks in every cohort/model/leg-count, plus every invalid case.
            if detail['valid'] and not (sample_counts[key] < 40 and rng.random() < .1):
                continue
            ps = [tasks[mid, r['phase']]['scoring']['market_probs'] if model == 'market'
                  else old[mid, r['phase'], r['repeat']] if r['cohort'] == 'run3'
                  else pilot[mid, r['phase'], model] for mid in r['matches']]
            if any(p is None for p in ps):
                assert not detail['valid'] and detail['brier'] == 2
                assert not detail['selected'] and detail['synthetic_profit_per_ticket'] == 0
            else:
                assert detail['valid']
                actual = tuple(tasks[mid, r['phase']]['scoring']['result'] for mid in r['matches'])
                dist = {sides: math.prod(p[side]/sum(p.values()) for p, side in zip(ps, sides))
                        for sides in itertools.product(SIDES, repeat=r['legs'])}
                assert abs(sum(dist.values())-1) < 1e-10
                brier = sum((p-float(sides == actual))**2 for sides, p in dist.items())
                log = -math.log(max(dist[actual], 1e-6))
                assert abs(brier-detail['brier']) < 1e-10
                assert abs(log-detail['log_loss']) < 1e-10
                # Recompute selector from only source forecasts and market probabilities.
                market = [tasks[mid, r['phase']]['scoring']['market_probs'] for mid in r['matches']]
                odds = {sides: min(20, .95/max(math.prod(p[s]/sum(p.values()) for p, s in zip(market, sides)), 1e-6))
                        for sides in dist}
                chosen = max(dist, key=lambda s: dist[s]*odds[s]-1)
                entered = dist[chosen]*odds[chosen]-1 > .05
                pnl = odds[chosen]*float(chosen == actual)-1 if entered else 0
                assert entered == detail['selected']
                assert abs(pnl-detail['synthetic_profit_per_ticket']) < 1e-10
            sample_counts[key] += 1; checked += 1
for r in summary['results']:
    key = (r['cohort'], r['legs'], r['model'])
    assert counts[key] == r['cases']
    assert abs(totals[key]['brier']/counts[key]-r['joint_brier_including_invalid']) < 1e-8
    assert abs(totals[key]['log_loss']/counts[key]-r['joint_log_loss_including_invalid']) < 1e-8
    assert selected_counts[key] == r['hypothetical_payout_sensitivity']['0.95']['selected_tickets']
    assert abs(1-invalid_counts[key]/counts[key]-r['valid_fraction']) < 1e-10
    assert sample_counts[key] >= 1
ids = {mid for mid, phase in tasks}
assert guard(ROOT / 'rin-gym-simulation/data/evals/ledger.sqlite3', ids) == summary['holdout_before_after'] == protocol['holdout_before']
result = {'status': 'passed', 'independent_source_based_checks': checked,
    'aggregate_metric_rows_checked': len(summary['results']), 'case_model_rows_checked': sum(counts.values()),
    'source_hashes_verified': len(protocol['source_hashes']), 'holdout_unchanged': True}
(OUT / 'verification.json').write_text(json.dumps(result, indent=2)+'\n')
print(json.dumps(result, indent=2))
