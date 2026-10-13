"""Matched market-prior prompt inference on a frozen original-position tape."""
import asyncio, argparse, hashlib, json
from datetime import datetime,timezone
from experiments.eight_directions.d2_market_prior.study import ROOT,OUT,SETUP,SIDES,write
from experiments.eight_directions.budget import Spending
from experiments.forecast_only.core import digest,guard
from app.trade_env.tasks import read_tasks

INSTRUCTION=('\nMarket-prior forecasting instruction: for your regulation-result probs, start from the normalized current displayed prices supplied below. Adjust each outcome probability only when the observed match information supports it, by at most 0.10 in absolute probability from that prior. Preserve probabilities summing to one and the complete existing five-field JSON schema. Market prices are noisy evidence; unchanged prices are an acceptable forecast.')
ARMS=['original_prompt','market_prior_prompt'];MAX_TOKENS=256;SEED=20261013
CHECKPOINT='tinker://d2426bc1-ec71-559d-bb81-e17da9372e58:train:0/sampler_weights/final'
RATES={'prefill':.195,'sample':.60}

def matched_tasks():
    s=json.loads(SETUP.read_text());ids=set(s['chronological_replay_partition']['assessment_match_ids'])
    tm={t['id']:t for t in read_tasks(ROOT/'inputs/earlier-run-risk/tasks.jsonl.gz')}
    rows=[json.loads(l) for l in (ROOT/'inputs/closed-loop-risk/predictions.jsonl').read_text().splitlines()]
    rows=[r for r in rows if r['arm']=='original' and tm[r['task_id']]['match_id'] in ids]
    return [(r,tm[r['task_id']]) for r in sorted(rows,key=lambda r:(r['repeat'],tm[r['task_id']]['match_id'],tm[r['task_id']]['t']))]

def messages(task,arm):
    if arm not in ARMS:raise ValueError('Unknown arm')
    ms=[dict(m) for m in task['messages']]
    if arm=='market_prior_prompt':
        ms[0]['content']+=INSTRUCTION
        # Derived ONLY from currently displayed mid prices, not result/future fill.
        prices=task['scoring']['shown_prices'];total=sum(prices.values());p={k:prices[k]/total for k in SIDES}
        ms[1]['content']+='\nNormalized current market prior: '+json.dumps(p,separators=(',',':'))
    return ms

def seed(r,t):return int(digest([SEED,r['repeat'],t['match_id'],t['decision']])[:8],16)%2147483647

def renderer():
    from tinker_cookbook import renderers
    from tinker_cookbook.tokenizer_utils import get_tokenizer
    return renderers.get_renderer('qwen3_disable_thinking',get_tokenizer('Qwen/Qwen3-8B'))

