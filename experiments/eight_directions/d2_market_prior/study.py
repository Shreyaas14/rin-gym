"""Predeclared market-prior direction, frozen signal fit and assessment."""
from pathlib import Path
import argparse, hashlib, json, math, statistics
import numpy as np
from app.trade_env.tasks import read_tasks
from app.trade_env.replay import by_match
from experiments.forecast_only.core import guard, digest
from experiments.forecast_only.risk_policy import replay_match, RiskPolicy

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'artifacts/eight-directions/d2_market_prior'
SETUP=ROOT/'research/eight-directions/setup.json'
SIDES=['home','draw','away']; SEED=20261013; FLOOR=1e-4

def write(name,x):
    (OUT/name).write_text(json.dumps(x,indent=2,allow_nan=False)+'\n')

def protocol():
    s=json.loads(SETUP.read_text()); part=s['chronological_replay_partition']
    ids=part['fit_match_ids']+part['assessment_match_ids']
    before=guard(s['guard_ledger'],ids)
    obj={'hypothesis':'Shrinking the original model probabilities toward contemporaneous normalized market prices may improve proper scores; incremental value requires beating market alone.',
      'scope':'Post-hoc research on repeatedly inspected historical development matches; predeclared before this direction analysis results. No protected assessment.',
      'intervention':'p_blend=(1-alpha)*p_market+alpha*p_original; one alpha clipped to [0,1] analytically minimizes equally match-weighted multiclass Brier on fitting games only.',
      'source_files':['inputs/earlier-run-risk/tasks.jsonl.gz','inputs/closed-loop-risk/predictions.jsonl','inputs/closed-loop-risk/matches.jsonl','inputs/closed-loop-risk/protocol.json'],
      'source_sha256':{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in ['inputs/earlier-run-risk/tasks.jsonl.gz','inputs/closed-loop-risk/predictions.jsonl','inputs/closed-loop-risk/matches.jsonl','inputs/closed-loop-risk/protocol.json']},
      'source_manifest_sha256':s['source_manifest_sha256'],'cohort':part,'guard_before':before,
      'comparisons':['market_only','original_model_with_market_visible','fitted_blend'],
      'metrics':['multiclass_brier_sum_equal_match_weight','negative_log_realized_outcome_probability_floor_1e-4_equal_match_weight','valid_rate','fixed_rule_trading'],
      'invalid_rule':'Original invalid output: Brier=2, log loss=-log(1e-4), no trade. Blend preserves original invalidity; no asymmetric market fallback. Source valid probability normalization semantics retained.',
      'aggregation':'Average decisions and three repeats within each match; games equally weighted. Trading totals sum game-repeats, also report per-game-repeat means.',
      'bootstrap':{'seed':SEED,'reps':10000,'unit':'match','confidence':0.95,'method':'paired percentile; repeats/timestamps retained within match'},
      'trading_policy':vars(RiskPolicy()),'execution_kind':'Frozen original position-conditioned signal counterfactual; same fixed entry/hold rule and task delayed fills for all forecasts. This is not fresh closed-loop inference.',
      'timing':{'event_delay_s':60,'fill_delay_s':60,'price_max_age_s':90},
      'optional_fresh':{'cohort':'all 33 assessment games, all original decisions, three repeats, both matched arms','control':'same original five-field forecast/action prompts and original observed-position tape','intervention':'append instruction to normalize currently displayed prices as prior and make only evidence-supported small changes (absolute change <=0.10 per outcome)','settings':'Qwen/Qwen3-8B original final checkpoint; qwen3_disable_thinking, temperature=1, max_tokens=256, shared seeds by match/decision/repeat','analysis_kind':'Matched frozen observed-state inference, not fresh closed-loop trajectories','readiness':'Only after concrete cost preflight and orchestrator implementation/budget confirmation; no automatic retries'},
      'stopping_rule':'One offline alpha fit and later assessment; optional single fixed fresh matched study only if provenance/auth/budget available. No threshold tuning, cohort substitution, checkpoint selection or reruns.',
      'budget_usd':5,'prior_reports_inspected':['inputs/closed-loop-risk/REPORT.md'],'limitations':['Repeated development use','Same teams create dependence beyond match bootstrap','Known historical matches may be remembered','Sparse five-point schedule','Mid-price and fractional-share/limit assumptions','Market already visible to original model']}
    if (OUT/'protocol.json').exists():
        old=json.loads((OUT/'protocol.json').read_text());assert old['source_sha256']==obj['source_sha256'];return old
    write('protocol.json',obj)
    write('spending.json',{'authorized_usd':5,'reserved_maximum_usd':0,'completed_token_cost_estimate_usd':0,'verified_billing_usd':None,'paid_requests':0,'note':'Offline analysis uses existing predictions; no new external service spending.'})
    return obj

