"""Predeclared matched position-prompt study; no execution or training."""
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re

from app.trade_env.tasks import read_tasks
from app.trade_env.parse import parse, last_object
from app.trade_env.forecast import predictions
from experiments.forecast_only.core import digest, guard
from experiments.eight_directions.budget import Spending

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'artifacts/eight-directions/d4_position_bias'
SOURCE = ROOT / 'inputs/earlier-run-risk/tasks.jsonl.gz'
ARMS = ['flat', 'home', 'draw', 'away']
MAX_TOKENS = 256

def write(name, value):
    (OUT / name).write_text(json.dumps(value, indent=2, allow_nan=False)+'\n')

def append(name, value):
    with (OUT/name).open('a') as f:
        f.write(json.dumps(value, allow_nan=False)+'\n'); f.flush(); os.fsync(f.fileno())

def load():
    setup = json.loads((ROOT/'research/eight-directions/setup.json').read_text())
    partition = setup['chronological_replay_partition']
    ids = sorted(partition['assessment_match_ids'], key=lambda mid: (partition['first_observed_t_by_match'][mid],mid))[:12]
    tasks = [t for t in read_tasks(SOURCE) if t['match_id'] in ids]
    assert len({t['match_id'] for t in tasks}) == 12
    assert set(ids) <= set(setup['allowed_match_ids'])
    assert all(t['split']=='val' and not t['demo'] for t in tasks)
    return setup, ids, tasks

def canonical(messages):
    assert [m['role'] for m in messages] == ['system','user']
    content=messages[1]['content']
    assert len(re.findall(r'^Position:.*$',content,re.M))==1
    assert len(re.findall(r'^Legal actions:.*$',content,re.M))==1
    content=re.sub(r'^(Selling .* now returns about .*|Position:.*|Legal actions:.*)$','',content,flags=re.M)
    return [messages[0], {'role':'user','content':'\n'.join(l for l in content.splitlines() if l.strip())}]

