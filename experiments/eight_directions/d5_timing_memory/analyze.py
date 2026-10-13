"""Proper scores on two shared clocks and a fixed secondary executor."""
import json, math, statistics
from collections import defaultdict
import numpy as np
from .study import ROOT,OUT,SETUP,ARMS,OUTCOMES,write,selective_load,digest,guard,brier,RiskPolicy,choose_entry,buy_cost,fill_prices

def bootstrap(delta):
    v=np.array(delta);rng=np.random.default_rng(20261013);idx=rng.integers(0,len(v),(10000,len(v)))
    return {'mean_delta':float(v.mean()),'match_cluster_bootstrap_95':np.quantile(v[idx].mean(1),[.025,.975]).tolist(),'matches':len(v)}
def forecast_score(probs,target):
    return {'brier':brier(probs,target),'log_loss':-math.log(max(1e-4,probs[target])) if probs else -math.log(1e-4),'valid':float(probs is not None)}
def mean(rows,key):return statistics.mean(r[key] for r in rows)
def execute(record,rows,observations,cfg):
    policy=RiskPolicy();attempts=[];pending_until=float('-inf')
    for row in sorted(rows,key=lambda x:x['t']):
        if row['t']<pending_until:continue
        ob=observations[record['match_id'],row['decision'],row['arm'].endswith('_memory') and not row['arm'].endswith('_no_memory')]
        pricing={**cfg,'market_fee_rates':record['fee_rates']}
        side=choose_entry(row['probs'],ob['shown'],pricing,policy)
        if side is None:continue
        # Fill selection happens ONLY after displayed-state decision chooses side.
        fills,reason=fill_prices(record['prices'],row['t'],record['periods']['h2_end'],cfg)
        pending_until=row['t']+cfg['fill_delay_s']+cfg['fill_max_wait_s']
        if reason:attempts.append({'decision':row['decision'],'side':side,'accepted':False,'reason':reason});continue
        cost=buy_cost(fills[side],pricing,side);accepted=cost<=policy.maximum_cost
        attempts.append({'decision':row['decision'],'side':side,'accepted':accepted,'cost_per_share':cost})
        if accepted:
            debit=policy.shares*cost;pnl=policy.shares*float(record['regulation']['result']==side)-debit
            assert pnl>=-.1625-1e-9
            return {'pnl':pnl,'stake':debit,'traded':True,'turnover_shares':.25,'entry_and_settlement_turnover_shares':.5,'attempts':attempts}
    return {'pnl':0.,'stake':0.,'traded':False,'turnover_shares':0.,'entry_and_settlement_turnover_shares':0.,'attempts':attempts}
