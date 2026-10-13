"""Bounded fresh Tinker joint and matched single forecasts on tied development games."""
import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import hashlib
import itertools
import json
import math
from pathlib import Path
import random
import sys

import numpy as np
import tinker
from tinker_cookbook import renderers
from tinker_cookbook.tokenizer_utils import get_tokenizer

ROOT=Path(__file__).resolve().parents[2]
REPO=ROOT/'rin-gym-forecast-only'
OUT=Path(__file__).resolve().parent
sys.path.insert(0,str(REPO))
from app.trade_env.tasks import read_tasks
from app.trade_env.train_tinker import read_answer
from experiments.forecast_only.core import guard,digest

SIDES=('home','draw','away')
KEYS=tuple(a+'_'+b for a,b in itertools.product(SIDES,repeat=2))
CHECKPOINT='tinker://d2426bc1-ec71-559d-bb81-e17da9372e58:train:0/sampler_weights/final'
LEDGER=ROOT/'rin-gym-simulation/data/evals/ledger.sqlite3'
SEED=20261013
REPEATS=3
MAX_TOKENS=384
RATES={'prefill':.195,'sample':.60}
SYSTEM='''Forecast soccer regulation-time outcomes using only the supplied observations. Regulation includes stoppage time and excludes extra time and penalties. A level final score is a draw. Market prices are useful noisy evidence. You are forecasting, not trading; there are no positions or actions to select. Return only the required JSON object, with finite numeric probabilities between 0 and 1 summing to 1. Do not include reasoning text, explanations, markdown or extra fields.'''
SINGLE_SYSTEM=SYSTEM+' Required object: one field named probs, containing exactly home, draw, away.'
JOINT_SYSTEM=SYSTEM+(' Two observations A and B are a synthetic retrospective pair, not necessarily simultaneous. '
    'Forecast their full joint distribution of regulation results. Each key names A result then B result. '
    'Account for the evidence in each observation; do not invent a relationship merely because both are currently tied. '
    'Required object: one field named joint, containing exactly these nine keys: '+', '.join(KEYS)+'.')


def body(task):
    text=task['messages'][1]['content']
    assert text.count('Buying now costs about:')==1
    return text.split('Buying now costs about:')[0].rstrip()


def observation(a,b,arm):
    if arm=='joint':
        return [{'role':'system','content':JOINT_SYSTEM},{'role':'user','content':'Observation A:\n'+body(a)+'\n\nObservation B:\n'+body(b)}]
    task=a if arm=='single_a' else b if arm=='single_b' else None
    if task is None:raise ValueError('Unknown arm')
    return [{'role':'system','content':SINGLE_SYSTEM},{'role':'user','content':body(task)}]


def parse(text,arm):
    def unique(items):
        value={}
        for key,v in items:
            if key in value:raise ValueError('Duplicate key')
            value[key]=v
        return value
    try:
        obj=json.loads(text,object_pairs_hook=unique)
        field='joint' if arm=='joint' else 'probs';keys=KEYS if arm=='joint' else SIDES
        if not isinstance(obj,dict) or set(obj)!={field}:return None
        values=obj[field]
        if not isinstance(values,dict) or set(values)!=set(keys):return None
        if any(type(values[k]) not in (int,float) or not math.isfinite(values[k]) or not 0<=values[k]<=1 for k in keys):return None
        total=math.fsum(values.values())
        if abs(total-1)>.02 or total<=0:return None
        return {k:values[k]/total for k in keys}
    except (ValueError,TypeError,OverflowError):return None


def append(path,row):
    with path.open('a') as f:f.write(json.dumps(row,allow_nan=False)+'\n');f.flush()


def renderer():return renderers.get_renderer('qwen3_disable_thinking',get_tokenizer('Qwen/Qwen3-8B'))


