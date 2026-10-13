"""Independent source-based joint scorer and fit/evaluation isolation checks."""
import hashlib
import itertools
import json
import math

import numpy as np
from run import ROOT, REPO, OUT, SIDES, read_tasks, guard
from residual import fit, features


def scalar_projection(v):
    # Enumerate active coordinates independently of the vectorized implementation.
    for count in [1, 2, 3]:
        active = sorted(range(3), key=lambda i: v[i], reverse=True)[:count]
        theta = (sum(v[i] for i in active)-1)/count
        candidate = [max(x-theta, 0) for x in v]
        if abs(sum(candidate)-1) < 1e-10:
            return candidate
    raise AssertionError('Simplex projection failed')


def main():
    protocol = json.loads((OUT/'protocol.json').read_text())
    summary = json.loads((OUT/'summary.json').read_text())
    rp=json.loads((OUT/'residual-protocol.json').read_text())
    rs=json.loads((OUT/'residual-summary.json').read_text())
    for path,checksum in protocol['sources_sha256'].items():
        assert hashlib.sha256((ROOT/path).read_bytes()).hexdigest()==checksum
    assert hashlib.sha256((OUT/'protocol.json').read_bytes()).hexdigest()==rp['source_protocol_sha256']
    assert hashlib.sha256((OUT/'residual.py').read_bytes()).hexdigest()==rp['code_sha256']
    tasks=sorted([t for t in read_tasks(REPO/'artifacts/earlier-run-risk/tasks.jsonl.gz') if t['position']=='flat' and t['decision']=='h1+15'],key=lambda t:t['match_id'])
    ids=[t['match_id'] for t in tasks];lookup={mid:i for i,mid in enumerate(ids)}
    labels=np.array([SIDES.index(t['scoring']['result']) for t in tasks])
    market=np.broadcast_to(np.array([[t['scoring']['market_probs'][s] for s in SIDES] for t in tasks])[None],(3,72,3)).copy()
    model=np.full_like(market,np.nan)
    for line in (ROOT/'training-results/run-3-results/forecast-rules-run-3/repeated-validation/matches.jsonl').read_text().splitlines():
        r=json.loads(line);s,=[s for s in r['steps'] if s['decision']=='h1+15']
        model[r['repeat'],lookup[r['match_id']]]=[s['probs'][side] for side in SIDES]
    score=np.array([t['scoring']['observed_score']['home']-t['scoring']['observed_score']['away'] for t in tasks])
    selected={(r['seed'],r['fold'],r['recipe']):r for r in summary['parameter_receipts']}
    residual_selected={(r['seed'],r['fold'],r['recipe']):r for r in rs['parameter_receipts']}
    designs={(r['seed'],r['fold']):r for r in protocol['fold_membership']}
    # Every selected predictor fits on only its declared training games.
    prediction_cache={}
    for key,r in selected.items():
        d=designs[key[:2]]
        assert not set(d['fit_matches'])&set(d['evaluation_matches'])
        w,t,b=r['model_weight'],r['temperature'],r['draw_multiplier']
        p=np.maximum(w*model+(1-w)*market,1e-8)**(1/t)
        p[...,1]*=b;p/=p.sum(-1,keepdims=True)
        prediction_cache[key]=p
    isolation=0
    for key,r in residual_selected.items():
        d=designs[key[:2]];assert r['fit_matches']==d['fit_matches'] and r['evaluation_matches']==d['evaluation_matches']
        train=[lookup[mid] for mid in d['fit_matches']];test=[lookup[mid] for mid in d['evaluation_matches']]
        x=features(model,market,score,r['recipe']=='ridge_model_context')
        beta=fit(x,market,labels,train,r['ridge'])
        assert np.allclose(beta,np.array(r['beta']),rtol=1e-12,atol=1e-12)
        changed=labels.copy();changed[test]=(changed[test]+1)%3
        assert np.array_equal(beta,fit(x,market,changed,train,r['ridge']))
        isolation+=1
        raw=market+x@beta
        p=np.array([scalar_projection(v) for v in raw.reshape(-1,3)]).reshape(raw.shape)
        assert np.allclose(p.sum(-1),1)
        prediction_cache[key]=p
    checks=0
    for path,stage_summary in [('cases.jsonl',summary),('residual-cases.jsonl',rs)]:
        totals={}
        counts={}
        for line in (OUT/path).read_text().splitlines():
            row=json.loads(line);d=designs[row['seed'],row['fold']]
            assert set(row['matches'])<=set(d['evaluation_matches'])
            assert not set(row['matches'])&set(d['fit_matches'])
            a,b=[lookup[mid] for mid in row['matches']]
            actual=(labels[a],labels[b])
            for name,stored in row['scores'].items():
                p=market if name=='market' else prediction_cache[row['seed'],row['fold'],name]
                losses=[];logs=[]
                for repeat in range(3):
                    dist={(i,j):float(p[repeat,a,i]*p[repeat,b,j]) for i,j in itertools.product(range(3),repeat=2)}
                    assert abs(sum(dist.values())-1)<1e-10
                    losses.append(sum((q-float(label==actual))**2 for label,q in dist.items()))
                    logs.append(-math.log(max(dist[actual],1e-6)))
                assert abs(np.mean(losses)-stored['brier'])<1e-10
                assert abs(np.mean(logs)-stored['log_loss'])<1e-10
                k=(row['seed'],name);totals[k]=totals.get(k,0)+stored['brier'];counts[k]=counts.get(k,0)+1
                checks+=1
        for r in stage_summary['results']:
            key=r['seed'],r['recipe'];assert abs(totals[key]/counts[key]-r['brier'])<1e-10
    seal=guard(ROOT/'rin-gym-simulation/data/evals/ledger.sqlite3',ids)
    assert seal==summary['holdout_before_after']==rs['holdout_before_after']==protocol['holdout_before']
    result={'status':'passed','source_based_joint_score_checks':checks,
        'residual_fits_unchanged_by_evaluation_label_mutation':isolation,'source_and_code_hashes_verified':True,
        'fit_evaluation_match_membership_disjoint':True,'holdout_unchanged':True}
    (OUT/'verification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))


if __name__=='__main__':main()
