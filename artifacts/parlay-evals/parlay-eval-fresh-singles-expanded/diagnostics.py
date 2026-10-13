"""Post-hoc validity/fallback diagnostics; primary frozen results remain unchanged."""
import json
import numpy as np
from expanded import OUT,SEED,REPEATS
from score import product,loss


def run():
    points=json.loads((OUT/'observations.json').read_text());pairs=json.loads((OUT/'pairs.json').read_text())
    predictions=[json.loads(line) for line in (OUT/'predictions.jsonl').read_text().splitlines()]
    at={(r['observation_id'],r['repeat']):r['probs'] for r in predictions}
    rows=[]
    for pair in pairs:
        if pair['in_original_24']:continue
        a,b=[points[i] for i in pair['indices']];truth=a['evaluation']['result']+'_'+b['evaluation']['result']
        original=[];fallback=[];baseline=[]
        for rep in range(REPEATS):
            pa,pb=at[a['id'],rep],at[b['id'],rep]
            ma,mb=a['evaluation']['market_probs'],b['evaluation']['market_probs']
            original.append(loss(product(pa,pb),truth))
            fallback.append(loss(product(pa if pa is not None else ma,pb if pb is not None else mb),truth))
            baseline.append(loss(product(ma,mb),truth))
        rows.append({**pair,'valid':all(r['valid'] for r in original),
            'original_brier':float(np.mean([r['brier'] for r in original])),
            'fallback_brier':float(np.mean([r['brier'] for r in fallback])),
            'market_brier':float(np.mean([r['brier'] for r in baseline]))})
    phases=sorted({r['phase'] for r in rows});ids=sorted({p['match_id'] for p in points});lookup={mid:i for i,mid in enumerate(ids)}
    node=np.random.default_rng(SEED).multinomial(len(ids),np.full(len(ids),1/len(ids)),size=4000)
    result={'scope':'Post-hoc diagnostic following three invalid outputs. Does not change primary acceptance, scoring or experiment success criteria.',
            'fallback_rule':'Replace an invalid leg forecast with its contemporaneous normalized market vector; no result or future price reaches fallback.'}
    for name,metric,valid_only in [('common_valid','original_brier',True),('invalid_leg_market_fallback','fallback_brier',False)]:
        selected=[r for r in rows if r['valid'] or not valid_only]
        model=[];market=[];replicates=[]
        for phase in phases:
            group=[r for r in selected if r['phase']==phase]
            model.append(float(np.mean([r[metric] for r in group])));market.append(float(np.mean([r['market_brier'] for r in group])))
            weights=np.column_stack([node[:,lookup[r['match_ids'][0]]]*node[:,lookup[r['match_ids'][1]]] for r in group])
            den=weights.sum(1);delta=np.array([r[metric]-r['market_brier'] for r in group])
            replicates.append(np.divide(weights@delta,den,out=np.full(4000,np.nan),where=den>0))
        boot=np.mean(replicates,axis=0);boot=boot[np.isfinite(boot)]
        result[name]={'pair_points':len(selected),'model_equal_phase_brier':float(np.mean(model)),
            'market_same_cases_equal_phase_brier':float(np.mean(market)),
            'mean_model_minus_market':float(np.mean(model)-np.mean(market)),
            'game_bootstrap_95':np.quantile(boot,[.025,.975]).tolist()}
    (OUT/'diagnostics.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))


if __name__=='__main__':run()
