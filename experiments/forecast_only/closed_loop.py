"""Fresh, paired, position-conditioned Tinker replay; no training or holdout use."""
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics

import numpy as np
import tinker
from tinker_cookbook import renderers
from tinker_cookbook.tokenizer_utils import get_tokenizer
from app.trade_env.tasks import read_tasks
from app.trade_env.replay import by_match, Replay
from app.trade_env.reward import score
from app.trade_env.costs import buy_cost
from app.trade_env.train_tinker import read_answer
from experiments.forecast_only.core import guard, digest
from experiments.forecast_only.risk_policy import RiskPolicy, choose_entry
from experiments.forecast_only.earlier_run_risk import stats

SOURCE = Path('artifacts/earlier-run-risk')
OUT = Path('artifacts/closed-loop-risk')
CHECKPOINT = 'tinker://d2426bc1-ec71-559d-bb81-e17da9372e58:train:0/sampler_weights/final'
LEDGER = '/Users/alexkan/Desktop/frontiers/rin-gym-simulation/data/evals/ledger.sqlite3'
MAX_TOKENS = 256
RATES = {'prefill':0.195, 'sample':0.60}
POLICY = RiskPolicy()


def append(path, value):
    with Path(path).open('a') as f:
        f.write(json.dumps(value, allow_nan=False)+'\n'); f.flush()


def observation(task, arm):
    messages = [dict(m) for m in task['messages']]
    if arm == 'original':
        return messages
    if arm != 'conservative':
        raise ValueError('Unknown arm')
    messages[0]['content'] = messages[0]['content'].replace(
        'You hold either nothing or 1 share of one contract.',
        'You hold either nothing or 0.25 shares of one contract.')
    messages[0]['content'] += ('\nExecution policy: the external executor uses your probs to select a contract only when '
        'its predicted probability exceeds displayed all-in cost per share by more than 0.05. '
        'It buys 0.25 shares, with a maximum all-in fill cost of 0.65 per share. '
        'Once purchased, the position is held to settlement; no closes, switches or averaging down. '
        'Your action field is advisory; execution is determined by this rule. '
        'All displayed quotes, buying costs and selling proceeds are per share. '
        'Continue returning the complete five-field JSON response.')
    messages[1]['content'] = messages[1]['content'].replace('Position: 1 share of ', 'Position: 0.25 shares of ')
    if task['position'] != 'flat':
        messages[1]['content'] = messages[1]['content'].replace(
            f"Selling {task['position']} now returns about", f"Selling 1 share of {task['position']} now returns about")
    return messages


def execute(replay, text, arm, config):
    task = replay.next_task()
    parsed = score(task, text, config)
    reason = None
    if arm == 'original':
        selected = parsed['action']
    elif replay.position != 'flat':
        selected = 'keep'
    else:
        pricing = {**config, 'market_fee_rates':task['scoring'].get('fee_rates',{})}
        side = choose_entry(parsed['probs'] if parsed['valid'] else None,
                            task['scoring']['shown_prices'], pricing, POLICY)
        selected = 'do_nothing'
        if side:
            # Execution, after the order decision: enforce the limit at delayed fill.
            fill = buy_cost(task['scoring']['fill_prices'][side], pricing, side)
            if fill <= POLICY.maximum_cost:
                selected = 'buy_'+side
            else:
                reason = 'delayed_fill_above_limit'
    replay.step(selected)
    return {'valid':parsed['valid'], 'parse_error':parsed['error'], 'probs':parsed['probs'],
            'advisory_action':parsed['action'], 'executed_action':selected,
            'execution_rejection':reason, 'next_position':replay.position}


class Budget:
    def __init__(self, maximum_calls, ceiling=2.5):
        self.maximum_calls, self.ceiling = maximum_calls, ceiling
        self.calls, self.reserved = 0, 0.0
    def reserve(self, prompt_tokens):
        cost = (prompt_tokens*RATES['prefill'] + MAX_TOKENS*RATES['sample'])/1e6
        if self.calls >= self.maximum_calls or self.reserved+cost > self.ceiling:
            raise ValueError('Request budget exhausted')
        self.calls += 1; self.reserved += cost
        return cost


async def play(client, renderer, tasks, repeat, arm, config, budget, protocol_hash):
    replay = Replay(tasks,config)
    while (task:=replay.next_task()) is not None:
        position = replay.position
        messages = observation(task,arm)
        prompt = renderer.build_generation_prompt(messages)
        seed = int(digest([20261013,repeat,task['match_id'],task['decision']])[:8],16) % 2147483647
        budget.reserve(prompt.length)
        receipt = {'arm':arm,'repeat':repeat,'task_id':task['id'],'position_before':position,
            'quantity_before':(0 if position=='flat' else .25 if arm=='conservative' else 1),
            'observation_sha256':digest(messages),'seed':seed,'input_tokens':prompt.length,
            'protocol_sha256':protocol_hash,'requested_utc':datetime.now(timezone.utc).isoformat(),
            'kind':'retrospective_development_closed_loop'}
        append(OUT/'requests.jsonl',receipt)
        response = await client.sample_async(prompt=prompt,num_samples=1,
            sampling_params=tinker.SamplingParams(max_tokens=MAX_TOKENS,temperature=1.0,
                stop=renderer.get_stop_sequences(),seed=seed))
        seq=response.sequences[0];text,truncated=read_answer(renderer,seq.tokens)
        result=execute(replay,None if truncated else text,arm,config)
        append(OUT/'predictions.jsonl',{**receipt,**result,'completion':text,'truncated':truncated,
            'output_tokens':len(seq.tokens),'completed_utc':datetime.now(timezone.utc).isoformat()})
    result=replay.result();scale=.25 if arm=='conservative' else 1
    row={'arm':arm,'repeat':repeat,'match_id':result['match_id'],'traded':result['trades']>0,
         'pnl':result['pnl']*scale,'cost':result['cost']*scale,'trades':result['trades'],'steps':result['steps'],
         'note':'Step P&L/cost are per nominal share; aggregate P&L/cost apply arm quantity.'}
    if arm=='conservative' and row['pnl'] < -.25*.65-1e-9:
        raise ValueError('Risk bound violated')
    append(OUT/'matches.jsonl',row)
    return row