def prepare():
    from tinker_cookbook import renderers
    from tinker_cookbook.tokenizer_utils import get_tokenizer
    OUT.mkdir(parents=True,exist_ok=True)
    if (OUT/'protocol.json').exists(): raise RuntimeError('Frozen protocol already exists')
    setup, ids, tasks=load(); seal=guard(setup['guard_ledger'],ids)
    partition=setup['chronological_replay_partition']
    config=json.loads((ROOT/'inputs/earlier-run-risk/comparison.json').read_text())['config']
    assert (config['event_delay_s'],config['fill_delay_s'],config['price_max_age_s'])==(60,60,90)
    renderer=renderers.get_renderer('qwen3_disable_thinking',get_tokenizer('Qwen/Qwen3-8B'))
    grouped={}
    for t in tasks: grouped.setdefault((t['match_id'],t['t'],t['decision']),{})[t['position']]=t
    receipts=[]; invariants=[]; total_tokens=0
    for key,variants in sorted(grouped.items(),key=lambda kv:(ids.index(kv[0][0]),kv[0][1])):
        assert set(variants)==set(ARMS)
        assert len({digest(canonical(t['messages'])) for t in variants.values()})==1
        assert len({digest({k:t['scoring'][k] for k in ['shown_prices','market_probs','observed_score']}) for t in variants.values()})==1
        assert len({t['messages'][0]['content'] for t in variants.values()})==1
        invariants.append({'match_id':key[0],'t':key[1],'decision':key[2],'common_observation_sha256':digest(canonical(variants['flat']['messages']))})
        for repeat in range(2):
            seed=int(digest([20261013,4,repeat,key[0],key[1]])[:8],16)%2147483647
            # Rotate position order by state and repeat, preventing fixed service order.
            offset=(len(invariants)+repeat)%4
            for arm in ARMS[offset:]+ARMS[:offset]:
                t=variants[arm]; prompt=renderer.build_generation_prompt(t['messages']); total_tokens+=prompt.length
                receipts.append({'request_id':f'{t["id"]}:seed{repeat}','task_id':t['id'],'match_id':t['match_id'],'observed_t':t['t'],'decision':t['decision'],'arm':arm,'repeat':repeat,'seed':seed,'input_tokens':prompt.length,'max_output_tokens':MAX_TOKENS,'messages':t['messages'],'observation_sha256':digest(t['messages']),'common_observation_sha256':invariants[-1]['common_observation_sha256']})
    maximum=(total_tokens*.195+len(receipts)*MAX_TOKENS*.60)/1e6
    assert 2*maximum<=4.5
    protocol={'direction':4,'hypothesis':'Holding a contract may raise its forecast probability relative to flat at identical observed match state.','intervention':'Existing flat/home/draw/away position bundle, including associated legal actions and selling proceeds. System role/instruction is identical. No portfolio-free arm, no policy execution.','checkpoint':json.loads((ROOT/'research/eight-directions/checkpoint-verification.json').read_text())['checkpoint'],'source_sha256':hashlib.sha256(SOURCE.read_bytes()).hexdigest(),'cohort_match_ids':ids,'partition':partition,'fit_selection':'None; no parameters fitted. First 12 chronological later-assessment development matches selected by timing only.','prior_use':'All historical validation games repeatedly inspected development data. Existing reports/code inspected for provenance/template; no prior position effect used for selection.','arms':ARMS,'repeats':2,'seed_rule':'SHA256([20261013,4,repeat,match_id,observed_t]) first 8 hex mod 2147483647; identical per four-arm set','decision_states':len(grouped),'maximum_requests':len(receipts),'maximum_input_tokens':total_tokens,'maximum_output_tokens':len(receipts)*MAX_TOKENS,'maximum_token_cost_usd':maximum,'maximum_reservation_usd':2*maximum,'budget':{'hard_limit_usd':5,'unallocated_headroom_usd':.5,'reservation_multiplier':2,'rates_per_million':{'prefill':.195,'sample':.60},'billing':'Token estimates, invoice unavailable'},'sampling':{'temperature':1,'max_tokens':MAX_TOKENS,'renderer':'qwen3_disable_thinking','automatic_retries':False},'primary_metric':'Equal-match mean of held-side p(position=side)-p(flat), averaging states, seeds and sides within match, complete valid four-arm sets only.','secondary_metrics':['side-specific paired shifts','absolute vector probability changes','matched Brier/log loss vs flat','market baseline','validity and forecast-only-valid sensitivity','fixed five-bin classwise calibration ECE and signed residuals','action frequencies descriptive only; legal menu differences are expected'], 'validity':'Complete exact five-field JSON schema with legal action, valid probs, valid next_goal/future_prices, and string reason; probs-only sensitivity uses parser-valid probs even when another field fails. Truncation invalidates all.','bootstrap':{'unit':'whole match','replicates':4000,'seed':202610134,'interval':'percentile 95%; matched sets kept together; shared-team dependence unmodeled'},'stopping_rule':'Run all frozen requests once. Any failed request stops subsequent scheduling; retain pending reservations; no retries, restart, threshold search, training or checkpoint selection.','classification_rule':'Promising position sensitivity only if primary 95% interval excludes zero positively; negative if upper bound <=0; otherwise inconclusive. Forecast shifts alone do not establish welfare loss or psychological mechanism.','config':config,'seal_before':seal}
    protocol['sha256']=digest(protocol)
    write('protocol.json',protocol);write('prompt_validation.json',{'all_sets_identical_observed_match_information':True,'sets':invariants,'roles':['system','user'],'allowed_differences':['Selling current contract proceeds','Position line','Legal actions line']})
    with (OUT/'prepared_requests.jsonl').open('x') as f:
        for row in receipts:f.write(json.dumps(row,allow_nan=False)+'\n')
    write('spending.json',{'hard_limit_usd':5,'reserved_max_usd':0.,'token_cost_estimate_usd':0.,'paid_requests':0,'verified_invoice_billing_usd':None,'entries':[]})
    write('guards.json',{'before':seal,'after_offline':guard(setup['guard_ledger'],ids),'actual_match_ids':ids})
    print(json.dumps({k:protocol[k] for k in ['decision_states','maximum_requests','maximum_input_tokens','maximum_output_tokens','maximum_token_cost_usd','maximum_reservation_usd','sha256']}),flush=True)

def parse_response(text,truncated,task,config):
    if truncated:return {'valid':False,'probability_valid':False,'probs':None,'action':None,'parse_error':'truncated'}
    parsed=parse(text,task['legal_actions'],config['prob_sum_tol']); obj=last_object(text)
    schema=isinstance(obj,dict) and set(obj)=={'probs','next_goal','future_prices','action','reason'} and isinstance(obj.get('reason'),str)
    valid=bool(parsed['ok'] and schema and predictions(text,config['prob_sum_tol']) is not None)
    return {'valid':valid,'probability_valid':parsed['probs'] is not None,'probs':parsed['probs'],'action':parsed['action'],'parse_error':None if valid else parsed['error'] or 'schema_or_auxiliary_forecast'}

