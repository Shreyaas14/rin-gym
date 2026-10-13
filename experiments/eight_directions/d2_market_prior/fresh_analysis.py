"""Complete-pair proper scores, full-trajectory frozen P&L and attrition audit."""
import json,math,statistics
from collections import defaultdict
import numpy as np
from experiments.eight_directions.d2_market_prior.study import ROOT,OUT,SIDES,FLOOR,boot_delta,trade_stats,write
from experiments.eight_directions.d2_market_prior.fresh import ARMS,messages
from experiments.forecast_only.core import digest,guard
from experiments.forecast_only.risk_policy import replay_match,RiskPolicy
from app.trade_env.tasks import read_tasks
from app.trade_env.replay import by_match


def run():
    pro=json.loads((OUT/'fresh_protocol.json').read_text());ts=read_tasks(ROOT/'inputs/earlier-run-risk/tasks.jsonl.gz');tm={t['id']:t for t in ts};grouped=by_match(ts)
    plans={p['request_id']:p for p in pro['planned_requests']};predfile=OUT/'fresh_predictions.jsonl'
    rows=[json.loads(l) for l in predfile.read_text().splitlines()] if predfile.exists() else []
    assert len({r['request_id'] for r in rows})==len(rows)
    rm={r['request_id']:r for r in rows};paired=defaultdict(dict);maxshift=[]
    for r in rows:
        plan=plans[r['request_id']];t=tm[r['task_id']]
        for k in ['arm','repeat','task_id','seed','observation_sha256','input_tokens']:assert r[k]==plan[k]
        assert r['observation_sha256']==digest(messages(t,r['arm']))
        assert r['position']==t['position'] and r['observed_t']==t['t']
        paired[(r['task_id'],r['repeat'])][r['arm']]=r
        if r['arm']=='market_prior_prompt' and r['valid']:
            m=t['scoring']['market_probs'];maxshift.append(max(abs(r['probs'][k]-m[k]) for k in SIDES))
    complete={k:v for k,v in paired.items() if set(v)==set(ARMS)}
    for ps in complete.values():assert ps[ARMS[0]]['seed']==ps[ARMS[1]]['seed']
    arms=['market_only',*ARMS];point_scores={a:[] for a in arms}
    for (tid,rep),ps in complete.items():
        t=tm[tid];target=t['scoring']['result']
        for arm in arms:
            p=t['scoring']['market_probs'] if arm=='market_only' else ps[arm]['probs'] if ps[arm]['valid'] else None
            b=sum((p[k]-float(target==k))**2 for k in SIDES) if p is not None else 2.
            ll=-math.log(max(FLOOR,p[target])) if p is not None else -math.log(FLOOR)
            point_scores[arm].append({'match_id':t['match_id'],'repeat':rep,'decision':t['decision'],'task_id':tid,'brier':b,'log_loss':ll,'valid':p is not None,'probs':p})
    ids=sorted({tm[tid]['match_id'] for tid,rep in complete});match_metrics={a:{} for a in arms}
    summary={};paired_metrics={}
    for arm in arms:
        for mid in ids:
            rs=[r for r in point_scores[arm] if r['match_id']==mid]
            match_metrics[arm][mid]={k:statistics.mean(r[k] for r in rs) for k in ['brier','log_loss','valid']}
        summary[arm]={k:statistics.mean(match_metrics[arm][mid][k] for mid in ids) for k in ['brier','log_loss','valid']} if ids else None
    for a,b in [('original_prompt','market_only'),('market_prior_prompt','market_only'),('market_prior_prompt','original_prompt')]:
        paired_metrics[a+'_minus_'+b]={k:boot_delta(match_metrics[a],match_metrics[b],k,ids) for k in ['brier','log_loss']} if ids else None
    # Trading only uses complete paired trajectories; never infer absent forecasts.
    expected=defaultdict(set)
    for p in pro['planned_requests']:expected[(tm[p['task_id']]['match_id'],p['repeat'])].add(p['task_id'])
    fullcases=[]
    for (mid,rep),tids in expected.items():
        if all((tid,rep) in complete for tid in tids):fullcases.append((mid,rep))
    config=json.loads((ROOT/'inputs/closed-loop-risk/protocol.json').read_text())['config'];trading={a:[] for a in arms};tr_summary={};tr_matches={a:{} for a in arms}
    for a in arms:
        for mid,rep in fullcases:
            tape={f"{mid}:{r['decision']}:flat":r['probs'] for r in point_scores[a] if r['match_id']==mid and r['repeat']==rep}
            x=replay_match(grouped[mid],tape,config,RiskPolicy());x['repeat']=rep;trading[a].append(x)
        tr_summary[a]=trade_stats(trading[a]) if fullcases else None
        for mid in sorted({mid for mid,rep in fullcases}):tr_matches[a][mid]={'pnl':statistics.mean(r['pnl'] for r in trading[a] if r['match_id']==mid)}
    tr_ids=sorted(tr_matches['market_only'])
    for a,b in [('original_prompt','market_only'),('market_prior_prompt','market_only'),('market_prior_prompt','original_prompt')]:
        if paired_metrics[a+'_minus_'+b] is not None:paired_metrics[a+'_minus_'+b]['pnl']=boot_delta(tr_matches[a],tr_matches[b],'pnl',tr_ids) if tr_ids else None
    result={'scope':'Fresh paired prompt intervention at frozen original observed states and positions, historical later development games; trading is frozen-signal counterfactual.',
      'forecast':summary,'paired':paired_metrics,'trading':tr_summary,
      'attrition':{'planned_requests':len(plans),'completed_requests':len(rows),'missing_request_ids':sorted(set(plans)-set(rm)),'planned_pairs':len(plans)//2,'complete_pairs':len(complete),'orphan_completed_predictions':len(rows)-2*len(complete),'assessment_match_count':len(ids),'forecast_point_pairs_by_match':{mid:sum(tm[tid]['match_id']==mid for tid,rep in complete) for mid in ids},'expected_game_repeat_cases':99,'full_paired_trajectory_cases':len(fullcases),'full_paired_trajectory_match_count':len(tr_ids),'trading_case_repeats_by_match':{mid:sum(m==mid for m,r in fullcases) for mid in tr_ids},'complete_pair_exclusion_rule':'Only missing arm predictions exclude a pair; invalid completed outputs remain with penalty; P&L requires a full paired trajectory.'},
      'adjustment_cap_audit':{'instruction_only':True,'valid_treatment_predictions':len(maxshift),'violations_over_0_10':sum(x>.100001 for x in maxshift),'violation_rate':statistics.mean(x>.100001 for x in maxshift) if maxshift else None,'mean_largest_adjustment':statistics.mean(maxshift) if maxshift else None,'max_adjustment':max(maxshift) if maxshift else None},
      'spending':{k:v for k,v in json.loads((OUT/'spending.json').read_text()).items() if k!='entries'}}
    setup=json.loads((ROOT/'research/eight-directions/setup.json').read_text());result['guard_after']=guard(setup['guard_ledger'],pro['cohort_match_ids']);assert result['guard_after']==pro['guard_before']
    result['classification']='inconclusive'
    if summary['market_prior_prompt'] is not None:
        ci=paired_metrics['market_prior_prompt_minus_market_only']['brier']['paired_match_bootstrap_95']
        result['classification']='promising' if ci[1]<0 else 'negative' if ci[0]>0 else 'inconclusive'
    write('fresh_results.json',result);write('fresh_match_metrics.json',match_metrics);write('fresh_trading_receipts.json',trading)
    write('fresh_verification.json',{'receipt_prompt_hashes_checked':len(rows),'receipt_seed_identity_checks':len(complete),'exactly_two_paired_arms':True,'guard_unchanged':True,'no_result_based_exclusions':True,'spending_reservation_entries':len(json.loads((OUT/'spending.json').read_text())['entries'])})
    print(json.dumps(result,indent=2))

if __name__=='__main__':run()