def report_results(rows, seal):
    arms={arm:sorted([r for r in rows if r['arm']==arm],key=lambda r:(r['match_id'],r['repeat'])) for arm in ['original','conservative']}
    arms['original_quarter_size_arithmetic']=[{**r,'pnl':r['pnl']*.25} for r in arms['original']]
    ids=sorted({r['match_id'] for r in rows});rng=np.random.default_rng(20261013)
    indices=rng.integers(0,len(ids),(4000,len(ids)))
    paired={}
    for baseline in ['original','original_quarter_size_arithmetic']:
        d=np.array([statistics.mean(r['pnl'] for r in arms['conservative'] if r['match_id']==mid)-statistics.mean(r['pnl'] for r in arms[baseline] if r['match_id']==mid) for mid in ids])
        paired[baseline]={'mean_pnl_delta_per_game_repeat':float(d.mean()),'match_cluster_bootstrap_95':np.quantile(d[indices].mean(1),[.025,.975]).tolist()}
    predictions=[json.loads(x) for x in (OUT/'predictions.jsonl').read_text().splitlines()]
    usage={'input_tokens':sum(r['input_tokens'] for r in predictions),'output_tokens':sum(r['output_tokens'] for r in predictions),'requests':len(predictions)}
    usage['full_rate_cost_estimate_usd']=(usage['input_tokens']*RATES['prefill']+usage['output_tokens']*RATES['sample'])/1e6
    usage['note']='Logged-token estimate, not provider invoice; excludes cache discounts.'
    return {'summaries':{k:stats(v) for k,v in arms.items()},'paired':paired,'usage':usage,
            'validity':{arm:statistics.mean(r['valid'] for r in predictions if r['arm']==arm) for arm in ['original','conservative']},
            'seal_after':seal,'scope':'Fresh position-conditioned replay of historical development games; original sparse schedule, not minute-by-minute. No training or holdout evaluation. Quarter-size original is arithmetic, not a third inference arm.'}


async def main(live):
    tasks=read_tasks(SOURCE/'tasks.jsonl.gz');assert all(t['split']=='val' and not t['demo'] for t in tasks)
    grouped=by_match(tasks);assert len(grouped)==72
    config=json.loads((SOURCE/'comparison.json').read_text())['config']
    seal=guard(LEDGER,grouped)
    renderer=renderers.get_renderer('qwen3_disable_thinking',get_tokenizer('Qwen/Qwen3-8B'))
    prompt_total=0;calls=0
    for match in grouped.values():
        for time in sorted({t['t'] for t in match}):
            for arm in ['original','conservative']:
                prompt_total+=3*max(renderer.build_generation_prompt(observation(t,arm)).length for t in match if t['t']==time)
                calls+=3
    estimate=(prompt_total*RATES['prefill']+calls*MAX_TOKENS*RATES['sample'])/1e6
    assert estimate < 2.5, 'Preflight exceeds half of authorized $5; stop before requests'
    protocol={'checkpoint':CHECKPOINT,'match_ids':sorted(grouped),'repeats':3,'arms':['original','conservative'],
       'conservative':vars(POLICY),'config':config,'temperature':1,'seed':20261013,'max_tokens':MAX_TOKENS,
       'maximum_requests':calls,'upper_token_cost_estimate_usd':estimate,'request_budget_ceiling_usd':2.5,'authorized_usd':5,
       'rates_per_million':RATES,'pricing_source':'https://tinker-docs.thinkingmachines.ai/tinker/models/models_and_pricing/',
       'seal':seal,'tasks_sha256':hashlib.sha256((SOURCE/'tasks.jsonl.gz').read_bytes()).hexdigest(),
       'code_sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path(__file__),Path('experiments/forecast_only/risk_policy.py')]},
       'design':'Fresh original prompts vs policy-aware conservative prompts; current position/quantity reflect each arm execution. No conversational history, matching original task design. Identical seeds per matched decision; no retries or checkpoint selection.'}
    protocol['sha256']=digest(protocol)
    print(json.dumps({'maximum_requests':calls,'upper_cost_estimate_usd':estimate,'seal':seal}),flush=True)
    if not live:return
    OUT.mkdir(exist_ok=True)
    with (OUT/'protocol.json').open('x') as f:json.dump(protocol,f,indent=2)
    service=tinker.ServiceClient();client=service.create_sampling_client(model_path=CHECKPOINT)
    budget=Budget(calls);semaphore=asyncio.Semaphore(16)
    async def bounded(match,repeat,arm):
        async with semaphore:
            guard(LEDGER,[match[0]['match_id']])
            return await play(client,renderer,match,repeat,arm,config,budget,protocol['sha256'])
    rows=await asyncio.gather(*(bounded(grouped[mid],repeat,arm) for repeat in range(3) for mid in sorted(grouped) for arm in ['original','conservative']))
    after=guard(LEDGER,grouped);assert after==seal
    assert len(rows)==432 and budget.calls==calls
    report=report_results(rows,after);(OUT/'summary.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--live',action='store_true');args=p.parse_args();asyncio.run(main(args.live))
