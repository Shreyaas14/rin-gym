"""Evaluate frozen real contracts with heuristics, raw Jev, and a hybrid."""
import argparse
from collections import Counter,defaultdict
import concurrent.futures
import hashlib
import importlib.util
import json
from pathlib import Path
import statistics
import time

from features import features,identity,retrieval_score,settlement

ROOT=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('synthetic_transport',ROOT.parent/'market-entity-resolution'/'run.py')
# The sibling script imports cases; add its directory only for transport reuse.
import sys
sys.path.insert(0,str(ROOT.parent/'market-entity-resolution'))
transport=importlib.util.module_from_spec(spec);spec.loader.exec_module(transport)

QUESTIONS={
    'identity':{
        'type':'choice',
        'instructions':'Compare Kalshi YES with the explicitly selected Polymarket outcome. Judge market identity: same advertised event, observation period/instant, participant and requested outcome/threshold bucket. Sources, aggregation methods, rounding and exceptional settlement behavior are assessed separately in settlement_compatibility; those alone do not prevent nominal identity. Same advertised 25bps Fed action is an identity match even if nonstandard changes are rounded differently. For large Fed moves, use explicitly documented upward 25bps rounding to compare >25 with a 50+ bracket. For one-decimal published CPI, >3.9 equals >=4.0. A sports winner is not a spread or total, and the opponent outcome is not the selected outcome. Different observation times, meetings or thresholds are nonmatches. Do not use trading close or expiration timestamps as a proxy for the actual observation window. Do not assume first Polymarket outcome means YES. Read actual selected_outcome and the corresponding rules.',
        'criteria':{
            'match':'Same advertised market identity after explicit period, alias, unit or reported-precision normalization, with payout caveats considered separately.',
            'nonmatch':'Different advertised event, date/instant, participant, requested side, indicator, bet type or threshold/bucket.',
            'insufficient':'Supplied terms do not establish the advertised identity either way.'
        }
    },
    'settlement_compatibility':{
        'type':'choice',
        'instructions':'Compare the payout of Kalshi YES with the selected Polymarket outcome under the full supplied terms. Look for an explicit possible scenario where payouts differ: sources, index versus candle, temporal aggregation, strict/inclusive threshold, rounding, observation times, delays, cancellation, fallback data, or side mapping. Distinguish a nominal identity match from payout equivalence. Ignore platform governance differences alone. Choose unknown rather than compatible if source, role definitions or material contingencies are unresolved. Examples appearing in a generic Kalshi contract PDF are illustrations, not extra markets or bound parameter values. Market-specific fields instantiate the generic PDF parameters. Never infer equivalent payout merely from matching headlines.',
        'criteria':{
            'compatible':'Supplied settlement rules establish equivalent payouts for the compared outcomes, with no material counterexample or missing relevant term.',
            'different':'Explicit stated conditions permit a scenario in which the compared outcomes pay differently.',
            'unknown':'A complete compatibility conclusion cannot be established from the supplied terms.'
        }
    }
}


def public_state(m):
    result={k:m[k] for k in ('venue','id','title','selected_outcome','outcomes','rules')}
    for field in ('selected_outcome_description','contract_terms_text','contract_terms_url','settlement_sources','resolution_source'):
        if field in m:result[field]=m[field]
    raw=m['raw']
    # These are raw source fields, not investigator annotations or label rationale.
    result['source_metadata']={k:raw[k] for k in ('event_ticker','slug','groupItemTitle','groupItemThreshold','sportsMarketType','gameStartTime','open_time','close_time','endDate','floor_strike','cap_strike','strike_type') if k in raw}
    return result


def make_payload(pair,hybrid=False):
    state={'contract_a':public_state(pair['a']),'contract_b':public_state(pair['b'])}
    if hybrid:
        state['normalized_source_facts']={'contract_a':features(pair['a']),'contract_b':features(pair['b'])}
        state['normalized_source_facts_note']='Deterministic parsing of public source fields. Unsupported or null values do not establish identity or compatibility. Raw rules remain authoritative.'
    return {'model':'jev-latest','state':state,'questions':QUESTIONS}


def decide(answer,positive,negative):
    p=answer['probabilities']
    if p.get(positive,0)>=0.99:return positive
    if p.get(negative,0)>=0.99:return negative
    return 'review'