def boot_delta(a,b,key,ids):
    d=np.array([a[i][key]-b[i][key] for i in ids]); rng=np.random.default_rng(SEED)
    vals=d[rng.integers(0,len(ids),(10000,len(ids)))].mean(1)
    return {'mean_delta':float(d.mean()),'paired_match_bootstrap_95':np.quantile(vals,[.025,.975]).tolist(),'matches':len(ids)}

def trade_stats(rows):
    win=[r['pnl'] for r in rows if r['pnl']>1e-10];loss=[r['pnl'] for r in rows if r['pnl']< -1e-10]
    traded=sum(r['traded'] for r in rows);n=len(rows)
    return {'game_repeats':n,'matches':len({r['match_id'] for r in rows}),'net_pnl':sum(r['pnl'] for r in rows),'gross_wins':sum(win),'gross_losses':sum(loss),'average_win':statistics.mean(win) if win else None,'average_loss':statistics.mean(loss) if loss else None,'worst_loss':min(r['pnl'] for r in rows),'traded_game_repeats':traded,'trading_coverage':traded/n,'win_rate_traded':len(win)/traded if traded else None,'total_entry_exposure':sum(r['stake'] for r in rows),'maximum_match_exposure':max(r['stake'] for r in rows),'turnover_shares':traded*.25,'mean_pnl_per_game_repeat':sum(r['pnl'] for r in rows)/n,'simultaneous_exposure':'Not simulated; total entry debits and maximum per-match exposure only'}

def evaluate(rows,tasks,alpha):
    arms={'market_only':{},'original_model_with_market_visible':{},'fitted_blend':{}}
    points={a:[] for a in arms}
    for r in rows:
        t=tasks[r['task_id']]; mid=t['match_id'];m=np.array([t['scoring']['market_probs'][k] for k in SIDES]);y=t['scoring']['result']
        p=np.array([r['probs'][k] for k in SIDES]) if r['valid'] else None
        ps={'market_only':m,'original_model_with_market_visible':p,'fitted_blend':(1-alpha)*m+alpha*p if p is not None else None}
        for a,x in ps.items():
            if x is None:b=2.;ll=-math.log(FLOOR)
            else:b=float(sum((x[i]-float(k==y))**2 for i,k in enumerate(SIDES)));ll=-math.log(max(FLOOR,x[SIDES.index(y)]))
            points[a].append({'match_id':mid,'repeat':r['repeat'],'decision':t['decision'],'task_id':t['id'],'observed_t':t['t'],'brier':b,'log_loss':ll,'valid':x is not None,'probs':dict(zip(SIDES,x.tolist())) if x is not None else None})
    for a in arms:
        for mid in sorted({t['match_id'] for t in tasks.values()}):
            rs=[r for r in points[a] if r['match_id']==mid]
            arms[a][mid]={k:statistics.mean(r[k] for r in rs) for k in ['brier','log_loss','valid']}
    return arms,points

