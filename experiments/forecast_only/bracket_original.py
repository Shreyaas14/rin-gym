"""Fresh original entries with checkpoint take-profit and stop-loss exits."""
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
from app.trade_env.train_tinker import read_answer
from experiments.forecast_only.core import guard, digest
from experiments.forecast_only.closed_loop import CHECKPOINT, LEDGER, MAX_TOKENS, RATES, Budget, append, execute, SOURCE
from experiments.forecast_only.earlier_run_risk import stats

OUT = Path('artifacts/bracket-original-live')
BASELINE = Path('artifacts/closed-loop-risk')
QUANTITY = 1.0


def observation(task):
    # Keep original instructions and entry selection unchanged. Actual executed
    # position flows into the next prompt through Replay.next_task().
    return [dict(m) for m in task['messages']]


def execute_bracket(replay, text, config, entry_cost):
    from app.trade_env.reward import score
    from app.trade_env.costs import buy_cost, sell_proceeds
    task = replay.next_task()
    parsed = score(task, text, config)
    selected = parsed['action']
    trigger, liquidation, gain = None, None, None
    if replay.position != 'flat':
        assert entry_cost is not None
        liquidation = sell_proceeds(task['scoring']['shown_prices'][replay.position], config, replay.position)
        gain = liquidation - entry_cost
        if gain >= .10 - 1e-12:
            trigger = 'take_profit'
        elif gain <= -.25 + 1e-12:
            trigger = 'stop_loss'
        if trigger:
            selected = 'close'
    else:
        assert entry_cost is None
    before = entry_cost
    if selected.startswith(('buy_', 'switch_')):
        side = selected.split('_', 1)[1]
        entry_cost = buy_cost(task['scoring']['fill_prices'][side], config, side)
    elif selected == 'close':
        entry_cost = None
    replay.step(selected)
    return {'valid': parsed['valid'], 'parse_error': parsed['error'], 'probs': parsed['probs'],
        'advisory_action': parsed['action'], 'executed_action': selected,
        'execution_rejection': trigger, 'next_position': replay.position,
        'entry_cost_before': before, 'entry_cost_after': entry_cost,
        'shown_net_liquidation': liquidation, 'shown_gain_per_share': gain}, entry_cost


async def play(client, renderer, tasks, repeat, config, budget, protocol_hash):
    replay = Replay(tasks,config)
    entry_cost = None
    while (task := replay.next_task()) is not None:
        position = replay.position
        messages = observation(task)
        prompt = renderer.build_generation_prompt(messages)
        seed = int(digest([20261013,repeat,task['match_id'],task['decision']])[:8],16) % 2147483647
        budget.reserve(prompt.length)
        receipt = {'arm':'original_bracket_fresh','repeat':repeat,'task_id':task['id'],
            'position_before':position,'quantity_before':0 if position=='flat' else QUANTITY,
            'observation_sha256':digest(messages),'seed':seed,'input_tokens':prompt.length,
            'protocol_sha256':protocol_hash,'requested_utc':datetime.now(timezone.utc).isoformat(),
            'kind':'retrospective_development_closed_loop'}
        append(OUT/'requests.jsonl',receipt)
        response = await client.sample_async(prompt=prompt,num_samples=1,
            sampling_params=tinker.SamplingParams(max_tokens=MAX_TOKENS,temperature=1,
                stop=renderer.get_stop_sequences(),seed=seed))
        seq=response.sequences[0];text,truncated=read_answer(renderer,seq.tokens)
        # Bracket exits take precedence; subsequent model responses see actual positions.
        result,entry_cost=execute_bracket(replay,None if truncated else text,config,entry_cost)
        append(OUT/'predictions.jsonl',{**receipt,**result,'completion':text,'truncated':truncated,
            'output_tokens':len(seq.tokens),'completed_utc':datetime.now(timezone.utc).isoformat()})
    result=replay.result()
    row={'arm':'original_bracket_fresh','repeat':repeat,'match_id':result['match_id'],
        'traded':result['trades']>0,'pnl':result['pnl']*QUANTITY,'cost':result['cost']*QUANTITY,
        'trades':result['trades'],'steps':result['steps'],
        'note':'Step P&L/cost are per nominal share; aggregate P&L/cost apply quantity 1.0.'}
    append(OUT/'matches.jsonl',row)
    return row


