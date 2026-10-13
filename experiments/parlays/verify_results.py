"""Verify published parlay evidence offline with Python's standard library only."""
from collections import defaultdict
import gzip
import hashlib
import itertools
import json
import math
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
DATA=ROOT/'artifacts/parlay-evals'
SIDES=('home','draw','away')
KEYS=tuple(a+'_'+b for a,b in itertools.product(SIDES,repeat=2))


def read(path):
    if not path.exists():path=path.with_name(path.name+'.gz')
    raw=path.read_bytes()
    return gzip.decompress(raw) if path.suffix=='.gz' else raw


def document(folder,name):return json.loads(read(DATA/folder/name))


def lines(folder,name):return [json.loads(line) for line in read(DATA/folder/name).decode().splitlines()]


def parse(text,joint=False):
    def unique(items):
        d={}
        for k,v in items:
            if k in d:raise ValueError('Duplicate key')
            d[k]=v
        return d
    try:
        obj=json.loads(text,object_pairs_hook=unique);field='joint' if joint else 'probs';keys=KEYS if joint else SIDES
        if set(obj)!={field} or set(obj[field])!=set(keys):return None
        p=obj[field]
        if any(type(v) not in (float,int) or not math.isfinite(v) or not 0<=v<=1 for v in p.values()):return None
        total=math.fsum(p.values())
        if total<=0 or abs(total-1)>.02:return None
        return {k:p[k]/total for k in keys}
    except (ValueError,TypeError,KeyError):return None


def product(a,b):return None if a is None or b is None else {x+'_'+y:a[x]*b[y] for x,y in itertools.product(SIDES,repeat=2)}


def brier(p,actual):return 2. if p is None else math.fsum((p[k]-float(k==actual))**2 for k in KEYS)


def check_receipts(folder):
    predictions=lines(folder,'predictions.jsonl');requests=lines(folder,'requests.jsonl');p=document(folder,'protocol.json')
    assert len(predictions)==len(requests)==p['maximum_requests']
    for r in predictions:
        parsed=None if r['completion'] is None or r['truncated'] else parse(r['completion'],r.get('arm')=='joint')
        assert parsed==r['probs']
    assert sum(r['reserved_max_cost_usd'] for r in requests)<p['request_reservation_ceiling_usd']<p['budget_usd']
    assert document(folder,'summary.json')['holdout_before_after']==p['holdout_before']
    return predictions


def main():
    manifest=document('','snapshot-manifest.json')
    for r in manifest['files']:
        raw=(DATA/r['published_path']).read_bytes()
        assert hashlib.sha256(raw).hexdigest()==r['published_sha256'],r['published_path']
        original=gzip.decompress(raw) if r['gzip_wrapped'] else raw
        assert hashlib.sha256(original).hexdigest()==r['original_sha256'],r['published_path']
    folder='parlay-eval-fresh-joint'
    preds=check_receipts(folder);at={(r['case_id'],r['repeat'],r['arm']):r for r in preds}
    totals=defaultdict(list);checks=0
    for c in document(folder,'cases.json'):
        actual='_'.join(c['evaluation']['results'])
        for rep in range(3):
            joint=at[c['id'],rep,'joint']['probs'];a=at[c['id'],rep,'single_a']['probs'];b=at[c['id'],rep,'single_b']['probs']
            values={'joint':joint,'single_product':product(a,b),'market':product(*c['evaluation']['market_probs'])}
            if joint:
                ma={x:sum(joint[x+'_'+y] for y in SIDES) for x in SIDES};mb={y:sum(joint[x+'_'+y] for x in SIDES) for y in SIDES}
                values['joint_marginal_product']=product(ma,mb)
            else:values['joint_marginal_product']=None
            for name,p in values.items():totals[name].append(brier(p,actual));checks+=1
    summary=document(folder,'summary.json')
    for name,values in totals.items():assert abs(sum(values)/len(values)-summary['results'][name]['brier'])<1e-10
    folder='parlay-eval-fresh-singles-expanded'
    preds=check_receipts(folder);at={(r['observation_id'],r['repeat']):r for r in preds}
    points=document(folder,'observations.json');pairs=document(folder,'pairs.json');scored=document(folder,'scored-pairs.json')
    assert len(pairs)==len(scored)==2170
    phase_totals=defaultdict(list)
    for pair,row in zip(pairs,scored):
        assert row['indices']==pair['indices'];a,b=[points[i] for i in pair['indices']]
        assert a['visible_score'][0]==a['visible_score'][1] and b['visible_score'][0]==b['visible_score'][1]
        assert a['phase']==b['phase'] and a['match_id']!=b['match_id']
        actual=a['evaluation']['result']+'_'+b['evaluation']['result']
        for name in ['single_product','market']:
            values=[]
            for rep in range(3):
                p=product(at[a['id'],rep]['probs'],at[b['id'],rep]['probs']) if name=='single_product' else product(a['evaluation']['market_probs'],b['evaluation']['market_probs'])
                loss=brier(p,actual);assert abs(loss-row['repeat_scores'][rep][name]['brier'])<1e-10
                values.append(loss);checks+=1
            if not pair['in_original_24']:phase_totals[name,pair['phase']].append(sum(values)/len(values))
    summary=document(folder,'summary.json');phases=sorted({p['phase'] for p in points})
    for name in ['single_product','market']:
        result=sum(sum(phase_totals[name,phase])/len(phase_totals[name,phase]) for phase in phases)/len(phases)
        assert abs(result-summary['aggregates']['additional_primary']['equal_phase_metrics'][name]['brier'])<1e-10
    print(json.dumps({'status':'passed','snapshot_files_verified':len(manifest['files']),
        'independent_fresh_forecast_score_checks':checks,'paid_receipts_verified':216+396,
        'published_holdout_guard_snapshots_match':True,'network_calls':0},indent=2))


if __name__=='__main__':main()