def run():
    pro=protocol();s=json.loads(SETUP.read_text());fit=pro['cohort']['fit_match_ids'];assess=pro['cohort']['assessment_match_ids'];ids=fit+assess
    ts=read_tasks(ROOT/'inputs/earlier-run-risk/tasks.jsonl.gz');tm={t['id']:t for t in ts};grouped=by_match(ts)
    assert set(ids)==set(grouped) and set(ids)<=set(s['allowed_match_ids'])
    rows=[json.loads(l) for l in (ROOT/'inputs/closed-loop-risk/predictions.jsonl').read_text().splitlines() if json.loads(l)['arm']=='original']
    assert len(rows)==1077 and len({(r['task_id'].split(':')[0],r['repeat']) for r in rows})==216
    assert len({(r['task_id'].split(':')[0],r['task_id'].split(':')[1],r['repeat']) for r in rows})==len(rows)
    numerator=0.;denominator=0.
    for mid in fit:
        rs=[r for r in rows if tm[r['task_id']]['match_id']==mid]
        for r in rs:
            if not r['valid']:continue
            t=tm[r['task_id']];m=np.array([t['scoring']['market_probs'][k] for k in SIDES]);p=np.array([r['probs'][k] for k in SIDES]);d=p-m;y=np.array([float(k==t['scoring']['result']) for k in SIDES])
            numerator+=float(np.dot(d,y-m))/len(rs);denominator+=float(np.dot(d,d))/len(rs)
    alpha=float(np.clip(numerator/denominator,0,1)) if denominator else 0.
    arms,points=evaluate(rows,tm,alpha)
    config=json.loads((ROOT/'inputs/closed-loop-risk/protocol.json').read_text())['config']
    assert all(config[k]==v for k,v in pro['timing'].items())
    traded={a:[] for a in arms}
    for a in arms:
        for mid in ids:
            for rep in range(3):
                pred={f"{mid}:{r['decision']}:flat":r['probs'] for r in points[a] if r['match_id']==mid and r['repeat']==rep}
                tr=replay_match(grouped[mid],pred,config,RiskPolicy());tr['repeat']=rep;traded[a].append(tr)
    result={'classification':'inconclusive','alpha_model_weight':alpha,'fit_weight_objective':{'numerator':numerator,'denominator':denominator},'partitions':{},'guard_before':pro['guard_before'],'scope':pro['scope'],'prompt_availability':{'market_prices_visible':True,'original_model_is_market_blind':False,'source_template':'app/trade_env/templates/user.j2'},'fresh_status':'Awaiting provenance/auth/cost readiness; no paid work performed'}
    per_match={}
    for partition, mids in [('fit',fit),('assessment',assess)]:
        part={'matches':len(mids),'forecast':{},'trading':{},'paired':{}};per_match[partition]={}
        for a in arms:
            part['forecast'][a]={k:statistics.mean(arms[a][i][k] for i in mids) for k in ['brier','log_loss','valid']}
            rs=[r for r in traded[a] if r['match_id'] in mids];part['trading'][a]=trade_stats(rs)
            pm={mid:{'pnl':statistics.mean(r['pnl'] for r in rs if r['match_id']==mid),**arms[a][mid]} for mid in mids};per_match[partition][a]=pm
        part['trading']['no_trade']={'net_pnl':0.,'mean_pnl_per_game_repeat':0.,'trading_coverage':0.,'turnover_shares':0.,'total_entry_exposure':0.}
        for a,b in [('original_model_with_market_visible','market_only'),('fitted_blend','market_only'),('fitted_blend','original_model_with_market_visible')]:
            part['paired'][a+'_minus_'+b]={k:boot_delta(per_match[partition][a],per_match[partition][b],k,mids) for k in ['brier','log_loss','pnl']}
        result['partitions'][partition]=part
    result['guard_after']=guard(s['guard_ledger'],ids);assert result['guard_after']==result['guard_before']
    write('results.json',result);write('match_metrics.json',per_match);write('trading_receipts.json',traded)
    write('guard.json',{'before':pro['guard_before'],'after':result['guard_after'],'identical':True,'actual_match_ids':ids})
    (OUT/'analysis_receipts.jsonl').write_text(''.join(json.dumps({'arm':a,**r},allow_nan=False)+'\n' for a in points for r in points[a]))
    print(json.dumps({'alpha':alpha,'assessment':result['partitions']['assessment']},indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--protocol-only',action='store_true');args=parser.parse_args()
    protocol() if args.protocol_only else run()