def prepare():
    source=REPO/'artifacts/earlier-run-risk/tasks.jsonl.gz'
    tasks=read_tasks(source);assert all(t['split']=='val' and not t['demo'] for t in tasks)
    by_key={(t['match_id'],t['decision']):t for t in tasks if t['position']=='flat'}
    context=ROOT/'outputs/parlay-eval-context'
    f=json.loads((context/'features.json').read_text())
    # Validate prefix feature and task artifacts against the prior checked context protocol.
    cp=json.loads((context/'protocol.json').read_text())
    for path,checksum in cp['source_hashes'].items():
        assert hashlib.sha256((ROOT/path).read_bytes()).hexdigest()==checksum
    seal=guard(LEDGER,{t['match_id'] for t in tasks})
    sys.path.insert(0,str(ROOT/'outputs/parlay-eval-context'))
    from run_context import load
    loaded=load()
    assert loaded['seal']==seal
    assert {str(k):v for k,v in loaded['features'].items()}==f
    candidates=[]
    for x,y in itertools.combinations(sorted(loaded['features']),2):
        a,b=loaded['features'][x],loaded['features'][y]
        if a['phase']!=b['phase'] or a['match_id']==b['match_id'] or loaded['teams'][x]&loaded['teams'][y]:continue
        if a['visible_home']!=a['visible_away'] or b['visible_home']!=b['visible_away']:continue
        candidates.append((a['match_id'],b['match_id'],a['phase']))
    random.Random(SEED).shuffle(candidates)
    chosen=[];used=set()
    for a,b,phase in candidates:
        if {a,b}&used:continue
        chosen.append((a,b,phase));used.update([a,b])
        if len(chosen)==24:break
    assert len(chosen)>=12 and len(used)==2*len(chosen)
    r=renderer();cases=[];total_tokens=0
    for i,(a,b,phase) in enumerate(chosen):
        ta,tb=by_key[a,phase],by_key[b,phase]
        assert all(t['scoring']['observed_score']['home']==t['scoring']['observed_score']['away'] for t in [ta,tb])
        observations={arm:observation(ta,tb,arm) for arm in ['joint','single_a','single_b']}
        lengths={arm:r.build_generation_prompt(messages).length for arm,messages in observations.items()}
        assert all(length+MAX_TOKENS<=32768 for length in lengths.values())
        total_tokens+=REPEATS*sum(lengths.values())
        cases.append({'id':f'tied-{i:03d}','match_ids':[a,b],'phase':phase,
            'decision_times':[ta['t'],tb['t']],'observation_sha256':{arm:digest(messages) for arm,messages in observations.items()},
            'prompt_tokens':lengths,'messages':observations,
            'evaluation':{'results':[ta['scoring']['result'],tb['scoring']['result']],
                'market_probs':[ta['scoring']['market_probs'],tb['scoring']['market_probs']]}})
    calls=len(cases)*REPEATS*3
    upper=(total_tokens*RATES['prefill']+calls*MAX_TOKENS*RATES['sample'])/1e6
    assert upper*2<2.5
    protocol={'checkpoint':CHECKPOINT,'cohort':'24 maximum seeded disjoint fixture pairs, same active checkpoint, both observed scores tied; selection ignores final outcomes.',
        'scope':'Fresh retrospective development forecasts; phase-matched pairs may be from different days. Not real-time simultaneous execution.',
        'cases':len(cases),'unique_games':len(used),'phase_counts':dict(Counter(c['phase'] for c in cases)),
        'arms':['joint','single_a','single_b'],'repeats':REPEATS,'temperature':1.,'seed':SEED,'max_tokens':MAX_TOKENS,
        'parser_tolerance':.02,'invalid_brier':2,'invalid_log_loss':float(-np.log(1e-6)),
        'primary':'Joint nine-cell Brier versus product of normalized contemporaneous market probabilities; repeats averaged within disjoint pair.',
        'secondary':'Joint versus product of fresh single-match forecasts; same checkpoint and observations. Joint marginal calibration, log loss, validity and paired disjoint-pair bootstrap.',
        'budget_usd':5,'request_reservation_ceiling_usd':2.5,'maximum_requests':calls,
        'max_token_cost_estimate_usd':upper,'rates_per_million':RATES,
        'pricing_source':'https://tinker-docs.thinkingmachines.ai/tinker/models/models_and_pricing/',
        'billing_limit':'Token-cost reservation with >2x margin; not a provider-enforced billing cap. No application-level inference retries or paid reruns.',
        'source_hashes':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [source,context/'features.json',context/'protocol.json',Path(__file__)]},
        'holdout_before':seal,'cases_sha256':digest(cases)}
    protocol['sha256']=digest(protocol)
    with (OUT/'protocol.json').open('x') as handle:json.dump(protocol,handle,indent=2)
    (OUT/'cases.json').write_text(json.dumps(cases,indent=2)+'\n')
    print(json.dumps({'cases':len(cases),'phase_counts':protocol['phase_counts'],'requests':calls,'maximum_token_cost_estimate_usd':upper,'holdout':seal}),flush=True)


