"""Verify all fresh quarter-share requests, original-rule decisions and cash scaling."""
import hashlib
import json
from pathlib import Path

from app.trade_env.tasks import read_tasks
from app.trade_env.replay import by_match, Replay
from experiments.forecast_only.quarter_original import OUT, SOURCE, BASELINE, LEDGER, observation, QUANTITY
from experiments.forecast_only.closed_loop import execute
from experiments.forecast_only.core import digest, guard


def main():
    p=json.loads((OUT/'protocol.json').read_text());checksum=p.pop('sha256');assert digest(p)==checksum;p['sha256']=checksum
    assert hashlib.sha256((SOURCE/'tasks.jsonl.gz').read_bytes()).hexdigest()==p['tasks_sha256']
    for group in ['code_sha256','baseline_files_sha256']:
        for name,expected in p[group].items():assert hashlib.sha256(Path(name).read_bytes()).hexdigest()==expected
    grouped=by_match(read_tasks(SOURCE/'tasks.jsonl.gz'))
    predictions=[json.loads(x) for x in (OUT/'predictions.jsonl').read_text().splitlines()]
    requests=[json.loads(x) for x in (OUT/'requests.jsonl').read_text().splitlines()]
    results=[json.loads(x) for x in (OUT/'matches.jsonl').read_text().splitlines()]
    key=lambda r:(r['repeat'],r['task_id'])
    assert len(requests)==len(predictions)==len({key(r) for r in predictions})==p['maximum_requests']==1077
    assert {key(r) for r in requests}=={key(r) for r in predictions}
    assert len(results)==len({(r['repeat'],r['match_id']) for r in results})==216
    lookup={key(r):r for r in predictions}
    before={(r['repeat'],r['task_id'].rsplit(':',1)[0]):r for r in [json.loads(x) for x in (BASELINE/'predictions.jsonl').read_text().splitlines()] if r['arm']=='original'}
    for result in results:
        replay=Replay(grouped[result['match_id']],p['config'])
        while (task:=replay.next_task()) is not None:
            row=lookup[(result['repeat'],task['id'])]
            assert row['position_before']==replay.position and row['quantity_before']==(0 if replay.position=='flat' else QUANTITY)
            assert row['observation_sha256']==digest(observation(task))
            assert row['seed']==before[(row['repeat'],task['id'].rsplit(':',1)[0])]['seed']
            actual=execute(replay,None if row['truncated'] else row['completion'],'original',p['config'])
            assert actual['executed_action']==row['executed_action'] and actual['next_position']==row['next_position']
            assert actual['execution_rejection'] is None
        reconstructed=replay.result()
        assert abs(reconstructed['pnl']*QUANTITY-result['pnl'])<1e-9
        assert abs(reconstructed['cost']*QUANTITY-result['cost'])<1e-9
    seal=guard(LEDGER,grouped);assert seal==p['seal']
    result={'passed':True,'verified_fresh_predictions':len(predictions),'verified_game_replays':len(results),
        'unique_development_matches':len(grouped),'quantity_and_position_feedback_verified':True,
        'original_entry_exit_engine_verified':True,'same_seeds_as_baseline':True,
        'cashflows_scaled_by_quarter':True,'baseline_and_code_hashes_match':True,'holdout_ledger_unchanged':True}
    (OUT/'verification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))

if __name__=='__main__':main()
