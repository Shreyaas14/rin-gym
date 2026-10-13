"""Equal-match matched analysis; outcomes accessed only after inference."""
from collections import Counter, defaultdict
import json
import math
import statistics
import numpy as np
from experiments.eight_directions.d4_position_bias.study import OUT, ARMS, load, write
from experiments.forecast_only.core import guard

def brier(p,y):return sum((p[s]-float(s==y))**2 for s in ARMS[1:]) if p else 2.
def loss(p,y):return -math.log(max(p[y],1e-4)) if p else -math.log(1e-4)
def mean(v):return statistics.mean(v) if v else None

def summarize(rows,tm,valid_key):
    grouped=defaultdict(dict)
    for r in rows:grouped[(r['match_id'],r['observed_t'],r['repeat'])][r['arm']]=r
    sets=[v for v in grouped.values() if set(v)==set(ARMS) and all(v[a][valid_key] for a in ARMS)]
    per_match=defaultdict(lambda:defaultdict(list)); arm_values=defaultdict(lambda:defaultdict(lambda:defaultdict(list)))
    for v in sets:
        mid=v['flat']['match_id']; y=tm[v['flat']['task_id']]['scoring']['result'];flat=v['flat']['probs']
        uplift=[];bd=[];ld=[];ad=[]
        for arm in ARMS:
            p=v[arm]['probs'];arm_values[arm][mid]['brier'].append(brier(p,y));arm_values[arm][mid]['log_loss'].append(loss(p,y))
            if arm=='flat':continue
            delta=p[arm]-flat[arm];uplift.append(delta)
            per_match[mid]['uplift_'+arm].append(delta)
            per_match[mid]['brier_delta_'+arm].append(brier(p,y)-brier(flat,y))
            bd.append(brier(p,y)-brier(flat,y));ld.append(loss(p,y)-loss(flat,y))
            ad.append(sum(abs(p[s]-flat[s]) for s in ARMS[1:])/3)
        per_match[mid]['primary_held_uplift'].append(mean(uplift));per_match[mid]['mean_brier_delta_held_vs_flat'].append(mean(bd));per_match[mid]['mean_log_loss_delta_held_vs_flat'].append(mean(ld));per_match[mid]['mean_absolute_component_shift'].append(mean(ad))
        market=tm[v['flat']['task_id']]['scoring']['market_probs']
        arm_values['market'][mid]['brier'].append(brier(market,y));arm_values['market'][mid]['log_loss'].append(loss(market,y))
    mids=sorted(per_match)
    if not mids:return {'complete_sets':0,'matches':0,'metrics':{},'arms':{}}
    rng=np.random.default_rng(202610134);idx=rng.integers(0,len(mids),(4000,len(mids)))
    metrics={}
    for metric in per_match[mids[0]]:
        values=np.array([mean(per_match[mid][metric]) for mid in mids])
        metrics[metric]={'mean':float(values.mean()),'match_bootstrap_95':np.quantile(values[idx].mean(1),[.025,.975]).tolist(),'match_means':dict(zip(mids,values.tolist()))}
    arms={}
    for arm in [*ARMS,'market']:
        arms[arm]={}
        for metric in ['brier','log_loss']:
            values=np.array([mean(arm_values[arm][mid][metric]) for mid in mids])
            arms[arm][metric]={'mean':float(values.mean()),'match_bootstrap_95':np.quantile(values[idx].mean(1),[.025,.975]).tolist()}
        # Fixed bins [0,.2,.4,.6,.8,1]; equal-match sample weights.
        totals={s:np.zeros((5,3)) for s in ARMS[1:]}
        counts=Counter(v['flat']['match_id'] for v in sets)
        for v in sets:
            mid=v['flat']['match_id'];t=tm[v['flat']['task_id']];y=t['scoring']['result']
            p=t['scoring']['market_probs'] if arm=='market' else v[arm]['probs'];w=1/(len(mids)*counts[mid])
            for s in ARMS[1:]:
                bin_idx=min(4,int(p[s]*5));totals[s][bin_idx]+=np.array([w,w*p[s],w*float(s==y)])
        arms[arm]['calibration']={'five_bin_classwise_ece':sum(float(np.abs(t[:,1]-t[:,2]).sum()) for t in totals.values())/3,'signed_class_residual':{s:float((t[:,1]-t[:,2]).sum()) for s,t in totals.items()},'bin_edges':[0,.2,.4,.6,.8,1],'bins':{s:[{'weight':float(a),'mean_probability':float(b/a) if a else None,'event_frequency':float(c/a) if a else None} for a,b,c in t] for s,t in totals.items()},'note':'Diagnostic point estimates; no bin selection; calibration uncertainty not estimated.'}
    return {'complete_sets':len(sets),'matches':len(mids),'metrics':metrics,'arms':arms,'weighting':'Equal match; within match equal decision-time/seed sets; held average equal sides.'}

