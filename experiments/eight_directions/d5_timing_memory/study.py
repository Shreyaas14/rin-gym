"""Predeclared timing/memory factorial with cohort-filtered release reads."""
import argparse, asyncio, hashlib, json, math, os
from collections import defaultdict
from pathlib import Path
from datetime import datetime, timezone
import numpy as np
from experiments.forecast_only.core import SYSTEM, digest, guard, parse, brier
from experiments.eight_directions.budget import Spending
from experiments.forecast_only.risk_policy import RiskPolicy, choose_entry
from app.trade_env.config import OUTCOMES
from app.trade_env.clock import decision_points
from app.trade_env.state import build_state, shown_prices, fill_prices
from app.trade_env.costs import buy_cost
from app.trade_env.dataset import snapshot, EVENT_FIELDS, match_record, verify_market, market_rate
ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'artifacts/eight-directions/d5_timing_memory'
SETUP=ROOT/'research/eight-directions/setup.json'
RELEASE=Path('/Users/alexkan/Desktop/frontiers/rin-gym-forecast-only/data/raw/release-data-2026-10-13-v1')
ARMS=['sparse_no_memory','sparse_memory','minute_no_memory']
CHECKPOINT='tinker://d2426bc1-ec71-559d-bb81-e17da9372e58:train:0/sampler_weights/final'
MAX_TOKENS=128
REPEATS=2
RATES={'prefill':.195,'sample':.60}

def write(name,value):
    (OUT/name).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')
def append(name,value):
    with (OUT/name).open('a') as f:f.write(json.dumps(value,allow_nan=False)+'\n');f.flush();os.fsync(f.fileno())
def config():return json.loads((ROOT/'inputs/forecast-only/protocol.json').read_text())['config']
def initialize():
    setup=json.loads(SETUP.read_text());ids=setup['chronological_replay_partition']['assessment_match_ids'][:8]
    before=guard(setup['guard_ledger'],ids)
    pro={'hypothesis':'Minute updates reduce stale-forecast error; compact memory may improve forecasts at fixed sparse observation cadence.',
      'cohort_match_ids':ids,'selection':'First eight IDs in frozen chronological later-33 assessment partition, fixed before selective loading outcomes; no replacements.',
      'scope':'Repeatedly inspected retrospective development; no fitting, tuning or protected evaluation.',
      'arms':ARMS,'repeats':REPEATS,'checkpoint':CHECKPOINT,
      'intervention':'Same compact forecast-only current observation prompt. Sparse original five times versus every supported minute from first-half minute15 through44, halftime, second-half minutes1 through44. Quotes must satisfy original as-of age90/coherence. Do not require future-fill coverage for forecast support. Memory separately appends last three earlier original sparse snapshots of observed score/totals/prices at fixed sparse cadence in BOTH schedules; no model predictions, results or fills in memory.',
      'prompt':'Compact JSON: observed score, cumulative counts, phase/team identity, shown prices, last12 observed event rows since preceding original sparse point. Same prompt and seed at common decision times. Original full template replaced consistently across all arms; cadence pilot, not exact original-template reproduction.',
      'common_times':'Minute schedule points with valid quotes, including original sparse common points and halftime; sparse forecast carried forward only after first supported sparse point. Dense available updates carried forward only if supported; same common clock for all arms. Include equal-match scores averaged over minutes then seeds. Report exact original common-decision subset separately.',
      'metrics':['Brier sum invalid=2','log loss floor1e-4 invalid=-log1e-4','valid forecast rate','normalized contemporaneous market baseline'],
      'uncertainty':'Paired match cluster bootstrap10000 seed20261013; seeds and minutes stay within whole-match clusters; shared teams and inspected development limit claims.',
      'execution':'Secondary frozen-signal counterfactual, fixed RiskPolicy 0.25 shares edge>0.05 displayed cost<=0.65; delayed fill cost cap0.65; hold settlement max one entry; fill delay60/maxwait120; execution simulator alone sees future fills. Quote-supported forecast rows chosen before any fill check. Pending order prevents another order until delayed fill window passes. No portfolio feedback in forecast prompt.',
      'config':config(),'guard_before':before,'stopping':'One run, no retries or automatic restarts; stop new scheduling on first failed request. No changes after outcomes.','seed_recipe':'digest([20261013,repeat,match_id,decision])[:8] mod2147483647','hard_limit_usd':5,
      'release':str(RELEASE),'source_manifest_sha256':hashlib.sha256((ROOT/'inputs/MANIFEST.json').read_bytes()).hexdigest()}
    OUT.mkdir(parents=True,exist_ok=True)
    with (OUT/'protocol.json').open('x') as f:json.dump(pro,f,indent=2);f.write('\n')
    write('guard_before.json',before)
    return pro

