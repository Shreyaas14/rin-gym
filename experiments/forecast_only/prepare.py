"""Freeze disjoint historical development cohorts before any model evaluation."""
import argparse
import hashlib
import json
from pathlib import Path
import random

from app.trade_env.config import make_config
from app.trade_env.dataset import load_release
from app.trade_env.splits import split_for
from app.trade_env.tasks import build_tasks, write_tasks
from experiments.forecast_only.core import guard, digest

if __name__ == '__main__':
    p=argparse.ArgumentParser();p.add_argument('--ledger',required=True);p.add_argument('--output',default='artifacts/forecast-only');a=p.parse_args()
    out=Path(a.output);out.mkdir(parents=True,exist_ok=True)
    if (out/'protocol.json').exists():raise SystemExit('Already frozen; refusing overwrite')
    cfg=make_config({'forecast_rewards':False,'football_rules':True})
    records=load_release()
    # Reject test/demo before building prompts or inspecting target distributions.
    eligible=[r for r in records if split_for(r,cfg) in ('train','val')]
    seal=guard(a.ledger,[r['match_id'] for r in eligible])
    selected=[]; cohorts={}
    for split,n in [('train',16),('val',16)]:
        rows=sorted([r for r in eligible if split_for(r,cfg)==split],key=lambda r:r['match_id'])
        random.Random('20261013:'+split).shuffle(rows)
        # Only require executable replay coverage; never select by outcome/probability.
        picked=[]
        for r in rows:
            ts,_=build_tasks([r],cfg)
            if len(ts)==20: picked.append(r)
            if len(picked)==n:break
        if len(picked)!=n:raise ValueError('Insufficient complete development matches')
        selected+=picked;cohorts[split]=sorted(r['match_id'] for r in picked)
    assert not set(cohorts['train']) & set(cohorts['val'])
    tasks,summary=build_tasks(selected,cfg)
    write_tasks(out/'tasks.jsonl.gz',tasks)
    protocol={'version':1,'seed':20261013,'cohorts':cohorts,'config':cfg,'ledger':str(Path(a.ledger).resolve()),'seal':seal,
      'model':'Qwen/Qwen3-8B','renderer':'qwen3_disable_thinking','lora_rank':32,'learning_rate':1e-5,
      'epochs':1,'groups_per_batch':4,'group_size':4,'max_steps':4,'max_tokens':128,'train_temperature':1.0,'eval_temperature':0.0,
      'target':'regulation_home_draw_away','reward':'negative multiclass Brier, mean per match; invalid=2',
      'execution':'one share; buy if predicted result probability - displayed buy cost > 0.02; hold to regulation settlement; no shorts, switching or re-entry; delayed recorded fills',
      'evaluation':'paired base/final/market on same 16 historical validation matches; equal match weighting; 4000 match bootstrap draws; no tuning or checkpoint selection',
      'budget_usd':5.0,'prices_per_million':{'prefill':0.195,'sample':0.60,'train':0.44},
      'pricing_source':'https://tinker-docs.thinkingmachines.ai/tinker/models/models_and_pricing/',
      'tasks_sha256':hashlib.sha256((out/'tasks.jsonl.gz').read_bytes()).hexdigest(),
      'source_manifest_sha256':hashlib.sha256(Path('data/release-archives/dataset-manifest.json').read_bytes()).hexdigest(),
      'note':'Historical development ablation; known fixtures may be memorized by base model; not prospective proof of generalization.'}
    protocol['protocol_sha256']=digest(protocol)
    (out/'protocol.json').write_text(json.dumps(protocol,indent=2)+'\n')
    print(json.dumps({'protocol_sha256':protocol['protocol_sha256'],'dataset':summary,'seal':seal},indent=2))
