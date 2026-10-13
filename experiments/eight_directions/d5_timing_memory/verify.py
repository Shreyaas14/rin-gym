"""Offline checks of declaration, observed-only prompts and receipt linkage."""
import json, hashlib
from pathlib import Path
from .study import OUT,ROOT,SETUP,guard,digest,ARMS

def run():
    pro=json.loads((OUT/'protocol.json').read_text());setup=json.loads(SETUP.read_text());ids=pro['cohort_match_ids']
    assert ids==setup['chronological_replay_partition']['assessment_match_ids'][:8]
    assert pro['arms']==ARMS and len(ARMS)==3
    for p,sha in pro['code_sha256'].items():assert hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==sha,p
    observations=json.loads((OUT/'observations.json').read_text());plans=json.loads((OUT/'planned_requests.json').read_text());pre=json.loads((OUT/'preflight.json').read_text())
    rows={(r['match_id'],r['decision'],r['memory']):r for r in observations}
    checks=0
    for r in observations:
        user=r['messages'][1]['content'];current=user.split('\nCompact earlier observations (past only): ')[0];body=json.loads(current)
        assert set(body)=={'names','phase','score','totals','prices','recent_events'}
        assert all(e['kind']!='match_end' for e in body['recent_events'])
        assert r['event_cutoff']==r['t']-60
        if r['memory']:
            memories=json.loads(user.split('\nCompact earlier observations (past only): ')[1])
            assert len(memories)<=3 and all(m['t']<r['t'] for m in memories)
            assert all(set(m)=={'t','phase','score','totals','prices'} for m in memories)
            assert current==rows[r['match_id'],r['decision'],False]['messages'][1]['content']
        checks+=1
    for p in plans:
        memory=p['arm']=='sparse_memory';r=rows[p['match_id'],p['decision'],memory]
        assert p['observation_sha256']==digest(r['messages'])
        if p['arm'].startswith('sparse'):assert r['sparse']
    assert pre['calls']==len(plans)==1354
    assert abs(sum(p['maximum_reserved_usd'] for p in plans)-pre['reserved_2x_max_usd'])<1e-10
    assert pre['reserved_2x_max_usd']<4.5
    actual_ids=set()
    for read in json.loads((OUT/'selective_loading_receipts.json').read_text()):assert ['match_id','in',ids] in read['filters']
    if (OUT/'predictions.jsonl').exists():
        predictions=[json.loads(x) for x in (OUT/'predictions.jsonl').read_text().splitlines()];pp={p['request_id']:p for p in plans}
        assert len(predictions)==len({r['request_id'] for r in predictions})
        for r in predictions:
            for k,v in pp[r['request_id']].items():assert r[k]==v
            actual_ids.add(r['match_id'])
        assert actual_ids<=set(ids)
    after=guard(setup['guard_ledger'],ids);assert after==pro['guard_before']
    result={'verified':True,'prompt_rows_checked':checks,'planned_requests':len(plans),'completed_requests':len(predictions) if (OUT/'predictions.jsonl').exists() else 0,'actual_cohort_ids':sorted(actual_ids),'guard_identical':True,'guard':after,'note':'Structure verifies no outcomes/fills/prediction history in prompt; event cutoff implemented by existing state builder, source hashes fixed before live.'}
    (OUT/'verification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
if __name__=='__main__':run()
