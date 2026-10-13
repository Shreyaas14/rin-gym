"""Fresh single forecasts across the complete existing both-tied development cohort."""
import argparse
import asyncio
from collections import Counter
from datetime import datetime,timezone
import hashlib
import itertools
import json
from pathlib import Path
import sys

import tinker

ROOT=Path(__file__).resolve().parents[2]
OUT=Path(__file__).resolve().parent
OLD=ROOT/'outputs/parlay-eval-fresh-joint'
sys.path.insert(0,str(OLD))
from experiment import CHECKPOINT,LEDGER,SEED,REPEATS,MAX_TOKENS,RATES,renderer,observation,parse,append,digest,guard,read_answer
sys.path.insert(0,str(ROOT/'outputs/parlay-eval-context'))
from run_context import load


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare():
    data=load();features=data['features']
    chosen=sorted([i for i,f in features.items() if f['visible_home']==f['visible_away']])
    used=[i for i in chosen if any(j!=i and features[j]['phase']==features[i]['phase'] and not data['teams'][i]&data['teams'][j] for j in chosen)]
    assert len(used)==len(chosen)==132
    old_cases=json.loads((OLD/'cases.json').read_text())
    previous={(tuple(sorted(c['match_ids'])),c['phase']) for c in old_cases}
    points=[];r=renderer()
    for i in used:
        f=features[i];t=data['at'][f['match_id'],f['phase']]
        assert t['split']=='val' and not t['demo']
        messages=observation(t,t,'single_a')
        length=r.build_generation_prompt(messages).length
        assert length+MAX_TOKENS<=32768
        points.append({'id':f['match_id']+':'+f['phase'],'match_id':f['match_id'],'phase':f['phase'],'decision_t':f['decision_t'],
            'visible_score':[f['visible_home'],f['visible_away']], 'event_cutoff':f['event_cutoff'],
            'messages':messages,'observation_sha256':digest(messages),'prompt_tokens':length,
            'evaluation':{'result':t['scoring']['result'],'market_probs':t['scoring']['market_probs']}})
    by_id={i:k for k,i in enumerate(used)}
    pairs=[]
    for a,b in itertools.combinations(used,2):
        fa,fb=features[a],features[b]
        if fa['phase']!=fb['phase'] or fa['match_id']==fb['match_id'] or data['teams'][a]&data['teams'][b]:continue
        original=(tuple(sorted([fa['match_id'],fb['match_id']])),fa['phase']) in previous
        pairs.append({'indices':[by_id[a],by_id[b]],'phase':fa['phase'],'match_ids':[fa['match_id'],fb['match_id']],
            'in_original_24':original})
    assert len(pairs)==2170 and sum(p['in_original_24'] for p in pairs)==24
    ids=sorted({p['match_id'] for p in points});seal=guard(LEDGER,ids);assert seal==data['seal']
    calls=len(points)*REPEATS
    inputs=sum(p['prompt_tokens'] for p in points)*REPEATS
    upper=(inputs*RATES['prefill']+calls*MAX_TOKENS*RATES['sample'])/1e6
    assert upper*2<2.5
    old_protocol=json.loads((OLD/'protocol.json').read_text())
    assert old_protocol['checkpoint']==CHECKPOINT and old_protocol['temperature']==1.
    protocol={'scope':'Retrospective development expansion; additional combinations reuse many of the original games. No independent holdout claim.',
        'checkpoint':CHECKPOINT,'prompt':'Exact existing fresh single-match prompt, no numeric probability example, position or action fields.',
        'repeats':REPEATS,'temperature':1.,'max_tokens':MAX_TOKENS,'seed':SEED,
        'selection':'Every existing active-play flat observation whose delayed visible score is tied and that has an eligible same-phase distinct-match partner; no shared-team pair.',
        'observations':len(points),'unique_matches':len(ids),'phase_counts':dict(Counter(p['phase'] for p in points)),
        'pair_points':len(pairs),'additional_pair_points':sum(not p['in_original_24'] for p in pairs),
        'original_pair_points':sum(p['in_original_24'] for p in pairs),
        'primary':'Additional combinations excluding the original 24 exact pair/checkpoint cases; average repeats within pair, pairs within phase, then four phases equally.',
        'secondary':'All combinations; original 24 under new outputs; each phase; pair-count-weighted aggregate; per-game single forecast Brier.',
        'uncertainty':'4000 underlying-game multinomial bootstrap draws, recomposing distinct-fixture pair weights within phase; no independent pair-row bootstrap. Shared-team/tournament dependence remains.',
        'invalid':'Exact schema, finite [0,1], probability sum within 0.02; invalid single invalidates a product, Brier 2 and log loss -log(1e-6).',
        'baseline':'Product of normalized contemporaneous market probabilities on identical pairs.',
        'budget_usd':5,'request_reservation_ceiling_usd':2.5,'maximum_requests':calls,'max_token_cost_estimate_usd':upper,
        'rates_per_million':RATES,'pricing_source':old_protocol['pricing_source'],
        'inference_policy':'Freshly issue every selected observation, once per repeat; share those outputs across tickets; no application retries or paid reruns.',
        'token_limit_note':'Token-cost reservation with >2x margin; not a provider-enforced dollar cap.',
        'source_hashes':{str(p.relative_to(ROOT)):sha(p) for p in data['sources']+[OLD/'experiment.py',OLD/'cases.json',Path(__file__)]},
        'points_sha256':digest(points),'pairs_sha256':digest(pairs),'holdout_before':seal}
    protocol['sha256']=digest(protocol)
    with (OUT/'protocol.json').open('x') as f:json.dump(protocol,f,indent=2)
    (OUT/'observations.json').write_text(json.dumps(points,indent=2)+'\n')
    (OUT/'pairs.json').write_text(json.dumps(pairs,indent=2)+'\n')
    print(json.dumps({'observations':len(points),'games':len(ids),'pair_points':len(pairs),'additional_pairs':protocol['additional_pair_points'],
        'maximum_requests':calls,'maximum_token_cost_estimate_usd':upper,'phase_counts':protocol['phase_counts'],'holdout':seal}),flush=True)