def identity_metrics(pairs,predictions):
    tp=sum(p['identity_gold']=='match' and predictions[p['id']]['identity']=='match' for p in pairs)
    fp=sum(p['identity_gold']!='match' and predictions[p['id']]['identity']=='match' for p in pairs)
    fn=sum(p['identity_gold']=='match' and predictions[p['id']]['identity']!='match' for p in pairs)
    review=sum(predictions[p['id']]['identity']=='review' for p in pairs)
    correct=sum(predictions[p['id']]['identity']==p['identity_gold'] for p in pairs)
    return {'true_matches':tp,'false_matches':fp,'missed_matches':fn,'precision':tp/(tp+fp) if tp+fp else None,'recall':tp/(tp+fn) if tp+fn else None,'reviews':review,'review_rate':review/len(pairs),'correct_nonreview_decisions':correct,'decision_coverage':1-review/len(pairs)}


def settlement_metrics(pairs,predictions):
    known=[p for p in pairs if p['settlement_gold']=='different']
    unknown=[p for p in pairs if p['settlement_gold']=='unknown']
    return {
        'documented_conflicts':len(known),
        'conflicts_detected':sum(predictions[p['id']]['settlement']=='different' for p in known),
        'false_compatible_accepts':sum(predictions[p['id']]['settlement']=='compatible' for p in known),
        'unknown_reference_pairs':len(unknown),
        'unsupported_compatible_accepts_on_unknown':sum(predictions[p['id']]['settlement']=='compatible' for p in unknown),
        'compatible_accepts':sum(predictions[p['id']]['settlement']=='compatible' for p in pairs),
        'strict_equivalence_recall':None,
        'note':'No established compatible reference pairs; strict equivalence recall cannot be estimated. Different predictions on unknown references are unadjudicated, not automatically errors.'
    }


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--env-file');parser.add_argument('--offline',action='store_true');args=parser.parse_args()
    frozen=json.loads((ROOT/'freeze.json').read_text())
    for name,expected in frozen['file_sha256'].items():
        assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==expected,'Frozen artifact changed: '+name
    pairs=json.loads((ROOT/'reference.json').read_text());pool=json.loads((ROOT/'markets.json').read_text())['polymarket']
    heuristics={};durations=[];retrieval=[]
    for pair in pairs:
        started=time.perf_counter();ident,reason=identity(pair['a'],pair['b']);settle,settle_reason=settlement(pair['a'],pair['b'],ident)
        heuristics[pair['id']]={'identity':ident,'settlement':settle,'identity_reason':reason,'settlement_reason':settle_reason,'a_features':features(pair['a']),'b_features':features(pair['b'])}
        durations.append(time.perf_counter()-started)
        if pair['identity_gold']=='match':
            ranked=sorted(pool,key=lambda m:(-retrieval_score(pair['a'],m),m['id']))
            rank=next(i+1 for i,m in enumerate(ranked) if m['id']==pair['b']['id'])
            retrieval.append({'pair_id':pair['id'],'target_poly_id':pair['b']['id'],'rank':rank,'in_top5':rank<=5,'top5_ids':[m['id'] for m in ranked[:5]]})
    (ROOT/'heuristic-results.json').write_text(json.dumps(heuristics,indent=2)+'\n')
    (ROOT/'retrieval-results.json').write_text(json.dumps(retrieval,indent=2)+'\n')
    result={'identity':{'heuristics':identity_metrics(pairs,heuristics)},'settlement':{'heuristics':settlement_metrics(pairs,heuristics)},'retrieval':{'reference_positives':len(retrieval),'top5_hits':sum(r['in_top5'] for r in retrieval),'reference_positive_recall_at5':sum(r['in_top5'] for r in retrieval)/len(retrieval),'scope':'Conditional on bounded frozen catalogs and investigator-labeled positives; not exhaustive catalog-wide recall.'},'heuristic_median_local_seconds':statistics.median(durations)}
    if args.offline:
        (ROOT/'offline-metrics.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2));return
    if not args.env_file:raise SystemExit('--env-file is required for the approved live experiment')
    key=transport.load_key(args.env_file)
    if not key:raise SystemExit('TYPESAFE_API_KEY missing')
    metadata={'questions':QUESTIONS,'requested_model':'jev-latest','threshold':0.99,'freeze_sha256':hashlib.sha256((ROOT/'freeze.json').read_bytes()).hexdigest()}
    path=ROOT/'evaluation-metadata.json'
    if path.exists():assert json.loads(path.read_text())==metadata,'Model configuration changed'
    path.write_text(json.dumps(metadata,indent=2)+'\n')
    jobs=[]
    for pair in pairs:
        jobs.append(('jev',pair))
        h=heuristics[pair['id']]
        if h['identity']=='review' or (h['identity']=='match' and h['settlement']=='unknown'):
            jobs.append(('hybrid',pair))
    def call(job):
        method,pair=job;payload=make_payload(pair,method=='hybrid');digest=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
        target=ROOT/'responses'/method/(pair['id']+'.json');target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists():
            data=json.loads(target.read_text());assert data['payload_sha256']==digest
            return data
        response,elapsed=transport.request_json('/v1/systemone',key,payload)
        data={'method':method,'pair_id':pair['id'],'payload_sha256':digest,'response':response,'elapsed_seconds':elapsed}
        target.write_text(json.dumps(data,indent=2)+'\n');return data
    responses={};errors=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures={executor.submit(call,job):job for job in jobs}
        for future in concurrent.futures.as_completed(futures):
            method,pair=futures[future]
            try:
                row=future.result();responses[(method,pair['id'])]=row
                ans=row['response']['answers']
                print(json.dumps({'completed':len(responses),'total':len(jobs),'method':method,'pair_id':pair['id'],'identity':ans['identity']['choice'],'p_match':ans['identity']['probabilities']['match'],'settlement':ans['settlement_compatibility']['choice']}),flush=True)
            except RuntimeError as exc:
                errors.append({'method':method,'pair_id':pair['id'],'error':str(exc)})
                print(json.dumps(errors[-1]),flush=True)
    if errors:
        (ROOT/'errors.json').write_text(json.dumps(errors,indent=2)+'\n')
        raise SystemExit('Some inference calls failed; rerun to resume completed responses')
    predictions={'heuristics':heuristics,'jev':{},'hybrid':{}}
    for pair in pairs:
        pid=pair['id'];answers=responses[('jev',pid)]['response']['answers']
        predictions['jev'][pid]={'identity':decide(answers['identity'],'match','nonmatch'),'settlement':decide(answers['settlement_compatibility'],'compatible','different')}
        if predictions['jev'][pid]['settlement']=='review':predictions['jev'][pid]['settlement']='unknown'
        h=heuristics[pid];hybrid=dict(h)
        if ('hybrid',pid) in responses:
            ans=responses[('hybrid',pid)]['response']['answers']
            if h['identity']=='review':hybrid['identity']=decide(ans['identity'],'match','nonmatch')
            if h['settlement']=='unknown':
                hybrid['settlement']=decide(ans['settlement_compatibility'],'compatible','different')
                if hybrid['settlement']=='review':hybrid['settlement']='unknown'
        predictions['hybrid'][pid]=hybrid
    for method in ('jev','hybrid'):
        result['identity'][method]=identity_metrics(pairs,predictions[method]);result['settlement'][method]=settlement_metrics(pairs,predictions[method])
    result['per_family']={}
    for family in sorted({p['family'] for p in pairs}):
        subset=[p for p in pairs if p['family']==family]
        result['per_family'][family]={'pairs':len(subset),'positive_identity_pairs':sum(p['identity_gold']=='match' for p in subset),'identity':{m:identity_metrics(subset,predictions[m]) for m in predictions}}
    result['model_versions']=sorted({r['response']['model'] for r in responses.values()})
    result['model_calls']={method:{'successful_calls':sum(m==method for m,p in responses),'median_end_to_end_seconds':statistics.median(r['elapsed_seconds'] for (m,p),r in responses.items() if m==method),'input_tokens':sum(r['response']['usage']['input_tokens'] for (m,p),r in responses.items() if m==method),'output_tokens':sum(r['response']['usage']['output_tokens'] for (m,p),r in responses.items() if m==method)} for method in ('jev','hybrid')}
    result['labeling_limitations']='60 convenience-selected real pairs; investigator-authored labels before inference, not independent adjudication; repeated contract families are correlated; no established strict-compatible positives; no deployment prevalence estimate.'
    (ROOT/'predictions.json').write_text(json.dumps(predictions,indent=2)+'\n')
    (ROOT/'metrics.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='per_family'},indent=2))


if __name__=='__main__':main()