def summarize(rows, baseline, seal):
    arms={'original_one_share_previous_fresh':baseline,
          'original_bracket_fresh':rows}
    ids=sorted({r['match_id'] for r in rows});rng=np.random.default_rng(20261013)
    indices=rng.integers(0,len(ids),(4000,len(ids)))
    means={k:np.array([statistics.mean(r['pnl'] for r in v if r['match_id']==mid) for mid in ids]) for k,v in arms.items()}
    comparison={}
    for key in ['original_one_share_previous_fresh']:
        d=means['original_bracket_fresh']-means[key]
        comparison[key]={'mean_pnl_delta_per_game_repeat':float(d.mean()),
             'match_cluster_bootstrap_95':np.quantile(d[indices].mean(1),[.025,.975]).tolist()}
    # Ratio bootstrap preserves clustered traded/winning counts for win-rate comparison.
    counts={}
    for key in ['original_one_share_previous_fresh','original_bracket_fresh']:
        counts[key]=np.array([[sum(r['pnl']>1e-10 for r in arms[key] if r['match_id']==mid),sum(r['traded'] for r in arms[key] if r['match_id']==mid)] for mid in ids])
    boot={k:a[indices].sum(1) for k,a in counts.items()}
    win_delta=boot['original_bracket_fresh'][:,0]/boot['original_bracket_fresh'][:,1]-boot['original_one_share_previous_fresh'][:,0]/boot['original_one_share_previous_fresh'][:,1]
    predictions=[json.loads(x) for x in (OUT/'predictions.jsonl').read_text().splitlines()]
    before={(r['repeat'],r['task_id'].rsplit(':',1)[0]):r for r in [json.loads(x) for x in (BASELINE/'predictions.jsonl').read_text().splitlines()] if r['arm']=='original'}
    changed=sum(r['executed_action']!=before[(r['repeat'],r['task_id'].rsplit(':',1)[0])]['executed_action'] for r in predictions)
    total_in=sum(r['input_tokens'] for r in predictions);total_out=sum(r['output_tokens'] for r in predictions)
    summaries={k:stats(v) for k,v in arms.items()}
    return {'summaries':summaries,'paired_pnl':comparison,
      'fresh_bracket_mean_pnl_bootstrap_95':np.quantile(means['original_bracket_fresh'][indices].mean(1),[.025,.975]).tolist(),
      'win_rate_difference':{'fresh_bracket_minus_original':summaries['original_bracket_fresh']['win_rate_traded']-summaries['original_one_share_previous_fresh']['win_rate_traded'],
           'match_cluster_bootstrap_95':np.quantile(win_delta,[.025,.975]).tolist()},
      'behavior':{'decisions_with_different_executed_action':changed,'decisions':len(predictions)},
      'valid_rate':statistics.mean(r['valid'] for r in predictions),
      'usage':{'requests':len(predictions),'input_tokens':total_in,'output_tokens':total_out,
               'full_rate_cost_estimate_usd':(total_in*RATES['prefill']+total_out*RATES['sample'])/1e6,
               'note':'Logged-token estimate, not billing invoice; cache discounts excluded.'},
      'seal_after':seal,'scope':'Fresh one-share original-entry inference with external bracket exits. Baseline is the immediately preceding verified one-share fresh replay, with identical checkpoint/games/seeds. No protected holdout use, no training, no threshold tuning.'}