async def live():
    p=json.loads((OUT/'protocol.json').read_text());checksum=p.pop('sha256');assert digest(p)==checksum;p['sha256']=checksum
    points=json.loads((OUT/'observations.json').read_text());pairs=json.loads((OUT/'pairs.json').read_text())
    assert digest(points)==p['points_sha256'] and digest(pairs)==p['pairs_sha256']
    for path,expected in p['source_hashes'].items():assert sha(ROOT/path)==expected
    ids={point['match_id'] for point in points};assert guard(LEDGER,ids)==p['holdout_before']
    r=renderer();prompts={point['id']:r.build_generation_prompt(point['messages']) for point in points}
    assert all(prompts[point['id']].length==point['prompt_tokens'] for point in points)
    with (OUT/'STARTED.json').open('x') as f:json.dump({'utc':datetime.now(timezone.utc).isoformat(),'protocol_sha256':checksum},f)
    service=tinker.ServiceClient();client=service.create_sampling_client(model_path=CHECKPOINT)
    sem=asyncio.Semaphore(8);reserved=0.;calls=0;completed=0
    async def one(point,repeat):
        nonlocal reserved,calls,completed
        async with sem:
            assert guard(LEDGER,[point['match_id']])==p['holdout_before']
            prompt=prompts[point['id']]
            cost=(prompt.length*RATES['prefill']+MAX_TOKENS*RATES['sample'])/1e6
            if calls>=p['maximum_requests'] or reserved+cost>p['request_reservation_ceiling_usd']:raise ValueError('Budget exhausted')
            reserved+=cost;calls+=1
            seed=int(digest([SEED,'expanded-single',point['id'],repeat])[:8],16)%2147483647
            receipt={'observation_id':point['id'],'repeat':repeat,'seed':seed,'input_tokens':prompt.length,
                'observation_sha256':point['observation_sha256'],'protocol_sha256':checksum,'reserved_max_cost_usd':cost,
                'requested_utc':datetime.now(timezone.utc).isoformat(),'kind':'retrospective_development_fresh_single_expansion'}
            append(OUT/'requests.jsonl',receipt)
            try:
                response=await client.sample_async(prompt=prompt,num_samples=1,sampling_params=tinker.SamplingParams(
                    max_tokens=MAX_TOKENS,temperature=p['temperature'],stop=r.get_stop_sequences(),seed=seed))
                seq=response.sequences[0];text,truncated=read_answer(r,seq.tokens);probs=None if truncated else parse(text,'single_a')
                row={**receipt,'completion':text,'truncated':truncated,'probs':probs,'output_tokens':len(seq.tokens),
                     'error':None if probs is not None else 'truncated' if truncated else 'invalid_schema_or_probabilities'}
            except Exception as error:
                row={**receipt,'completion':None,'truncated':False,'probs':None,'output_tokens':None,'error':type(error).__name__}
            row['completed_utc']=datetime.now(timezone.utc).isoformat();append(OUT/'predictions.jsonl',row);completed+=1
            if completed%10==0 or completed==p['maximum_requests']:
                print(json.dumps({'completed':completed,'maximum':p['maximum_requests'],'reserved_usd':reserved}),flush=True)
    await asyncio.gather(*(one(point,repeat) for point in points for repeat in range(REPEATS)))
    seal=guard(LEDGER,ids);assert seal==p['holdout_before'] and calls==p['maximum_requests']
    (OUT/'COMPLETED.json').write_text(json.dumps({'requests':calls,'reserved_max_cost_usd':reserved,'holdout_after':seal},indent=2)+'\n')


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--live',action='store_true');args=ap.parse_args()
    asyncio.run(live()) if args.live else prepare()
