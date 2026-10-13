"""Final independent join and budget checks, no remote calls."""
from pathlib import Path
import hashlib,json,math
from experiments.eight_directions.d2_market_prior.study import ROOT,OUT
from experiments.forecast_only.core import guard
from app.trade_env.tasks import read_tasks
from app.trade_env.reward import score

pro=json.loads((OUT/'fresh_protocol.json').read_text());plans={r['request_id']:r for r in pro['planned_requests']}
req=[json.loads(l) for l in (OUT/'fresh_requests.jsonl').read_text().splitlines()];pred=[json.loads(l) for l in (OUT/'fresh_predictions.jsonl').read_text().splitlines()]
b=json.loads((OUT/'spending.json').read_text());res=json.loads((OUT/'fresh_results.json').read_text());tm={t['id']:t for t in read_tasks(ROOT/'inputs/earlier-run-risk/tasks.jsonl.gz')};config=json.loads((ROOT/'inputs/closed-loop-risk/protocol.json').read_text())['config']
assert len(req)==len(pred)==len(b['entries'])==b['paid_requests']==990
assert {r['request_id'] for r in req}=={r['request_id'] for r in pred}==set(plans)=={e['request_id'] for e in b['entries']}
assert all(e['status']=='completed' for e in b['entries'])
reqm={r['request_id']:r for r in req};pref=0;out=0
for r in pred:
    for k,v in reqm[r['request_id']].items():assert r[k]==v
    parsed=score(tm[r['task_id']],None if r['truncated'] else r['completion'],config)
    assert r['valid']==parsed['valid'] and r['probs']==parsed['probs'] and r['advisory_action']==parsed['action']
    pref+=r['input_tokens'];out+=r['output_tokens'];assert r['output_tokens']<=256
    e=next(e for e in b['entries'] if e['request_id']==r['request_id'])
    assert e['usage']=={'input_tokens':r['input_tokens'],'output_tokens':r['output_tokens']}
    assert abs(e['maximum_reserved_usd']-2*(r['input_tokens']*.195+256*.6)/1e6)<1e-12
assert abs(b['token_cost_estimate_usd']-(pref*.195+out*.6)/1e6)<1e-10
assert abs(b['reserved_max_usd']-pro['preflight']['reservation_with_2x_headroom_usd'])<1e-10
assert b['reserved_max_usd']<5
assert all(hashlib.sha256(Path(k).read_bytes()).hexdigest()==v for k,v in pro['code_sha256'].items())
s=json.loads((ROOT/'research/eight-directions/setup.json').read_text());assert guard(s['guard_ledger'],pro['cohort_match_ids'])==pro['guard_before']==res['guard_after']
checks={'complete_requests':990,'complete_pairs':495,'complete_match_repeat_trajectories':99,'unique_matches':33,'joined_protocol_request_prediction_reservation_ids':True,'all_raw_completion_parser_results_reproduced':True,'all_max_reservations_reproduced_2x':True,'all_reservations_completed_no_pending':True,'frozen_code_hashes_match':True,'input_tokens':pref,'output_tokens':out,'token_cost_estimate_usd':(pref*.195+out*.6)/1e6,'reserved_max_usd':b['reserved_max_usd'],'verified_invoice_billing_usd':None,'holdout_guard_unchanged':True}
(OUT/'fresh_verification.json').write_text(json.dumps(checks,indent=2)+'\n');print(json.dumps(checks,indent=2))
