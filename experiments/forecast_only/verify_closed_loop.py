"""Audit completed fresh replay receipts against executed state and published limits."""
import hashlib
import json
from pathlib import Path

from app.trade_env.replay import by_match, Replay
from app.trade_env.tasks import read_tasks
from experiments.forecast_only.closed_loop import OUT, SOURCE, LEDGER, observation, execute
from experiments.forecast_only.core import digest, guard


def verify():
    protocol=json.loads((OUT/'protocol.json').read_text())
    sha=protocol.pop('sha256');assert digest(protocol)==sha;protocol['sha256']=sha
    assert hashlib.sha256((SOURCE/'tasks.jsonl.gz').read_bytes()).hexdigest()==protocol['tasks_sha256']
    for name,checksum in protocol['code_sha256'].items():
        assert hashlib.sha256(Path(name).read_bytes()).hexdigest()==checksum
    tasks=read_tasks(SOURCE/'tasks.jsonl.gz');matches=by_match(tasks)
    predictions=[json.loads(x) for x in (OUT/'predictions.jsonl').read_text().splitlines()]
    requests=[json.loads(x) for x in (OUT/'requests.jsonl').read_text().splitlines()]
    results=[json.loads(x) for x in (OUT/'matches.jsonl').read_text().splitlines()]
    key=lambda r:(r['arm'],r['repeat'],r['task_id'])
    assert len(predictions)==len(requests)==protocol['maximum_requests']==2154
    assert len({key(r) for r in predictions})==len(predictions)
    assert {key(r) for r in requests}=={key(r) for r in predictions}
    assert len(results)==len({(r['arm'],r['repeat'],r['match_id']) for r in results})==432
    lookup={key(r):r for r in predictions}
    for result in results:
        replay=Replay(matches[result['match_id']],protocol['config'])
        while (task:=replay.next_task()) is not None:
            row=lookup[(result['arm'],result['repeat'],task['id'])]
            assert row['position_before']==replay.position
            assert row['quantity_before']==(0 if replay.position=='flat' else .25 if result['arm']=='conservative' else 1)
            assert row['observation_sha256']==digest(observation(task,result['arm']))
            out=execute(replay,None if row['truncated'] else row['completion'],result['arm'],protocol['config'])
            assert out['executed_action']==row['executed_action'] and out['next_position']==row['next_position']
        scale=.25 if result['arm']=='conservative' else 1
        assert abs(replay.result()['pnl']*scale-result['pnl'])<1e-9
        if result['arm']=='conservative':
            assert result['trades']<=1 and result['pnl']>=-.1625-1e-9
    seal=guard(LEDGER,matches);assert seal==protocol['seal']
    report={'passed':True,'verified_predictions':len(predictions),'verified_game_replays':len(results),
            'verified_unique_matches':len(matches),'verified_state_feedback':True,'verified_risk_limits':True,
            'ledger_unchanged':True,'source_hashes_match':True}
    (OUT/'verification.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))

if __name__=='__main__':verify()
