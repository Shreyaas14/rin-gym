"""Meaningful provenance, pairing and analytic-fit checks; no paid calls."""
import json, math
import numpy as np
from experiments.eight_directions.d2_market_prior.study import ROOT,OUT,SIDES,SETUP
from experiments.forecast_only.core import digest,guard
from app.trade_env.tasks import read_tasks

s=json.loads(SETUP.read_text());p=json.loads((OUT/'protocol.json').read_text());r=json.loads((OUT/'results.json').read_text())
tm={t['id']:t for t in read_tasks(ROOT/'inputs/earlier-run-risk/tasks.jsonl.gz')}
rows=[json.loads(l) for l in (ROOT/'inputs/closed-loop-risk/predictions.jsonl').read_text().splitlines() if json.loads(l)['arm']=='original']
for row in rows:
    t=tm[row['task_id']]
    assert row['observation_sha256']==digest(t['messages'])
    assert row['position_before']==t['position']
    if row['valid']:
        assert set(row['probs'])==set(SIDES)
        assert all(math.isfinite(v) and 0<=v<=1 for v in row['probs'].values())
        assert abs(sum(row['probs'].values())-1)<1e-9
keys={(tm[row['task_id']]['match_id'],row['repeat'],tm[row['task_id']]['t']) for row in rows}
assert len(keys)==len(rows)==1077
for mid in p['cohort']['fit_match_ids']+p['cohort']['assessment_match_ids']:
    times={tm[row['task_id']]['t'] for row in rows if tm[row['task_id']]['match_id']==mid}
    assert all((mid,rep,t) in keys for t in times for rep in range(3))
fit=p['cohort']['fit_match_ids'];assessment=p['cohort']['assessment_match_ids']
assert len(fit)==39 and len(assessment)==33 and not set(fit)&set(assessment)
assert max(p['cohort']['first_observed_t_by_match'][i] for i in fit)<min(p['cohort']['first_observed_t_by_match'][i] for i in assessment)
f=r['fit_weight_objective'];a=r['alpha_model_weight'];assert abs(a-np.clip(f['numerator']/f['denominator'],0,1))<1e-12
assert r['partitions']['fit']['forecast']['fitted_blend']['brier']<=min(r['partitions']['fit']['forecast']['market_only']['brier'],r['partitions']['fit']['forecast']['original_model_with_market_visible']['brier'])+1e-12
assert r['guard_after']==r['guard_before']==guard(s['guard_ledger'],fit+assessment)
checks={'source_original_prompt_hashes':1077,'all_observed_timestamps_matched':True,'three_repeat_complete_cases':216,'chronological_partition':{'fit':39,'assessment':33},'analytical_weight_and_endpoint_loss_check':True,'holdout_guard_unchanged':True}
(OUT/'verification.json').write_text(json.dumps(checks,indent=2)+'\n')
print(json.dumps(checks,indent=2))
