"""Verify live receipts, hidden-label isolation, budget bounds and independent scores."""
import hashlib
import itertools
import json
import math

import numpy as np
from experiment import OUT, ROOT, REPO, SIDES, KEYS, LEDGER, guard, digest, parse, observation, read_tasks


def main():
    p=json.loads((OUT/'protocol.json').read_text());cases=json.loads((OUT/'cases.json').read_text())
    checksum=p.pop('sha256');assert digest(p)==checksum;p['sha256']=checksum
    assert digest(cases)==p['cases_sha256']
    for path,sha in p['source_hashes'].items():assert hashlib.sha256((ROOT/path).read_bytes()).hexdigest()==sha
    tasks={(t['match_id'],t['decision']):t for t in read_tasks(REPO/'artifacts/earlier-run-risk/tasks.jsonl.gz') if t['position']=='flat'}
    mids=[mid for c in cases for mid in c['match_ids']];assert len(mids)==len(set(mids))==2*len(cases)
    for c in cases:
        a,b=[tasks[mid,c['phase']] for mid in c['match_ids']]
        assert all(t['split']=='val' and not t['demo'] and t['scoring']['observed_score']['home']==t['scoring']['observed_score']['away'] for t in [a,b])
        assert c['evaluation']['results']==[a['scoring']['result'],b['scoring']['result']]
        assert c['evaluation']['market_probs']==[a['scoring']['market_probs'],b['scoring']['market_probs']]
        for arm in p['arms']:
            assert observation(a,b,arm)==c['messages'][arm]
            assert digest(c['messages'][arm])==c['observation_sha256'][arm]
    req=[json.loads(line) for line in (OUT/'requests.jsonl').read_text().splitlines()]
    pred=[json.loads(line) for line in (OUT/'predictions.jsonl').read_text().splitlines()]
    assert len(req)==len(pred)==p['maximum_requests']
    key=lambda r:(r['case_id'],r['repeat'],r['arm'])
    qr={key(r):r for r in req};pr={key(r):r for r in pred}
    assert len(qr)==len(pr)==len(req) and set(qr)==set(pr)
    caseat={c['id']:c for c in cases}
    for k,r in pr.items():
        c=caseat[r['case_id']]
        for name in ['input_tokens','seed','protocol_sha256','observation_sha256','reserved_max_cost_usd']:
            assert r[name]==qr[k][name]
        assert r['protocol_sha256']==checksum
        assert r['observation_sha256']==c['observation_sha256'][r['arm']]
        assert r['input_tokens']==c['prompt_tokens'][r['arm']]
        if r['output_tokens'] is not None:assert 0<=r['output_tokens']<=p['max_tokens']
        assert r['probs']==(None if r['truncated'] or r['completion'] is None else parse(r['completion'],r['arm']))
    scored=json.loads((OUT/'scored-cases.json').read_text());summary=json.loads((OUT/'summary.json').read_text())
    assert len(scored)==len(cases)*p['repeats']
    totals={name:[] for name in summary['results']};checks=0
    for row in scored:
        c=caseat[row['case_id']];rep=row['repeat'];truth=tuple(c['evaluation']['results'])
        a=pr[c['id'],rep,'single_a']['probs'];b=pr[c['id'],rep,'single_b']['probs'];j=pr[c['id'],rep,'joint']['probs']
        market=c['evaluation']['market_probs']
        distributions={'joint':j,'market':{x+'_'+y:market[0][x]*market[1][y] for x,y in itertools.product(SIDES,repeat=2)},
            'single_product':None if a is None or b is None else {x+'_'+y:a[x]*b[y] for x,y in itertools.product(SIDES,repeat=2)}}
        if j:
            am={x:sum(j[x+'_'+y] for y in SIDES) for x in SIDES};bm={y:sum(j[x+'_'+y] for x in SIDES) for y in SIDES}
            distributions['joint_marginal_product']={x+'_'+y:am[x]*bm[y] for x,y in itertools.product(SIDES,repeat=2)}
        else:distributions['joint_marginal_product']=None
        for recipe,probs in distributions.items():
            if probs is None:bs=2.;ll=-math.log(1e-6)
            else:
                assert abs(sum(probs.values())-1)<1e-9
                actual='_'.join(truth)
                bs=sum((v-float(k==actual))**2 for k,v in probs.items());ll=-math.log(max(probs[actual],1e-6))
            assert abs(bs-row['metrics'][recipe]['brier'])<1e-10
            assert abs(ll-row['metrics'][recipe]['log_loss'])<1e-10
            totals[recipe].append(bs);checks+=1
    for recipe,values in totals.items():assert abs(np.mean(values)-summary['results'][recipe]['brier'])<1e-10
    upper=sum(r['reserved_max_cost_usd'] for r in req)
    assert upper<=p['request_reservation_ceiling_usd']<p['budget_usd']
    assert abs(upper-p['max_token_cost_estimate_usd'])<1e-10
    assert summary['usage']['full_rate_token_cost_estimate_usd']<=upper
    assert guard(LEDGER,mids)==p['holdout_before']==summary['holdout_before_after']
    result={'status':'passed','receipt_pairs':len(req),'independently_recomputed_forecast_scores':checks,
        'source_hashes_verified':True,'cohort_disjoint_games':len(mids),'safe_messages_match_frozen_source_tasks':True,
        'token_budget_verified':True,'holdout_unchanged':True}
    (OUT/'verification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))


if __name__=='__main__':main()