async def live():
    import tinker
    from tinker.lib.retry_handler import RetryConfig
    from tinker_cookbook import renderers
    from tinker_cookbook.tokenizer_utils import get_tokenizer
    from app.trade_env.train_tinker import read_answer
    setup,ids,tasks=load();tm={t['id']:t for t in tasks}
    p=json.loads((OUT/'protocol.json').read_text()); rows=[json.loads(l) for l in (OUT/'prepared_requests.jsonl').read_text().splitlines()]
    assert digest({k:v for k,v in p.items() if k!='sha256'})==p['sha256']
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest()==p['source_sha256']
    for filename, checksum in p['code_sha256'].items():
        assert hashlib.sha256((ROOT/filename).read_bytes()).hexdigest()==checksum
    assert len(rows)==p['maximum_requests']
    for row in rows:
        assert digest(row['messages'])==row['observation_sha256']
        assert row['messages']==tm[row['task_id']]['messages']
    assert guard(setup['guard_ledger'],ids)==p['seal_before']
    if (OUT/'STARTED.json').exists():raise RuntimeError('Paid start exists; no automatic restart')
    write('STARTED.json',{'utc':datetime.now(timezone.utc).isoformat(),'protocol_sha256':p['sha256']})
    renderer=renderers.get_renderer('qwen3_disable_thinking',get_tokenizer('Qwen/Qwen3-8B'))
    try:
        service=tinker.ServiceClient(user_metadata={'study':'soccer-eight-directions-20261013','direction':'d4'})
        client=service.create_sampling_client(model_path=p['checkpoint'],retry_config=RetryConfig(enable_retry_logic=False))
        spending=Spending(OUT/'spending.json')
        # At most four concurrent requests; first failure stops all later batches.
        async def one(row):
            prompt=renderer.build_generation_prompt(row['messages']);assert prompt.length==row['input_tokens']
            cost=2*(prompt.length*.195+MAX_TOKENS*.60)/1e6
            spending.reserve(row['request_id'],cost,{'input_tokens':prompt.length,'maximum_output_tokens':MAX_TOKENS,'reservation_multiplier':2})
            receipt={**row,'protocol_sha256':p['sha256'],'reserved_maximum_usd':cost,'requested_utc':datetime.now(timezone.utc).isoformat()}
            append('requests.jsonl',receipt)
            try:
                response=await client.sample_async(prompt=prompt,num_samples=1,sampling_params=tinker.SamplingParams(max_tokens=MAX_TOKENS,temperature=1.,stop=renderer.get_stop_sequences(),seed=row['seed']))
                seq=response.sequences[0];text,truncated=read_answer(renderer,seq.tokens)
                parsed=parse_response(text,truncated,tm[row['task_id']],p['config'])
                result={**receipt,**parsed,'completion':text,'raw_tokens':seq.tokens,'truncated':truncated,'output_tokens':len(seq.tokens),'completed_utc':datetime.now(timezone.utc).isoformat()}
                append('predictions.jsonl',result)
                usage={'input_tokens':prompt.length,'output_tokens':len(seq.tokens)}
                spending.complete(row['request_id'],(usage['input_tokens']*.195+usage['output_tokens']*.60)/1e6,usage)
                return True
            except Exception as e:
                append('failures.jsonl',{'request_id':row['request_id'],'safe_error_class':type(e).__name__,'note':'Reservation retained; no retry or restart'})
                return False
        for start in range(0,len(rows),4):
            outcomes=await asyncio.gather(*(one(row) for row in rows[start:start+4]))
            print(json.dumps({'completed_batch_end':start+4,'total':len(rows),'ok':all(outcomes)}),flush=True)
            if not all(outcomes):break
    except Exception as e:
        write('blocked.json',{'safe_error_class':type(e).__name__,'note':'No credentials read; no retry or restart'})
    finally:
        after=guard(setup['guard_ledger'],ids);assert after==p['seal_before']
        write('guards.json',{'before':p['seal_before'],'after':after,'identical':True,'actual_match_ids':ids})

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--live',action='store_true');args=parser.parse_args()
    asyncio.run(live()) if args.live else prepare()