async def main(live):
    source_report=json.loads((SOURCE/'comparison.json').read_text())
    tasks=read_tasks(SOURCE/'tasks.jsonl.gz');assert all(t['split']=='val' and not t['demo'] for t in tasks)
    grouped=by_match(tasks);assert len(grouped)==72
    config=source_report['config'];seal=guard(LEDGER,grouped)
    old=json.loads((BASELINE/'protocol.json').read_text())
    recorded_hashes=json.loads((BASELINE/'SHA256SUMS.json').read_text())
    for name in ['protocol.json','matches.jsonl','predictions.jsonl','verification.json']:
        assert hashlib.sha256((BASELINE/name).read_bytes()).hexdigest()==recorded_hashes[name]
    original_hash=old.pop('sha256');assert digest(old)==original_hash;old['sha256']=original_hash
    assert old['checkpoint']==CHECKPOINT and old['config']==config and old['match_ids']==sorted(grouped)
    assert json.loads((BASELINE/'verification.json').read_text())['passed']
    baseline=[json.loads(x) for x in (BASELINE/'matches.jsonl').read_text().splitlines() if json.loads(x)['arm']=='original']
    assert len(baseline)==216
    renderer=renderers.get_renderer('qwen3_disable_thinking',get_tokenizer('Qwen/Qwen3-8B'))
    prompt_total=0;calls=0
    for match in grouped.values():
        for time in sorted({t['t'] for t in match}):
            prompt_total+=3*max(renderer.build_generation_prompt(observation(t)).length for t in match if t['t']==time);calls+=3
    estimate=(prompt_total*RATES['prefill']+calls*MAX_TOKENS*RATES['sample'])/1e6
    assert estimate < 2.5
    files=[Path(__file__),Path('experiments/forecast_only/closed_loop.py'),Path('app/trade_env/reward.py'),Path('app/trade_env/replay.py')]
    protocol={'checkpoint':CHECKPOINT,'match_ids':sorted(grouped),'repeats':3,'quantity':QUANTITY,'take_profit_per_share':.10,'stop_loss_per_share':.25,
        'config':config,'temperature':1,'seed':20261013,'max_tokens':MAX_TOKENS,
        'maximum_requests':calls,'upper_cost_estimate_usd':estimate,'request_reservation_ceiling_usd':2.5,'authorized_usd':5,
        'rates_per_million':RATES,'seal':seal,'tasks_sha256':hashlib.sha256((SOURCE/'tasks.jsonl.gz').read_bytes()).hexdigest(),
        'baseline_protocol_sha256':old['sha256'],
        'baseline_files_sha256':{str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in [BASELINE/'matches.jsonl',BASELINE/'predictions.jsonl',BASELINE/'verification.json']},
        'code_sha256':{str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in files},
        'design':'Original one-share prompts and entry selection unchanged. At each held-position checkpoint, close when displayed net liquidation minus actual entry cost reaches +0.10 or -0.25 per share. Bracket close overrides any model action including switches; otherwise honor original buys, closes, switches and re-entry. No same-checkpoint re-entry after forced close; later re-entry allowed, resetting basis. Delayed fills, fees and spread unchanged; stop is not guaranteed. Fresh model responses see actual positions. Reuse verified prior one-share baseline with matching games/checkpoint/seeds. No parameter tuning or holdout use.'}
    protocol['sha256']=digest(protocol)
    print(json.dumps({'maximum_requests':calls,'upper_cost_estimate_usd':estimate,'seal':seal}),flush=True)
    if not live:return
    OUT.mkdir(exist_ok=True)
    with (OUT/'protocol.json').open('x') as f:json.dump(protocol,f,indent=2)
    service=tinker.ServiceClient();client=service.create_sampling_client(model_path=CHECKPOINT)
    budget=Budget(calls);semaphore=asyncio.Semaphore(16)
    async def bounded(match,repeat):
        async with semaphore:
            guard(LEDGER,[match[0]['match_id']])
            return await play(client,renderer,match,repeat,config,budget,protocol['sha256'])
    rows=await asyncio.gather(*(bounded(grouped[mid],repeat) for repeat in range(3) for mid in sorted(grouped)))
    after=guard(LEDGER,grouped);assert after==seal and len(rows)==216 and budget.calls==calls
    report=summarize(rows,baseline,after);(OUT/'summary.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--live',action='store_true');a=p.parse_args();asyncio.run(main(a.live))