def selective_load(ids):
    """Every table containing match outcomes/events is filtered by guarded cohort."""
    import pyarrow.parquet as pq
    data=snapshot(RELEASE,'fifa-markets');history=snapshot(RELEASE,'fifa-market-history')
    receipt=[]
    def read(path,columns=None,filters=None):
        assert filters and ('match_id','in',ids) in filters
        rows=pq.read_table(path,columns=columns,filters=filters).to_pylist()
        assert all(r['match_id'] in ids for r in rows)
        receipt.append({'path':str(path),'filters':filters,'rows':len(rows),'columns':columns})
        return rows
    matches=read(data/'matches.parquet',filters=[('match_id','in',ids)])
    assert {m['match_id'] for m in matches}==set(ids)
    events=defaultdict(list)
    cols=list(dict.fromkeys(['match_id',*EVENT_FIELDS.values(),'event_time_utc','source_event_json']))
    for e in read(data/'events.parquet',cols,[('match_id','in',ids)]):events[e['match_id']].append(e)
    selected={}
    for m in matches:
        assert len(m['prediction_markets'])==3
        for market in m['prediction_markets']:selected[market['token_id']]=(m,market)
    rates={}
    for row in read(history/'markets.parquet',['match_id','market_id','hda_outcome','tokens','source_market_json'],[('match_id','in',ids)]):
        raw=json.loads(row['source_market_json'])
        for token in row['tokens']:
            if token['token_id'] in selected:
                key=token['token_id'];assert token['outcome']=='Yes' and key not in rates
                verify_market(*selected[key],row,raw);rates[key]=market_rate(raw)
    assert set(rates)==set(selected)
    prices=defaultdict(list)
    for row in read(history/'prices.parquet',['match_id','token_id','timestamp','price'],[('match_id','in',ids),('token_id','in',list(selected))]):
        assert row['match_id']==selected[row['token_id']][0]['match_id'] and math.isfinite(row['timestamp']) and 0<=row['price']<=1
        prices[row['token_id']].append([row['timestamp'],row['price']])
    records=[]
    for m in matches:
        r=match_record(m,events[m['match_id']]);r.update(prices={},markets={},fee_rates={})
        for market in m['prediction_markets']:
            o=market['outcome'];key=market['token_id'];series=sorted(prices[key]);assert series and all(a[0]<b[0] for a,b in zip(series,series[1:]))
            r['prices'][o]=series;r['markets'][o]={k:market[k] for k in ('market_id','token_id')};r['fee_rates'][o]=rates[key]
        records.append(r)
    write('selective_loading_receipts.json',receipt)
    return {r['match_id']:r for r in records}