async def live():
    p=json.loads((OUT/'protocol.json').read_text());cases=json.loads((OUT/'cases.json').read_text())
    phash=p.pop('sha256');assert digest(p)==phash;p['sha256']=phash
    assert digest(cases)==p['cases_sha256']
    for path,checksum in p['source_hashes'].items():assert hashlib.sha256((ROOT/path).read_bytes()).hexdigest()==checksum
    ids={mid for c in cases for mid in c['match_ids']};assert guard(LEDGER,ids)==p['holdout_before']
    r=renderer();prompts={(c['id'],arm):r.build_generation_prompt(c['messages'][arm]) for c in cases for arm in p['arms']}
    for c in cases:
        for arm in p['arms']:assert prompts[c['id'],arm].length==c['prompt_tokens'][arm]
    with (OUT/'STARTED.json').open('x') as f:json.dump({'utc':datetime.now(timezone.utc).isoformat(),'protocol_sha256':phash},f)
    service=tinker.ServiceClient();client=service.create_sampling_client(model_path=CHECKPOINT)
    sem=asyncio.Semaphore(8);reserved=0.;calls=0
    async def one(c,repeat,arm):
        nonlocal reserved,calls
        async with sem:
            assert guard(LEDGER,c['match_ids'])==p['holdout_before']
            prompt=prompts[c['id'],arm];cost=(prompt.length*RATES['prefill']+MAX_TOKENS*RATES['sample'])/1e6
            if calls>=p['maximum_requests'] or reserved+cost>p['request_reservation_ceiling_usd']:raise ValueError('Budget reservation exhausted')
            reserved+=cost;calls+=1
            seed=int(digest([SEED,c['id'],repeat])[:8],16)%2147483647
            receipt={'case_id':c['id'],'repeat':repeat,'arm':arm,'input_tokens':prompt.length,'seed':seed,
                'observation_sha256':c['observation_sha256'][arm],'protocol_sha256':phash,
                'requested_utc':datetime.now(timezone.utc).isoformat(),'kind':'retrospective_development_fresh_joint',
                'reserved_max_cost_usd':cost}
            append(OUT/'requests.jsonl',receipt)
            try:
                response=await client.sample_async(prompt=prompt,num_samples=1,sampling_params=tinker.SamplingParams(
                    max_tokens=MAX_TOKENS,temperature=p['temperature'],stop=r.get_stop_sequences(),seed=seed))
                seq=response.sequences[0];text,truncated=read_answer(r,seq.tokens)
                probs=None if truncated else parse(text,arm)
                row={**receipt,'completion':text,'truncated':truncated,'probs':probs,'output_tokens':len(seq.tokens),
                     'error':None if probs is not None else 'truncated' if truncated else 'invalid_schema_or_probabilities'}
            except Exception as error:
                # No retry. API failures count as invalid; reserved output cap remains charged in cost bound.
                row={**receipt,'completion':None,'truncated':False,'probs':None,'output_tokens':None,'error':type(error).__name__}
            row['completed_utc']=datetime.now(timezone.utc).isoformat();append(OUT/'predictions.jsonl',row)
            print(json.dumps({'completed':sum(1 for _ in (OUT/'predictions.jsonl').open()),'maximum':p['maximum_requests'],'last_arm':arm,'valid':row['probs'] is not None,'reserved_usd':reserved}),flush=True)
    await asyncio.gather(*(one(c,repeat,arm) for c in cases for repeat in range(REPEATS) for arm in p['arms']))
    after=guard(LEDGER,ids);assert after==p['holdout_before'] and calls==p['maximum_requests']
    (OUT/'COMPLETED.json').write_text(json.dumps({'requests':calls,'reserved_upper_cost_usd':reserved,'holdout_after':after},indent=2)+'\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--live',action='store_true');args=parser.parse_args()
    asyncio.run(live()) if args.live else prepare()
