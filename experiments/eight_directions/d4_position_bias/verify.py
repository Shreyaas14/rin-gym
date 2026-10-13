"""Independent receipt invariants and meaningful matched-analysis checks."""
from collections import defaultdict
import hashlib
import json
from pathlib import Path
from experiments.eight_directions.d4_position_bias.study import ROOT, OUT, ARMS, load, canonical, parse_response, write
from experiments.eight_directions.d4_position_bias.analyze import summarize
from experiments.forecast_only.core import digest, guard

def test_analysis(tm,config):
    t=next(t for t in tm.values() if t['position']=='flat');flat={'home':.4,'draw':.3,'away':.3};rows=[]
    for arm in ARMS:
        p=flat.copy()
        if arm!='flat':
            p={s:v+(.1 if s==arm else -.05) for s,v in p.items()}
        rows.append({'match_id':t['match_id'],'observed_t':t['t'],'repeat':0,'arm':arm,'task_id':t['id'],'probs':p,'valid':True,'probability_valid':True})
    summary=summarize(rows,tm,'valid')
    assert abs(summary['metrics']['primary_held_uplift']['mean']-.1)<1e-12
    assert summary['complete_sets']==1
    rows[1]['valid']=False
    assert summarize(rows,tm,'valid')['complete_sets']==0
    assert summarize(rows,tm,'probability_valid')['complete_sets']==1
    valid=json.dumps({'probs':flat,'next_goal':{'home':.1,'away':.1,'none':.8},'future_prices':flat,'action':'do_nothing','reason':'Wait.'})
    assert parse_response(valid,False,t,config)['valid']
    assert not parse_response(valid,True,t,config)['probability_valid']
    bad=json.loads(valid);bad['action']='keep';bad=json.dumps(bad)
    assert not parse_response(bad,False,t,config)['valid']
    assert parse_response(bad,False,t,config)['probability_valid']
    return ['known 0.10 matched held uplift recovered','invalid four-arm set excluded','probability sensitivity retains invalid action forecast','truncation invalidates probability','legal action validated']

def main():
    setup,ids,tasks=load();tm={t['id']:t for t in tasks};p=json.loads((OUT/'protocol.json').read_text())
    assert digest({k:v for k,v in p.items() if k!='sha256'})==p['sha256']
    for filename,checksum in p['code_sha256'].items():assert hashlib.sha256((ROOT/filename).read_bytes()).hexdigest()==checksum
    assert hashlib.sha256((ROOT/'inputs/earlier-run-risk/tasks.jsonl.gz').read_bytes()).hexdigest()==p['source_sha256']
    expected=[json.loads(l) for l in (OUT/'prepared_requests.jsonl').read_text().splitlines()]
    requests=[json.loads(l) for l in (OUT/'requests.jsonl').read_text().splitlines()] if (OUT/'requests.jsonl').exists() else []
    predictions=[json.loads(l) for l in (OUT/'predictions.jsonl').read_text().splitlines()] if (OUT/'predictions.jsonl').exists() else []
    ex={r['request_id']:r for r in expected};at={r['request_id']:r for r in requests};pred={r['request_id']:r for r in predictions}
    assert len(at)==len(requests);assert len(pred)==len(predictions)
    assert set(pred)<=set(at)<=set(ex)
    groups=defaultdict(list)
    for r in expected:
        assert r['messages']==tm[r['task_id']]['messages'];assert digest(r['messages'])==r['observation_sha256'];assert digest(canonical(r['messages']))==r['common_observation_sha256']
        groups[(r['match_id'],r['observed_t'],r['repeat'])].append(r)
    for group in groups.values():assert len(group)==4 and {r['arm'] for r in group}==set(ARMS) and len({r['seed'] for r in group})==1 and len({r['common_observation_sha256'] for r in group})==1
    for rid,r in at.items():
        assert all(r[k]==v for k,v in ex[rid].items());assert r['protocol_sha256']==p['sha256']
    for rid,r in pred.items():
        assert all(r[k]==v for k,v in at[rid].items())
        actual=parse_response(r['completion'],r['truncated'],tm[r['task_id']],p['config'])
        assert all(r[k]==v for k,v in actual.items());assert len(r['raw_tokens'])==r['output_tokens']<=256
    spending=json.loads((OUT/'spending.json').read_text());entries={e['request_id']:e for e in spending['entries']}
    assert len(entries)==len(spending['entries']);assert set(entries)==set(at)
    assert spending['reserved_max_usd']<=4.5
    assert abs(sum(e['maximum_reserved_usd'] for e in entries.values())-spending['reserved_max_usd'])<1e-9
    assert sum(e['status']=='completed' for e in entries.values())==len(predictions)==spending['paid_requests']
    assert abs(sum((r['input_tokens']*.195+r['output_tokens']*.60)/1e6 for r in predictions)-spending['token_cost_estimate_usd'])<1e-9
    after=guard(setup['guard_ledger'],ids);assert after==p['seal_before']
    result={'status':'passed','checks':test_analysis(tm,p['config'])+['protocol/source/code hashes intact','messages and common observed state invariant','four arms and paired seeds per set','unique request receipts and no duplicate requests','raw completion parse and token counts verified','durable spending reconciles','holdout seal intact'],'maximum_requests':len(expected),'requests':len(requests),'completed':len(predictions),'complete':len(predictions)==len(expected),'seal_after':after}
    write('verification.json',result);print(json.dumps(result,indent=2))

if __name__=='__main__':main()