def analyze():
    setup,ids,tasks=load();tm={t['id']:t for t in tasks};p=json.loads((OUT/'protocol.json').read_text())
    rows=[json.loads(l) for l in (OUT/'predictions.jsonl').read_text().splitlines()] if (OUT/'predictions.jsonl').exists() else []
    primary=summarize(rows,tm,'valid');sensitivity=summarize(rows,tm,'probability_valid')
    validity={arm:{'completed':sum(r['arm']==arm for r in rows),'full_valid_rate':mean([r['valid'] for r in rows if r['arm']==arm]),'probability_valid_rate':mean([r['probability_valid'] for r in rows if r['arm']==arm]),'truncated':sum(r['truncated'] for r in rows if r['arm']==arm),'actions':dict(Counter(r['action'] for r in rows if r['arm']==arm))} for arm in ARMS}
    unconditional={}
    for arm in ARMS:
        grouped=defaultdict(list)
        for r in rows:
            if r['arm']==arm:grouped[r['match_id']].append(r)
        unconditional[arm]={metric:mean([mean([fn(r['probs'] if r['valid'] else None,tm[r['task_id']]['scoring']['result']) for r in rr]) for rr in grouped.values()]) for metric,fn in [('brier_full_invalid_worst_case',brier),('log_loss_full_invalid_floor',loss)]}
    metric=primary['metrics'].get('primary_held_uplift');complete=len(rows)==p['maximum_requests']
    if not rows:classification='blocked'
    elif not complete or not metric:classification='inconclusive'
    elif metric['match_bootstrap_95'][0]>0:classification='promising'
    elif metric['match_bootstrap_95'][1]<=0:classification='negative'
    else:classification='inconclusive'
    after=guard(setup['guard_ledger'],ids);assert after==p['seal_before']
    result={'classification':classification,'interpretation':'Matched position/menu/selling-proceeds prompt-bundle sensitivity, not trading outcome or isolated psychological bias.','maximum_requests':p['maximum_requests'],'completed_requests':len(rows),'complete':complete,'primary_complete_valid':primary,'probability_valid_sensitivity':sensitivity,'validity':validity,'unconditional_invalid_penalty_scores':unconditional,'spending':json.loads((OUT/'spending.json').read_text()),'seal_before':p['seal_before'],'seal_after':after,'protected_enrollment_tested':False,'limitations':['Only 12 repeatedly inspected development matches; assessment is chronological but not untouched.','Position line, legal menu and sell information move together; cannot identify their individual causes.','Complete-case primary may select valid outputs; sensitivity and invalid penalties exposed.','Two seeds; paired seeds reduce noise but do not guarantee identical random draws after prompt differences.','Whole-match bootstrap does not model shared-team dependence.','Actions legitimately depend on positions; no execution, P&L or profitability claim.','Fixed-bin calibration uses a small sample and is descriptive.']}
    write('results.json',result)
    print(json.dumps({'classification':classification,'completed_requests':len(rows),'primary':metric,'spending_estimate':result['spending']['token_cost_estimate_usd']}),flush=True)
    report(result,p)

