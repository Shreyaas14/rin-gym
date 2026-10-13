"""Pair scores with underlying-game bootstrap, not independent ticket rows."""
from collections import Counter
import json
import math

import numpy as np
from expanded import OUT, OLD, REPEATS, SEED, RATES, LEDGER, guard, digest, parse
from score import product, loss


def run():
    p=json.loads((OUT/'protocol.json').read_text());points=json.loads((OUT/'observations.json').read_text());pairs=json.loads((OUT/'pairs.json').read_text())
    assert digest(points)==p['points_sha256'] and digest(pairs)==p['pairs_sha256']
    predictions=[json.loads(line) for line in (OUT/'predictions.jsonl').read_text().splitlines()]
    requests=[json.loads(line) for line in (OUT/'requests.jsonl').read_text().splitlines()]
    assert len(predictions)==len(requests)==p['maximum_requests']
    at={}
    for r in predictions:
        key=r['observation_id'],r['repeat'];assert key not in at;at[key]=r
        assert r['probs']==(None if r['truncated'] or r['completion'] is None else parse(r['completion'],'single_a'))
    rows=[]
    for pair in pairs:
        a,b=[points[i] for i in pair['indices']];actual=a['evaluation']['result']+'_'+b['evaluation']['result']
        results=[]
        for rep in range(REPEATS):
            pa=at[a['id'],rep]['probs'];pb=at[b['id'],rep]['probs']
            results.append({'repeat':rep,'single_product':loss(product(pa,pb),actual),
                'market':loss(product(a['evaluation']['market_probs'],b['evaluation']['market_probs']),actual)})
        rows.append({**pair,'observation_ids':[a['id'],b['id']],'actual':actual,'repeat_scores':results,
            'scores':{recipe:{metric:float(np.mean([r[recipe][metric] for r in results])) for metric in ['brier','log_loss','valid']}
                      for recipe in ['single_product','market']}})
    phases=sorted({point['phase'] for point in points})
    def aggregate(selected):
        perphase={}
        for phase in phases:
            phase_rows=[r for r in selected if r['phase']==phase]
            if not phase_rows:continue
            perphase[phase]={'pair_points':len(phase_rows),
                **{recipe:{metric:float(np.mean([r['scores'][recipe][metric] for r in phase_rows]))
                           for metric in ['brier','log_loss','valid']} for recipe in ['single_product','market']}}
        equal={recipe:{metric:float(np.mean([v[recipe][metric] for v in perphase.values()]))
                       for metric in ['brier','log_loss','valid']} for recipe in ['single_product','market']}
        weighted={recipe:{metric:float(np.mean([r['scores'][recipe][metric] for r in selected]))
                          for metric in ['brier','log_loss','valid']} for recipe in ['single_product','market']}
        return {'pair_points':len(selected),'phase_results':perphase,'equal_phase_metrics':equal,'pair_count_weighted_metrics':weighted}
    additional=[r for r in rows if not r['in_original_24']];original=[r for r in rows if r['in_original_24']]
    aggregates={'additional_primary':aggregate(additional),'all_pairs':aggregate(rows),'original_24_new_outputs':aggregate(original)}
    ids=sorted({point['match_id'] for point in points});index={mid:i for i,mid in enumerate(ids)}
    rng=np.random.default_rng(SEED)
    node_weights=rng.multinomial(len(ids),np.full(len(ids),1/len(ids)),size=4000)
    bootstrap={}
    for metric in ['brier','log_loss']:
        phase_replicates=[]
        for phase in phases:
            group=[r for r in additional if r['phase']==phase]
            aa=np.array([index[r['match_ids'][0]] for r in group]);bb=np.array([index[r['match_ids'][1]] for r in group])
            weights=node_weights[:,aa]*node_weights[:,bb]
            delta=np.array([r['scores']['single_product'][metric]-r['scores']['market'][metric] for r in group])
            den=weights.sum(1)
            replicate=np.divide(weights@delta,den,out=np.full(4000,np.nan),where=den>0)
            phase_replicates.append(replicate)
        boot=np.mean(phase_replicates,axis=0);valid=boot[np.isfinite(boot)]
        metrics=aggregates['additional_primary']['equal_phase_metrics']
        bootstrap[metric]={'mean_single_product_minus_market':metrics['single_product'][metric]-metrics['market'][metric],
            'game_bootstrap_95':np.quantile(valid,[.025,.975]).tolist(),'valid_bootstrap_draws':len(valid),
            'method':'Resample 54 underlying game IDs, pair weight = game multiplicities product; recompute within-phase means and equal-phase average; no self-fixture pairs.'}
    single_pergame={}
    for mid in ids:
        ps=[point for point in points if point['match_id']==mid]
        values=[]
        for point in ps:
            actual=point['evaluation']['result'];market=point['evaluation']['market_probs']
            for rep in range(REPEATS):
                probs=at[point['id'],rep]['probs']
                values.append({'model_brier':sum((probs[s]-float(s==actual))**2 for s in ['home','draw','away']) if probs else 2.,
                    'market_brier':sum((market[s]-float(s==actual))**2 for s in ['home','draw','away'])})
        single_pergame[mid]={key:float(np.mean([r[key] for r in values])) for key in ['model_brier','market_brier']}
    inputs=sum(r['input_tokens'] for r in predictions)
    outputs=sum(r['output_tokens'] if r['output_tokens'] is not None else p['max_tokens'] for r in predictions)
    usage={'requests':len(requests),'input_tokens':inputs,'observed_or_max_charged_output_tokens':outputs,
        'full_rate_token_cost_estimate_usd':(inputs*RATES['prefill']+outputs*RATES['sample'])/1e6,
        'reserved_max_token_cost_usd':sum(r['reserved_max_cost_usd'] for r in requests),'note':'Token-rate estimate, not provider invoice; unknown output charged at cap.'}
    seal=guard(LEDGER,ids);assert seal==p['holdout_before']
    result={'observations':len(points),'unique_games':len(ids),'aggregates':aggregates,'primary_paired_uncertainty':bootstrap,
        'valid_single_responses':sum(r['probs'] is not None for r in predictions),'single_response_count':len(predictions),
        'single_game_weighted_brier':{key:float(np.mean([v[key] for v in single_pergame.values()])) for key in ['model_brier','market_brier']},
        'errors':dict(Counter(r['error'] for r in predictions if r['error'])),'usage':usage,'holdout_before_after':seal,
        'limitation':'Additional combinations mostly reuse historical games, observations and teams. Resampling recognizes shared fixture IDs but not all shared-team/tournament dependence or model memorization.'}
    (OUT/'scored-pairs.json').write_text(json.dumps(rows,indent=2)+'\n')
    (OUT/'single-game-scores.json').write_text(json.dumps(single_pergame,indent=2)+'\n')
    (OUT/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':run()
