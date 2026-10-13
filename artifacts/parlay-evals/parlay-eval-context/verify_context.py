"""Independently verify prefix-only groups, scalar joint scores and fold isolation."""
from collections import defaultdict
import hashlib
import itertools
import json
import math

import numpy as np
from run_context import ROOT, OUT, load, visible_features, member, GROUPS, FAMILIES, choose


def main():
    protocol=json.loads((OUT/'protocol.json').read_text());summary=json.loads((OUT/'summary.json').read_text())
    for path,checksum in protocol['source_hashes'].items():
        assert hashlib.sha256((ROOT/path).read_bytes()).hexdigest()==checksum
    data=load();features=data['features'];m=data['market'];p=data['model'];labels=data['labels']
    for i,f in features.items():
        assert f['last_visible_event'] is None or f['last_visible_event']<=f['event_cutoff']
        # Delete or corrupt all future rows: cohort membership must remain unchanged.
        rows=data['events'][f['match_id']]
        prefix=[r for r in rows if r['event_time_utc'].timestamp()<=f['event_cutoff']]
        mutated=[dict(r,home_score=999,away_score=999,event_kind='red_card') if r['event_time_utc'].timestamp()>f['event_cutoff'] else r for r in rows]
        assert visible_features(prefix,f['event_cutoff'])==visible_features(rows,f['event_cutoff'])==visible_features(mutated,f['event_cutoff'])
    designs={(r['seed'],r['fold']):r for r in protocol['fold_membership']}
    params={(r['seed'],r['fold'],r['group'],r['recipe']):r for r in summary['parameter_receipts']}
    cached={}
    for key,r in params.items():
        w,t,d=r['params'];a=np.maximum(w*p+(1-w)*m,1e-8)**(1/t);a[...,1]*=d;a/=a.sum(-1,keepdims=True);cached[key]=a
        if r['fallback']:assert r['params']==[0.,1.,1.]
    totals=defaultdict(float);counts=defaultdict(int);checks=0
    for line in (OUT/'cases.jsonl').read_text().splitlines():
        r=json.loads(line);a,b=r['point_indices'];d=designs[r['seed'],r['fold']]
        assert set(r['matches'])<=set(d['evaluation_matches']) and not set(r['matches'])&set(d['fit_matches'])
        assert features[a]['phase']==features[b]['phase']==r['phase']
        assert not data['teams'][a]&data['teams'][b]
        assert member(r['group'],a,b,features,m)
        event=features[a]['recent_goal'] or features[b]['recent_goal'] or features[a]['red_visible'] or features[b]['red_visible']
        for recipe,stored in r['scores'].items():
            if recipe=='market':pred=m
            elif recipe=='model':pred=p
            elif recipe.startswith('event_gated'):
                base='calibrated_market' if recipe=='event_gated_market' else 'calibrated_blend'
                pred=cached[r['seed'],r['fold'],'event_gate_fit',base] if event else m
            else:pred=cached[r['seed'],r['fold'],r['group'],recipe]
            losses=[];logs=[]
            for repeat in range(3):
                dist={(x,y):float(pred[repeat,a,x]*pred[repeat,b,y]) for x,y in itertools.product(range(3),repeat=2)}
                actual=(labels[a],labels[b]);assert abs(sum(dist.values())-1)<1e-9
                losses.append(sum((q-float(s==actual))**2 for s,q in dist.items()))
                logs.append(-math.log(max(dist[actual],1e-6)))
            assert abs(np.mean(losses)-stored['brier'])<1e-9
            assert abs(np.mean(logs)-stored['log_loss'])<1e-9
            key=r['seed'],r['group'],recipe;totals[key]+=stored['brier'];counts[key]+=1;checks+=1
    for r in summary['results']:
        key=r['seed'],r['group'],r['recipe'];assert counts[key]==r['pair_points'];assert abs(totals[key]/counts[key]-r['brier'])<1e-9
    # Refit representative primary-fold recipes after changing evaluation-game labels.
    fold=designs[20261013,0];train=set(fold['fit_matches']);test=set(fold['evaluation_matches'])
    train_pairs=np.array([(a,b) for a,b in itertools.combinations(sorted(features),2)
        if features[a]['phase']==features[b]['phase'] and features[a]['match_id'] in train and features[b]['match_id'] in train
        and not data['teams'][a]&data['teams'][b]],dtype=int)
    altered=labels.copy()
    for i,f in features.items():
        if f['match_id'] in test:altered[i]=(altered[i]+1)%3
    for recipe in ['calibrated_market','calibrated_blend']:
        selected=choose(p,m,altered,train_pairs,FAMILIES[recipe])[0]
        assert list(selected)==params[20261013,0,'all_active',recipe]['params']
    assert data['seal']==summary['holdout_before_after']==protocol['holdout_before']
    result={'status':'passed','visible_prefix_mutation_checks':len(features),'independent_scalar_recipe_pair_scores':checks,
        'evaluation_label_mutation_refits_checked':2,'aggregate_rows_checked':len(summary['results']),
        'source_hashes_verified':True,'whole_game_fit_evaluation_isolation':True,'holdout_unchanged':True}
    (OUT/'verification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))


if __name__=='__main__':main()