def report(r,p):
    main=r['primary_complete_valid'];lines=['# Direction 4: position-induced forecast sensitivity','',f"Classification: **{r['classification']}**. {r['interpretation']}",'','The hypothesis is that holding a contract shifts its forecast probability upward relative to a flat position with identical observed match information. We used the earlier verified final Qwen3-8B checkpoint without training.','',f"The frozen cohort is the first 12 later-assessment development matches by first observation time: {', '.join(p['cohort_match_ids'])}. These historically inspected games are not a protected holdout. All 60 existing decision states were crossed with four positions and two matched seeds, for {p['maximum_requests']} maximum requests. There is no fitted method or data-based threshold selection.",'','The system prompt and user match events, score, clock, price history and buying costs were checked identical across all 60 sets. Only the existing position, legal-action and current selling-proceeds lines differ. Associated prompts are part of the intervention; this design does not isolate an endowment mechanism. Information delays remain event 60s, fill 60s, price maximum age 90s. Prepared request messages contain no outcome labels or future fills. No execution occurs.','',f"Completed {r['completed_requests']}/{r['maximum_requests']} requests. Primary complete-valid four-arm sets: {main['complete_sets']} across {main['matches']} matches. Strict schema/auxiliary validity and probs-only sensitivity are separate.",'','| Comparison | Effect / level | Match bootstrap 95% |','|---|---:|---|']
    for name,m in main['metrics'].items():lines.append(f"| {name} | {m['mean']:.6f} | [{m['match_bootstrap_95'][0]:.6f}, {m['match_bootstrap_95'][1]:.6f}] |")
    lines+=['','| Arm | Full valid | Probability valid | Brier | Log loss | Five-bin ECE |','|---|---:|---:|---:|---:|---:|']
    for arm in [*ARMS,'market']:
        m=main['arms'].get(arm);v=r['validity'].get(arm,{})
        if m:lines.append(f"| {arm} | {v.get('full_valid_rate','n/a')} | {v.get('probability_valid_rate','n/a')} | {m['brier']['mean']:.6f} | {m['log_loss']['mean']:.6f} | {m['calibration']['five_bin_classwise_ece']:.6f} |")
    sens=r['probability_valid_sensitivity']['metrics'].get('primary_held_uplift')
    lines+=['',f"Probs-only sensitivity primary effect: {sens if sens else 'unavailable'}. Lower Brier/log loss is better; log loss floors the realized probability at 0.0001. Calibration uses five fixed bins and equal-match weighting; its uncertainty is not estimated. Complete-case results and full invalid-penalty scores are exposed in results.json.",'','The primary averages held-side shifts over sides, decision times and seeds within each match, then weights matches equally. The bootstrap resamples whole matches 4,000 times with seed 202610134. Positive uplift measures ownership-linked prompt sensitivity. It does not by itself mean the forecast became less accurate. Action distributions are descriptive because legal menus differ.','',f"Maximum uncached token cost planned: ${p['maximum_token_cost_usd']:.6f}; retained 2x maximum reservations: ${r['spending']['reserved_max_usd']:.6f}; completed logged-token estimate: ${r['spending']['token_cost_estimate_usd']:.6f}. Direction cap $5 with $0.50 unallocated headroom. No training/storage cost; verified invoice billing is unavailable. Pending reservations remain encumbered and automatic retries/restarts are disabled.",'','Limitations: '+ ' '.join(r['limitations']),'','Next discriminating experiment: preregister a larger newly enrolled cohort, cross a neutral forecast-only role with positions, and vary legal-action/selling prompts independently to separate ownership wording from action-context effects. Preserve paired observed states and report validity before causal interpretation.','',f"Guard before/after identical: {r['seal_before']==r['seal_after']}; ledger head `{r['seal_after']['ledger_head']}`; state `{r['seal_after']['state']}` with {r['seal_after']['protected_matches']} protected matches. Enrollment is pending; no protected holdout was tested.",'','Reproduce offline analysis:','```sh','HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH=. /Users/alexkan/Desktop/frontiers/rin-gym-forecast-only/.venv/bin/python experiments/eight_directions/d4_position_bias/analyze.py','```','Paid inference was run once with `study.py --live`; the start marker prevents automatic resumption. Raw request messages, observation hashes, seeds, completions and output token IDs are preserved.']
    (OUT/'REPORT.md').write_text('\n'.join(lines)+'\n')

if __name__=='__main__':analyze()