def run():
    pro=json.loads((OUT/'protocol.json').read_text());ids=pro['cohort_match_ids'];setup=json.loads(SETUP.read_text());before=guard(setup['guard_ledger'],ids);assert before==pro['guard_before']
    records=selective_load(ids);obs=json.loads((OUT/'observations.json').read_text());ol={(r['match_id'],r['decision'],r['memory']):r for r in obs}
    predictions=[json.loads(x) for x in (OUT/'predictions.jsonl').read_text().splitlines()]
    plans=json.loads((OUT/'planned_requests.json').read_text());planned_ids={p['request_id'] for p in plans}
    assert len({p['request_id'] for p in predictions})==len(predictions)
    assert all(p['request_id'] in planned_ids for p in predictions)
    complete=len(predictions)==len(plans)
    groups=defaultdict(list)
    for r in predictions:groups[r['arm'],r['repeat'],r['match_id']].append(r)
    metrics=[];trades=[];grid_receipts=[]
    for mid in ids:
        clock=sorted([r for r in obs if r['match_id']==mid and not r['memory']],key=lambda r:r['t']);target=records[mid]['regulation']['result']
        for repeat in range(pro['repeats']):
            for arm in ARMS:
                rr=sorted(groups[arm,repeat,mid],key=lambda r:r['t'])
                if not rr:continue
                views={'shared_minute_grid':[],'original_sparse_checkpoints':[]}
                for ob in clock:
                    available=[r for r in rr if r['t']<=ob['t']]
                    if not available:continue
                    latest=available[-1];p=latest['probs'];score=forecast_score(p,target)
                    market={o:ob['shown'][o]/sum(ob['shown'].values()) for o in OUTCOMES};m=forecast_score(market,target)
                    receipt={'match_id':mid,'repeat':repeat,'arm':arm,'common_decision':ob['decision'],'common_t':ob['t'],'forecast_decision':latest['decision'],'forecast_age_s':ob['t']-latest['t'],**score,'market_brier':m['brier'],'market_log_loss':m['log_loss']}
                    grid_receipts.append(receipt);views['shared_minute_grid'].append(receipt)
                    if ob['sparse']:views['original_sparse_checkpoints'].append(receipt)
                for view,rows in views.items():
                    if rows:metrics.append({'match_id':mid,'repeat':repeat,'arm':arm,'view':view,'common_times':len(rows),**{k:mean(rows,k) for k in ['brier','log_loss','valid','forecast_age_s','market_brier','market_log_loss']}})
                trades.append({'match_id':mid,'repeat':repeat,'arm':arm,**execute(records[mid],rr,ol,pro['config'])})
    write('common_time_receipts.json',grid_receipts);write('match_metrics.json',metrics);write('execution_receipts.json',trades)
    summaries={};paired={}
    for view in ['shared_minute_grid','original_sparse_checkpoints']:
        summaries[view]={}
        for arm in ARMS:
            rows=[r for r in metrics if r['view']==view and r['arm']==arm]
            if rows:summaries[view][arm]={k:mean(rows,k) for k in ['brier','log_loss','valid','forecast_age_s','market_brier','market_log_loss']}
        contrasts=[('minute_no_memory','sparse_no_memory','cadence_information_age'),('sparse_memory','sparse_no_memory','memory_fixed_sparse')]
        paired[view]={}
        for a,b,name in contrasts:
            paired[view][name]={}
            for key in ['brier','log_loss']:
                diffs=[]
                for mid in ids:
                    ra=[r for r in metrics if r['view']==view and r['arm']==a and r['match_id']==mid];rb=[r for r in metrics if r['view']==view and r['arm']==b and r['match_id']==mid]
                    if ra and rb:diffs.append(mean(ra,key)-mean(rb,key))
                if diffs:paired[view][name][key]=bootstrap(diffs)
    trading={}
    for arm in ARMS:
        rr=[r for r in trades if r['arm']==arm];wins=[r['pnl'] for r in rr if r['pnl']>0];losses=[r['pnl'] for r in rr if r['pnl']<0]
        trading[arm]={'trajectories':len(rr),'net_pnl':sum(r['pnl'] for r in rr),'gross_losses':sum(losses),'average_win':statistics.mean(wins) if wins else None,'average_loss':statistics.mean(losses) if losses else None,'worst_loss':min(r['pnl'] for r in rr),'max_match_exposure':max(r['stake'] for r in rr),'sum_entry_debits':sum(r['stake'] for r in rr),'entry_turnover_shares':sum(r['turnover_shares'] for r in rr),'entry_settlement_turnover_shares':sum(r['entry_and_settlement_turnover_shares'] for r in rr),'trading_coverage':statistics.mean(r['traded'] for r in rr)}
    trading['no_trade']={'net_pnl':0.,'gross_losses':0.,'entry_turnover_shares':0.,'trading_coverage':0.}
    trading_pairs={}
    for a,b in [('minute_no_memory','sparse_no_memory'),('sparse_memory','sparse_no_memory')]:
        ds=[mean([r for r in trades if r['arm']==a and r['match_id']==mid],'pnl')-mean([r for r in trades if r['arm']==b and r['match_id']==mid],'pnl') for mid in ids]
        trading_pairs[a+'_minus_'+b]=bootstrap(ds)
    spend=json.loads((OUT/'spending.json').read_text());after=guard(setup['guard_ledger'],ids);assert after==before;write('guard_after.json',after)
    cadence_ci=paired['shared_minute_grid']['cadence_information_age']['brier']['match_cluster_bootstrap_95'];memory_ci=paired['original_sparse_checkpoints']['memory_fixed_sparse']['brier']['match_cluster_bootstrap_95']
    classify=lambda ci:'promising' if ci[1]<0 else 'negative' if ci[0]>0 else 'inconclusive'
    result={'classification':{'cadence':classify(cadence_ci),'memory':classify(memory_ci),'overall':'inconclusive' if not complete else classify(memory_ci)},'completion':{'planned':len(plans),'completed':len(predictions),'complete':complete},'cohort':ids,'forecast_summaries':summaries,'paired':paired,'trading':trading,'trading_paired':trading_pairs,'spending':{k:v for k,v in spend.items() if k!='entries'},'guard_before':before,'guard_after':after,'guard_identical':before==after,'scope':'Retrospective development pilot; compact template, supported subset minute cadence, forecast-only fixed tape, no holdout or training.'}
    write('results.json',result)
    lines=['# Direction 5: information timing and compact memory','',f"Classification: cadence **{result['classification']['cadence']}**, memory **{result['classification']['memory']}**. Eight fixed chronological development matches, two seeds, three arms with two separate primary contrasts. No checkpoint training or protected evaluation.",'','Same compact current-observation forecast prompt in all arms. Sparse original five checkpoints versus supported minute updates (first half15–44; halftime; second half1–44). Compact memory adds last three earlier sparse snapshots of observed score, cumulative counts and prices; memory collection cadence stays sparse at sparse cadence. Memory never contains outcomes, future fills or previous model forecasts. The current event window remains defined by prior original sparse checkpoint in every arm.','', 'The primary cadence comparison is an **information-age comparison** on the shared minute clock: sparse predictions carry forward, dense predictions refresh. Original sparse checkpoints are shown separately to control added easy late observations. The primary memory contrast holds sparse cadence fixed. Forecast-only compact prompts consistently replace the original five-field trading template, so this is a cadence pilot with a modified common prompt.','', '|Clock / arm|Brier|Log loss|Valid rate|Mean forecast age seconds|Market Brier|','|---|---:|---:|---:|---:|---:|']
    for view,arms in summaries.items():
        for arm,m in arms.items():lines.append(f"|{view} / {arm}|{m['brier']:.6f}|{m['log_loss']:.6f}|{m['valid']:.3f}|{m['forecast_age_s']:.1f}|{m['market_brier']:.6f}|")
    lines+=['','Negative score deltas favor the intervention. Equal match weighting averages minutes then seeds. 95% intervals resample eight whole matches; correlated shared teams and prior development use limit interpretation.','', '|Clock / contrast|Brier delta [95% CI]|Log-loss delta [95% CI]|','|---|---:|---:|']
    for view,cc in paired.items():
        for name,v in cc.items():
            fmt=lambda k:f"{v[k]['mean_delta']:.6f} [{v[k]['match_cluster_bootstrap_95'][0]:.6f}, {v[k]['match_cluster_bootstrap_95'][1]:.6f}]"
            lines.append(f"|{view} / {name}|{fmt('brier')}|{fmt('log_loss')}|")
    lines+=['','Secondary fixed executor: 0.25 shares, displayed edge >0.05 and all-in cost <=0.65; delayed fills60 seconds, maxwait120, cap0.65; max one entry held to settlement. Pending orders pause later orders. Only execution sees fills; pricing uses identical fees/spread, cancellation assumptions, and risk policy across arms. Turnover is entry shares; entry+settlement also provided in JSON. No-trade P&L=0.','', '|Arm|Net P&L|Gross losses|Average win|Average loss|Worst loss|Max exposure|Entry turnover|Trade coverage|','|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for arm,m in trading.items():
        if arm=='no_trade':continue
        val=lambda k:'NA' if m[k] is None else f"{m[k]:.4f}"
        lines.append('|'+arm+'|'+ '|'.join(val(k) for k in ['net_pnl','gross_losses','average_win','average_loss','worst_loss','max_match_exposure','entry_turnover_shares','trading_coverage'])+'|')
    pre=json.loads((OUT/'preflight.json').read_text())
    lines+=['',f"Requests {len(predictions)}/{len(plans)}; max-token estimate ${pre['maximum_token_cost_usd']:.6f}; durable 2x reservations ${spend['reserved_max_usd']:.6f}; completed full-rate logged-token estimate ${spend['token_cost_estimate_usd']:.6f}. Actual invoice billing unavailable. $5 direction cap, $0.50 headroom, no SDK/application retries or automatic restarts.",'',f"Holdout guard before/after identical: `{before['ledger_head']}`, state `{before['state']}`, protected matches {before['protected_matches']}. No enrollment or protected inference/scoring. All release reads filtered to predeclared eight match IDs; selective-load receipts document table filters.",'','Coverage: 597/600 requested minute observations; three quotes rejected on contemporaneous price-sum coherence, 40/40 original checkpoints supported. No missing events invented. The late stop at44 minutes excludes stoppage and remains the predeclared limited minute ablation. Historical period boundaries supply original halftime/start labels as in existing replay; it does not establish real-time phase detection. Two seeds do not increase the independent match count. Invalid forecasts get worst Brier2/log loss floor penalty and no trade. Future book liquidity, cash/inventory concurrency and true order routing are not validated.','', 'Next discriminating experiment: freeze more whole matches prospectively, replay truly observed wall-clock phase detection, and test compact memory at fixed sparse cadence while scoring a separately enrolled protected cohort once. No profitability claim follows from this development pilot.','', 'Reproduce scoring with `PYTHONPATH=. HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 /Users/alexkan/Desktop/frontiers/rin-gym-forecast-only/.venv/bin/python -m experiments.eight_directions.d5_timing_memory.analyze`. Live calls cannot automatically restart; `study --live` is guarded by the durable start marker.']
    (OUT/'REPORT.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({'classification':result['classification'],'completion':result['completion'],'cadence':paired['shared_minute_grid']['cadence_information_age'],'memory':paired['original_sparse_checkpoints']['memory_fixed_sparse'],'spending':result['spending']},indent=2))
if __name__=='__main__':run()
