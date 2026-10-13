"""Frozen-forecast counterfactual for the earlier 72-game, three-repeat run."""
import hashlib
import json
from pathlib import Path
import statistics

import numpy as np
from app.trade_env.dataset import load_release
from app.trade_env.tasks import build_tasks, read_tasks, write_tasks
from app.trade_env.replay import by_match, Replay
from experiments.forecast_only.core import guard
from experiments.forecast_only.risk_policy import RiskPolicy, replay_match

SOURCE = Path('/Users/alexkan/Desktop/frontiers/training-results/run-3-results/forecast-rules-run-3')
OUT = Path('artifacts/earlier-run-risk')
LEDGER = '/Users/alexkan/Desktop/frontiers/rin-gym-simulation/data/evals/ledger.sqlite3'


def stats(rows):
    wins = [r['pnl'] for r in rows if r['pnl'] > 1e-10]
    losses = [r['pnl'] for r in rows if r['pnl'] < -1e-10]
    traded = sum(r['traded'] for r in rows)
    return {'replay_cases': len(rows), 'unique_matches':len({r['match_id'] for r in rows}),
            'traded_cases':traded,'winning_cases':len(wins),'losing_cases':len(losses),
            'win_rate_traded':len(wins)/traded if traded else None,
            'win_rate_all_cases':len(wins)/len(rows),
            'total_pnl':sum(r['pnl'] for r in rows),'gross_wins':sum(wins),'gross_losses':sum(losses),
            'average_win':statistics.mean(wins) if wins else None,
            'average_loss':statistics.mean(losses) if losses else None,
            'worst_case_pnl':min(r['pnl'] for r in rows),
            'mean_pnl_per_case':statistics.mean(r['pnl'] for r in rows)}


def main():
    OUT.mkdir(exist_ok=True)
    source=SOURCE/'repeated-validation/matches.jsonl'
    saved=[json.loads(x) for x in source.read_text().splitlines()]
    assert len(saved)==216 and all(r['split']=='val' and not r['demo'] for r in saved)
    mids={r['match_id'] for r in saved};assert len(mids)==72
    assert len({(r['match_id'],r['repeat']) for r in saved})==216
    seal=guard(LEDGER,mids)
    config=json.loads((SOURCE/'settings.json').read_text())['env_config']
    cache=OUT/'tasks.jsonl.gz'
    if not cache.exists():
        records=[r for r in load_release() if r['match_id'] in mids]
        assert len(records)==72
        tasks,_=build_tasks(records,config)
        assert all(t['split']=='val' and not t['demo'] for t in tasks)
        write_tasks(cache,tasks)
    tasks=read_tasks(cache); grouped=by_match(tasks)
    assert set(grouped)==mids
    policies={'forecast_rule_original_size':RiskPolicy(1,.02,1),
              'forecast_rule_quarter_size':RiskPolicy(.25,.02,1),
              'stricter_entries_original_size':RiskPolicy(1,.05,.65),
              'conservative':RiskPolicy()}
    results={name:[] for name in ['recorded_agent','recorded_agent_quarter_size',*policies]}
    for case in saved:
        match=grouped[case['match_id']]; replay=Replay(match,config)
        probs={}
        for step in case['steps']:
            task=replay.next_task();assert task['id']==step['task_id']
            replay.step(step['action'])
            assert abs(replay.steps[-1]['pnl']-step['pnl'])<1e-9
            # Reuse the original normalized probabilities and validity semantics.
            probs[f"{case['match_id']}:{step['decision']}:flat"]=step['probs'] if step['valid'] else None
        original=replay.result();assert abs(original['pnl']-case['pnl'])<1e-9
        base={'match_id':case['match_id'],'repeat':case['repeat'],'traded':case['trades']>0,'pnl':case['pnl']}
        results['recorded_agent'].append(base)
        results['recorded_agent_quarter_size'].append({**base,'pnl':base['pnl']*.25})
        for name,policy in policies.items():
            row=replay_match(match,probs,config,policy);row['repeat']=case['repeat'];results[name].append(row)
    # Repeat trajectories are not independent games: average repeats, resample games.
    rng=np.random.default_rng(20261013);ids=sorted(mids);idx=rng.integers(0,len(ids),(4000,len(ids)))
    pairs={}
    for baseline in ['recorded_agent','recorded_agent_quarter_size','forecast_rule_quarter_size']:
        d=np.array([statistics.mean(r['pnl'] for r in results['conservative'] if r['match_id']==mid)-statistics.mean(r['pnl'] for r in results[baseline] if r['match_id']==mid) for mid in ids])
        pairs[baseline]={'mean_delta_per_match_repeat':float(d.mean()),'match_cluster_bootstrap_95':np.quantile(d[idx].mean(1),[.025,.975]).tolist()}
    assert guard(LEDGER,mids)==seal
    report={'scope':'Exploratory frozen-forecast replay of earlier final run; 72 development matches, 3 repeats. Not a new closed-loop model evaluation.',
       'limitation':'Saved forecasts were conditioned on original positions. Alternative policies change positions, so these are counterfactual executions of a frozen signal tape, not fresh model responses on altered trajectories.',
       'cost_usd':0,'holdout_before_after':seal,'saved_source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
       'task_sha256':hashlib.sha256(cache.read_bytes()).hexdigest(),'config':config,
       'policies':{name:{'config':vars(policies[name]) if name in policies else None,'summary':stats(rows)} for name,rows in results.items()},
       'conservative_paired_deltas':pairs}
    (OUT/'comparison.json').write_text(json.dumps(report,indent=2)+'\n')
    (OUT/'matches.json').write_text(json.dumps(results,indent=2)+'\n')
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