def observations(records,pro):
    cfg=pro['config'];rows=[];coverage=[]
    for mid in pro['cohort_match_ids']:
        r=records[mid];sparse=decision_points(r['periods'],cfg)
        densecfg={**cfg,'decisions':[f'h1+{i}' for i in range(15,45)]+['ht']+[f'h2+{i}' for i in range(1,45)]}
        dense=decision_points(r['periods'],densecfg);memory=[];kept=[];drops=[]
        for point in dense:
            shown,reason=shown_prices(r['prices'],point['t'],cfg)
            if reason:drops.append({'decision':point['name'],'reason':reason});continue
            prior=[p for p in sparse if p['t']<point['t']]
            points=prior+[point];state=build_state(r,points,len(prior),'flat',shown,cfg)
            # Only observed state is projected. build_state has no labels/fills.
            body={k:state[k] for k in ['names','phase','score','totals','prices']}
            body['recent_events']=state['events'][-12:]
            base=[{'role':'system','content':SYSTEM},{'role':'user','content':json.dumps(body,separators=(',',':'))}]
            for mem in [False,True]:
                msgs=[dict(m) for m in base]
                if mem:msgs[1]['content']+='\nCompact earlier observations (past only): '+json.dumps(memory[-3:],separators=(',',':'))
                rows.append({'match_id':mid,'decision':point['name'],'t':point['t'],'memory':mem,'sparse':point['name'] in cfg['decisions'],'messages':msgs,'shown':shown,'event_cutoff':point['t']-cfg['event_delay_s'],'memory_latest_t':memory[-1]['t'] if memory else None})
            kept.append(point['name'])
            if point['name'] in cfg['decisions']:memory.append({'t':point['t'],'phase':state['phase'],'score':state['score'],'totals':state['totals'],'prices':shown})
        coverage.append({'match_id':mid,'minute_requested':len(dense),'minute_supported':len(kept),'original_supported':sum(k in cfg['decisions'] for k in kept),'drops':drops})
    write('coverage.json',coverage)
    return rows

def renderer():
    from tinker_cookbook import renderers
    from tinker_cookbook.tokenizer_utils import get_tokenizer
    return renderers.get_renderer('qwen3_disable_thinking',get_tokenizer('Qwen/Qwen3-8B'))
def planned_requests(rows,rend):
    plans=[]
    for row in rows:
        for schedule in ([] if row['memory'] else ['minute'])+(['sparse'] if row['sparse'] else []):
            arm=schedule+('_memory' if row['memory'] else '_no_memory')
            for repeat in range(REPEATS):
                n=rend.build_generation_prompt(row['messages']).length
                plans.append({'request_id':f"{arm}:{repeat}:{row['match_id']}:{row['decision']}",'arm':arm,'repeat':repeat,'match_id':row['match_id'],'decision':row['decision'],'seed':int(digest([20261013,repeat,row['match_id'],row['decision']])[:8],16)%2147483647,'input_tokens':n,'maximum_output_tokens':MAX_TOKENS,'maximum_reserved_usd':2*(n*RATES['prefill']+MAX_TOKENS*RATES['sample'])/1e6,'observation_sha256':digest(row['messages'])})
    return plans

def prepare():
    if (OUT/'protocol.json').exists():
        assert not (OUT/'started.json').exists() and not (OUT/'predictions.jsonl').exists()
        pro=json.loads((OUT/'protocol.json').read_text())
        if not (OUT/'protocol_initial.json').exists():write('protocol_initial.json',pro)
        pro['arms']=ARMS
        pro['amendment']={'utc':datetime.now(timezone.utc).isoformat(),'reason':'Reduce cost/runtime before any predictions; no outcome/metric basis. Drop minute-memory interaction arm; keep two separate primary contrasts and all eight prespecified games.'}
        pro['intervention']=pro['intervention'].replace(' in BOTH schedules','; primary memory at sparse cadence only')
        pro['source_timing_limitation']='France-Iraq match400021511 has a known long first-half-end to second-half-start gap in source timestamps; included without replacement or outcome-based exclusion. Historical phase reconstruction may make stale carry-forward intervals long.'
    else:pro=initialize()
    pro['code_sha256']={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path(__file__),Path(__file__).with_name('analyze.py'),ROOT/'experiments/eight_directions/budget.py',ROOT/'experiments/forecast_only/core.py',ROOT/'app/trade_env/state.py',ROOT/'app/trade_env/dataset.py']}
    write('protocol.json',pro)
    records=selective_load(pro['cohort_match_ids']);rows=observations(records,pro);plans=planned_requests(rows,renderer())
    write('observations.json',rows);write('planned_requests.json',plans)
    pre={'calls':len(plans),'input_tokens':sum(p['input_tokens'] for p in plans),'max_prompt_tokens':max(p['input_tokens'] for p in plans),'maximum_output_tokens':len(plans)*MAX_TOKENS,'maximum_token_cost_usd':sum(p['maximum_reserved_usd'] for p in plans)/2,'reserved_2x_max_usd':sum(p['maximum_reserved_usd'] for p in plans),'rates':RATES,'paid_requests':0,'prompt_checks':{'no_labels_or_fills':True,'common_time_messages_same':True,'memory_latest_before_decision':all(r['memory_latest_t'] is None or r['memory_latest_t']<r['t'] for r in rows)},'coverage':json.loads((OUT/'coverage.json').read_text())}
    assert pre['reserved_2x_max_usd']<=4.5
    write('preflight.json',pre);write('guard_after_preflight.json',guard(json.loads(SETUP.read_text())['guard_ledger'],pro['cohort_match_ids']))
    print(json.dumps(pre,indent=2))

