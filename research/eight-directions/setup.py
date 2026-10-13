from pathlib import Path
import hashlib,json,shutil,gzip
from experiments.forecast_only.core import guard
ROOT=Path('/Users/alexkan/Desktop/frontiers/rin-gym-research-eight')
SRC=Path('/Users/alexkan/Desktop/frontiers/rin-gym-forecast-only')
LEDGER='/Users/alexkan/Desktop/frontiers/rin-gym-simulation/data/evals/ledger.sqlite3'
# Read cohort declarations before loading outcome-bearing development artifacts.
cp=json.loads((SRC/'artifacts/closed-loop-risk/protocol.json').read_text())
fp=json.loads((SRC/'artifacts/forecast-only/protocol.json').read_text())
ids=sorted(set(map(str,cp['match_ids']))|set(map(str,fp['cohorts']['train']))|set(map(str,fp['cohorts']['val'])))
seal=guard(LEDGER,ids)
manifest={}
for name in ('forecast-only','earlier-run-risk','closed-loop-risk','risk-policy'):
    source=SRC/'artifacts'/name;target=ROOT/'inputs'/name
    shutil.copytree(source,target,dirs_exist_ok=True)
    for p in source.rglob('*'):
        if p.is_file():
            rel=str(p.relative_to(SRC));h=hashlib.sha256(p.read_bytes()).hexdigest()
            assert h==hashlib.sha256((target/p.relative_to(source)).read_bytes()).hexdigest()
            manifest[rel]=h
for name in ('closed_loop.py','earlier_run_risk.py','risk_policy.py','verify_closed_loop.py'):
    p=SRC/'experiments/forecast_only'/name
    shutil.copy2(p,ROOT/'experiments/forecast_only'/name)
    manifest['experiments/forecast_only/'+name]=hashlib.sha256(p.read_bytes()).hexdigest()
assert guard(LEDGER,ids)==seal
(ROOT/'inputs/MANIFEST.json').write_text(json.dumps(manifest,indent=2)+'\n')
setup={'base_commit':'9b620a9','source_checkout':str(SRC),'guard_ledger':LEDGER,'allowed_match_ids':ids,'seal_before':seal,'excluded_active_run':'artifacts/quarter-original-live; PID 98239 at initial inspection','scope':'Retrospective development only; historical val labels do not imply untouched evaluation','budget_total_usd':40,'budget_per_direction_usd':5,'source_manifest_sha256':hashlib.sha256((ROOT/'inputs/MANIFEST.json').read_bytes()).hexdigest()}
(ROOT/'research/eight-directions/setup.json').write_text(json.dumps(setup,indent=2)+'\n')
names=['consistency','market_prior','calibration','position_bias','timing_memory','training_scale','execution_realism','specialization']
for i,name in enumerate(names,1):
    exp=ROOT/f'experiments/eight_directions/d{i}_{name}';art=ROOT/f'artifacts/eight-directions/d{i}_{name}'
    exp.mkdir(parents=True,exist_ok=True);art.mkdir(parents=True,exist_ok=True)
    (art/'spending.json').write_text(json.dumps({'direction':i,'hard_limit_usd':5,'reserved_max_usd':0,'token_cost_estimate_usd':0,'verified_billing_usd':None,'paid_requests':0,'entries':[],'note':'This study only; pre-existing runs are sunk costs. No new paid requests yet.'},indent=2)+'\n')
print(json.dumps({'snapshot_files':len(manifest),'development_matches':len(ids),'seal':seal}))
with gzip.open(ROOT/'inputs/earlier-run-risk/tasks.jsonl.gz','rt') as f: task=json.loads(next(f))
print('Task keys:',list(task));print('Scoring keys:',list(task['scoring']))
print('Task timing metadata:',{k:task[k] for k in ('match_id','decision','t','competition','kickoff') if k in task})
for name in ('predictions.jsonl','matches.jsonl'):
    row=json.loads((ROOT/'inputs/closed-loop-risk'/name).read_text().splitlines()[0])
    print(name,'keys',list(row))