def prepare():
    rend=renderer();pairs=matched_tasks();calls=0;tokens=0;maximum_prompt=0;planned=[]
    for r,t in pairs:
        for arm in ARMS:
            ms=messages(t,arm);n=rend.build_generation_prompt(ms).length
            tokens+=n;calls+=1;maximum_prompt=max(maximum_prompt,n)
            planned.append({'request_id':f"{arm}:{r['repeat']}:{t['id']}",'arm':arm,'repeat':r['repeat'],'task_id':t['id'],'seed':seed(r,t),'observation_sha256':digest(ms),'input_tokens':n,'maximum_output_tokens':MAX_TOKENS,'reserved_max_usd':2*(n*RATES['prefill']+MAX_TOKENS*RATES['sample'])/1e6})
    assert calls==990 and len(pairs)==495
    upper=(tokens*RATES['prefill']+calls*MAX_TOKENS*RATES['sample'])/1e6
    result={'maximum_calls':calls,'input_tokens':tokens,'maximum_output_tokens':calls*MAX_TOKENS,'maximum_prompt_tokens':maximum_prompt,'rates_per_million':RATES,'maximum_token_cost_usd':upper,'reservation_with_2x_headroom_usd':upper*2,'maximum_tokens_each':MAX_TOKENS,'arms':ARMS,'instruction':INSTRUCTION,'assessment_matches':33,'repeats':3,'pairs':calls//2,'status':'preflight_only_no_requests','checkpoint':CHECKPOINT}
    write('fresh_preflight.json',result)
    setup=json.loads(SETUP.read_text());ids=sorted({t['match_id'] for _,t in pairs});seal=guard(setup['guard_ledger'],ids)
    pro={'hypothesis':'Explicit market-prior prompting with small adjustment instruction may improve proper scores beyond both contemporaneous market and original market-visible prompt.',
      'kind':'Fresh matched prompt-only predictions at frozen original observed state/positions; no closed-loop forecast/position feedback',
      'cohort_match_ids':ids,'cohort_selection':'All frozen later 33 assessment games; all original observed decision times, three repeats; selected by chronological timing only before any fresh outcomes',
      'arms':ARMS,'market_prior_instruction':INSTRUCTION,'control':'Unmodified original messages; same observed prices/events/position as treatment','changed_fields':'system appended instruction and user appended normalized current displayed-price prior only; five-field response schema unchanged',
      'checkpoint':CHECKPOINT,'checkpoint_verification':json.loads((ROOT/'research/eight-directions/checkpoint-verification.json').read_text()),
      'settings':{'renderer':'qwen3_disable_thinking','temperature':1,'max_tokens':MAX_TOKENS,'seed_recipe':'digest([20261013,repeat,match_id,decision]) first8hex mod2147483647','max_concurrent':8,'automatic_retries':False},
      'metrics':'Equal-match Brier sum and log loss floor1e-4; invalid=2/-log1e-4/no trade in every model arm. Paired match bootstrap10000 seed20261013; preserve repeats and common times. All complete arm pairs included; audit missing requests, complete pairs, matches and repeat cases. No outcome-based exclusion.',
      'execution':'Same frozen-signal 0.25-share entry/hold executor as offline study; future fills never exposed to inference or order selection. Advisory actions ignored for fixed-rule P&L.',
      'adjustment_cap':'Instruction only; audit empirical per-outcome violation rate without rejecting completed predictions.',
      'stopping':'Single run; no automatic restart. Stop new scheduling on first failed paid request; allow already in-flight requests to finish; preserve failed reservation. Never change cohort/thresholds afterward.',
      'sources':json.loads((OUT/'protocol.json').read_text())['source_sha256'],
      'code_sha256':{str(Path):hashlib.sha256(Path.read_bytes()).hexdigest() for Path in [__import__('pathlib').Path(__file__),ROOT/'experiments/eight_directions/budget.py']},
      'preflight':result,'pricing':{'source':'https://tinker-docs.thinkingmachines.ai/tinker/models/models_and_pricing/','verified_date':'2026-10-13','rates_per_million':RATES},'guard_before':seal,'planned_requests':planned,'hard_limit_usd':5}
    if (OUT/'fresh_protocol.json').exists():
        existing=json.loads((OUT/'fresh_protocol.json').read_text());assert existing['planned_requests']==pro['planned_requests'];assert existing['market_prior_instruction']==INSTRUCTION
    else:
        with (OUT/'fresh_protocol.json').open('x') as f:json.dump(pro,f,indent=2,allow_nan=False);f.write('\n')
    print(json.dumps(result,indent=2))

def append(name,row):
    with (OUT/name).open('a') as f:f.write(json.dumps(row,allow_nan=False)+'\n');f.flush()

async def live():
    import tinker
    from tinker.lib.retry_handler import RetryConfig
    from app.trade_env.reward import score
    from app.trade_env.train_tinker import read_answer
    pro=json.loads((OUT/'fresh_protocol.json').read_text());setup=json.loads(SETUP.read_text());pairs=matched_tasks()
    seal=guard(setup['guard_ledger'],pro['cohort_match_ids']);assert seal==pro['guard_before']
    if (OUT/'fresh_started.json').exists():raise RuntimeError('Existing paid-start marker; reconcile manually, never automatic restart')
    rend=renderer()
    try:
        service=tinker.ServiceClient(user_metadata={'study':'soccer-eight-directions-20261013','direction':'d2'});client=service.create_sampling_client(model_path=CHECKPOINT,retry_config=RetryConfig(enable_retry_logic=False))
    except Exception as e:
        write('fresh_blocked.json',{'stage':'sampling_client_initialization','safe_error_class':type(e).__name__,'paid_requests':0,'note':'No credential inspection or workaround; no automatic restart.'});return
    config=json.loads((ROOT/'inputs/closed-loop-risk/protocol.json').read_text())['config']
    with (OUT/'fresh_started.json').open('x') as f:json.dump({'utc':datetime.now(timezone.utc).isoformat(),'seal':seal,'protocol_sha256':digest(pro)},f)
    spending=Spending(OUT/'spending.json')
    old=spending.read();assert not old.get('entries') and old.get('paid_requests',0)==0
    spending.write({'hard_limit_usd':5,'reserved_max_usd':0.,'token_cost_estimate_usd':0.,'billing_evidence_usd':None,'paid_requests':0,'entries':[],'note':'2x maximum-token reservations; no invoice available; no application/SDK retry logic.'})
    plans={q['request_id']:q for q in pro['planned_requests']};queue=asyncio.Queue();stopped=asyncio.Event()
    for r,t in pairs:
        for arm in ARMS:queue.put_nowait((r,t,arm))
    async def one(r,t,arm):
        reqid=f"{arm}:{r['repeat']}:{t['id']}";plan=plans[reqid];ms=messages(t,arm);prompt=rend.build_generation_prompt(ms)
        assert plan['input_tokens']==prompt.length and plan['observation_sha256']==digest(ms) and plan['seed']==seed(r,t)
        spending.reserve(reqid,plan['reserved_max_usd'],{'arm':arm,'task_id':t['id'],'repeat':r['repeat'],'input_tokens':prompt.length,'maximum_output_tokens':MAX_TOKENS})
        receipt={**plan,'observed_t':t['t'],'position':t['position'],'protocol_sha256':digest(pro),'requested_utc':datetime.now(timezone.utc).isoformat()}
        append('fresh_requests.jsonl',receipt)
        try:
            response=await client.sample_async(prompt=prompt,num_samples=1,sampling_params=tinker.SamplingParams(max_tokens=MAX_TOKENS,temperature=1.,stop=rend.get_stop_sequences(),seed=plan['seed']))
        except Exception as e:
            stopped.set();append('fresh_failures.jsonl',{**receipt,'safe_error_class':type(e).__name__})
            state=spending.read();entry=next(x for x in state['entries'] if x['request_id']==reqid);entry['failure_class']=type(e).__name__;entry['note']='Failed request remains reserved; billing unknown';spending.write(state)
            return
        seq=response.sequences[0];text,truncated=read_answer(rend,seq.tokens);parsed=score(t,None if truncated else text,config)
        row={**receipt,'completion':text,'truncated':truncated,'output_tokens':len(seq.tokens),'valid':parsed['valid'],'probs':parsed['probs'],'parse_error':parsed['error'],'advisory_action':parsed['action'],'completed_utc':datetime.now(timezone.utc).isoformat()}
        append('fresh_predictions.jsonl',row);spending.complete(reqid,(prompt.length*RATES['prefill']+len(seq.tokens)*RATES['sample'])/1e6,{'input_tokens':prompt.length,'output_tokens':len(seq.tokens)})
        n=spending.read()['paid_requests']
        if n%100==0:print(json.dumps({'completed_requests':n,'reserved_max_usd':spending.read()['reserved_max_usd']}),flush=True)
    async def worker():
        while not stopped.is_set():
            try:r,t,a=queue.get_nowait()
            except asyncio.QueueEmpty:return
            try:await one(r,t,a)
            except Exception as e:
                stopped.set();append('fresh_failures.jsonl',{'stage':'runner','safe_error_class':type(e).__name__,'task_id':t['id'],'repeat':r['repeat'],'arm':a});return
            finally:queue.task_done()
    await asyncio.gather(*(worker() for _ in range(8)))
    after=guard(setup['guard_ledger'],pro['cohort_match_ids']);assert after==seal
    write('fresh_guard.json',{'before':seal,'after':after,'identical':True,'actual_match_ids':pro['cohort_match_ids'],'stopped_on_failure':stopped.is_set(),'unscheduled_requests':queue.qsize()})
    print(json.dumps({'finished':True,'stopped_on_failure':stopped.is_set(),'unscheduled_requests':queue.qsize(),'spending':{k:v for k,v in spending.read().items() if k!='entries'}}),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--live',action='store_true');args=parser.parse_args()
    asyncio.run(live()) if args.live else prepare()