async def live():
    import tinker
    from tinker.lib.retry_handler import RetryConfig
    from app.trade_env.train_tinker import read_answer
    pro=json.loads((OUT/'protocol.json').read_text());plans=json.loads((OUT/'planned_requests.json').read_text());rows=json.loads((OUT/'observations.json').read_text());lookup={(r['match_id'],r['decision'],r['memory']):r for r in rows};rend=renderer();setup=json.loads(SETUP.read_text())
    assert guard(setup['guard_ledger'],pro['cohort_match_ids'])==pro['guard_before']
    if (OUT/'started.json').exists():raise RuntimeError('No automatic restart')
    service=tinker.ServiceClient(user_metadata={'study':'soccer-eight-directions-20261013','direction':'d5'});client=service.create_sampling_client(model_path=CHECKPOINT,retry_config=RetryConfig(enable_retry_logic=False))
    with (OUT/'started.json').open('x') as f:json.dump({'utc':datetime.now(timezone.utc).isoformat(),'protocol_sha256':digest(pro)},f)
    spending=Spending(OUT/'spending.json');assert not spending.read()['entries']
    queue=asyncio.Queue();stopped=asyncio.Event()
    for p in plans:queue.put_nowait(p)
    async def worker():
        while not stopped.is_set():
            try:p=queue.get_nowait()
            except asyncio.QueueEmpty:return
            row=lookup[p['match_id'],p['decision'],p['arm'].endswith('_memory') and not p['arm'].endswith('_no_memory')]
            try:
                prompt=rend.build_generation_prompt(row['messages']);assert prompt.length==p['input_tokens'] and digest(row['messages'])==p['observation_sha256']
                spending.reserve(p['request_id'],p['maximum_reserved_usd'],p);append('requests.jsonl',{**p,'requested_utc':datetime.now(timezone.utc).isoformat(),'protocol_sha256':digest(pro)})
                response=await client.sample_async(prompt=prompt,num_samples=1,sampling_params=tinker.SamplingParams(max_tokens=MAX_TOKENS,temperature=1.,stop=rend.get_stop_sequences(),seed=p['seed']))
                seq=response.sequences[0];text,truncated=read_answer(rend,seq.tokens);probs=None if truncated else parse(text)
                append('predictions.jsonl',{**p,'t':row['t'],'completion':text,'truncated':truncated,'probs':probs,'valid':probs is not None,'output_tokens':len(seq.tokens)})
                spending.complete(p['request_id'],(prompt.length*RATES['prefill']+len(seq.tokens)*RATES['sample'])/1e6,{'input_tokens':prompt.length,'output_tokens':len(seq.tokens)})
                n=spending.read()['paid_requests']
                if n%100==0:print(json.dumps({'completed':n,'planned':len(plans)}),flush=True)
            except Exception as e:stopped.set();append('failures.jsonl',{**p,'safe_error_class':type(e).__name__})
            finally:queue.task_done()
    await asyncio.gather(*(worker() for _ in range(8)))
    after=guard(setup['guard_ledger'],pro['cohort_match_ids']);assert after==pro['guard_before'];write('guard_after.json',after)
    write('run_status.json',{'completed':spending.read()['paid_requests'],'planned':len(plans),'unscheduled':queue.qsize(),'stopped':stopped.is_set()})
    print(json.dumps(json.loads((OUT/'run_status.json').read_text())),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--live',action='store_true');a=p.parse_args();asyncio.run(live()) if a.live else prepare()
