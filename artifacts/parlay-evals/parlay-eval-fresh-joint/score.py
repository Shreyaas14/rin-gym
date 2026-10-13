"""Score the frozen fresh joint experiment, retaining failures in every comparison."""
from collections import Counter
import json
import math

import numpy as np
from experiment import OUT, SIDES, KEYS, LEDGER, guard, parse, digest, REPEATS, RATES


def loss(probs, actual):
    if probs is None:return {'valid':False,'brier':2.,'log_loss':float(-np.log(1e-6)),'hit':None}
    return {'valid':True,'brier':math.fsum((probs[k]-float(k==actual))**2 for k in KEYS),
        'log_loss':-math.log(max(probs[actual],1e-6)),'hit':float(max(KEYS,key=lambda k:probs[k])==actual)}


def product(a,b):
    return None if a is None or b is None else {x+'_'+y:a[x]*b[y] for x,y in __import__('itertools').product(SIDES,repeat=2)}


def marginal_product(p):
    if p is None:return None
    a={side:math.fsum(p[side+'_'+b] for b in SIDES) for side in SIDES}
    b={side:math.fsum(p[a+'_'+side] for a in SIDES) for side in SIDES}
    return product(a,b)


def run():
    protocol=json.loads((OUT/'protocol.json').read_text());cases=json.loads((OUT/'cases.json').read_text())
    assert digest(cases)==protocol['cases_sha256']
    predictions=[json.loads(line) for line in (OUT/'predictions.jsonl').read_text().splitlines()]
    requests=[json.loads(line) for line in (OUT/'requests.jsonl').read_text().splitlines()]
    assert len(predictions)==len(requests)==protocol['maximum_requests']
    at={}
    for r in predictions:
        key=r['case_id'],r['repeat'],r['arm'];assert key not in at;at[key]=r
        assert r['probs']==(None if r['truncated'] or r['completion'] is None else parse(r['completion'],r['arm']))
    recipes=['joint','single_product','market','joint_marginal_product']
    rows=[]
    for c in cases:
        actual='_'.join(c['evaluation']['results'])
        for repeat in range(REPEATS):
            pj=at[c['id'],repeat,'joint']['probs'];a=at[c['id'],repeat,'single_a']['probs'];b=at[c['id'],repeat,'single_b']['probs']
            values={'joint':pj,'single_product':product(a,b),'market':product(*c['evaluation']['market_probs']),
                    'joint_marginal_product':marginal_product(pj)}
            metrics={name:loss(p,actual) for name,p in values.items()}
            rows.append({'case_id':c['id'],'repeat':repeat,'phase':c['phase'],'match_ids':c['match_ids'],'actual':actual,'metrics':metrics,
                'joint_dependence_l1':math.fsum(abs(pj[k]-values['joint_marginal_product'][k]) for k in KEYS) if pj else None})
    results={}
    common=[r for r in rows if all(r['metrics'][name]['valid'] for name in ['joint','single_product','market'])]
    for recipe in recipes:
        valid=[r['metrics'][recipe] for r in rows if r['metrics'][recipe]['valid']]
        results[recipe]={'valid_fraction':len(valid)/len(rows),
            'brier':float(np.mean([r['metrics'][recipe]['brier'] for r in rows])),
            'log_loss':float(np.mean([r['metrics'][recipe]['log_loss'] for r in rows])),
            'valid_only_brier':float(np.mean([r['brier'] for r in valid])) if valid else None,
            'favorite_ticket_hit_rate_valid_only':float(np.mean([r['hit'] for r in valid])) if valid else None,
            'common_valid_brier':float(np.mean([r['metrics'][recipe]['brier'] for r in common])) if common else None}
    rng=np.random.default_rng(protocol['seed']);indices=rng.integers(0,len(cases),(4000,len(cases)))
    paired={}
    for control in ['market','single_product','joint_marginal_product']:
        for metric in ['brier','log_loss']:
            d=np.array([np.mean([r['metrics']['joint'][metric]-r['metrics'][control][metric] for r in rows if r['case_id']==c['id']]) for c in cases])
            paired[control+'/'+metric]={'mean_joint_minus_control':float(d.mean()),
                'disjoint_pair_bootstrap_95':np.quantile(d[indices].mean(1),[.025,.975]).tolist()}
    single_comparison={}
    for metric in ['brier','log_loss']:
        d=np.array([np.mean([r['metrics']['single_product'][metric]-r['metrics']['market'][metric] for r in rows if r['case_id']==c['id']]) for c in cases])
        single_comparison[metric]={'mean_single_product_minus_market':float(d.mean()),
            'disjoint_pair_bootstrap_95':np.quantile(d[indices].mean(1),[.025,.975]).tolist(),
            'interpretation':'Secondary control comparison; same small development cohort, not a confirmatory independent improvement.'}
    known_tokens=sum(r['output_tokens'] for r in predictions if r['output_tokens'] is not None)
    upper_output=sum(r['output_tokens'] if r['output_tokens'] is not None else protocol['max_tokens'] for r in predictions)
    input_tokens=sum(r['input_tokens'] for r in predictions)
    usage={'requests':len(requests),'input_tokens':input_tokens,'known_output_tokens':known_tokens,
        'full_rate_token_cost_estimate_usd':(input_tokens*RATES['prefill']+upper_output*RATES['sample'])/1e6,
        'reserved_max_token_cost_usd':sum(r['reserved_max_cost_usd'] for r in requests),
        'note':'Token-rate estimate, not provider invoice; unobserved output charged at max cap in estimate.'}
    seal=guard(LEDGER,{mid for c in cases for mid in c['match_ids']});assert seal==protocol['holdout_before']
    summary={'cases':len(cases),'unique_games':2*len(cases),'repeat_cases':len(rows),'results':results,'paired':paired,
        'single_product_vs_market':single_comparison,
        'usage':usage,'common_valid_repeat_cases':len(common),'phase_counts':protocol['phase_counts'],
        'joint_average_dependence_l1':float(np.mean([r['joint_dependence_l1'] for r in rows if r['joint_dependence_l1'] is not None])) if any(r['joint_dependence_l1'] is not None for r in rows) else None,
        'errors':dict(Counter(r['error'] for r in predictions if r['error'])),'holdout_before_after':seal,
        'limitation':'Retrospective development, phase-matched synthetic pairs; disjoint games but shared teams across pairs and memorization can remain. No live Combo quotes or P&L.'}
    (OUT/'scored-cases.json').write_text(json.dumps(rows,indent=2)+'\n')
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2))


if __name__=='__main__':run()
