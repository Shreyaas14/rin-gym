"""Offline integrity audit of the frozen dataset, outputs, and budget."""
from collections import Counter
from pathlib import Path
import csv
import json
import sqlite3
from evaluate import ROOT,verify,valid_answers
from budget import Budget
from features import identity

verify();pairs=json.loads((ROOT/'pairs.json').read_text())
assert len(pairs)==400 and len({r['id'] for r in pairs})==400
assert len({(r['a']['id'],r['b']['id'],r['b']['selected_outcome']) for r in pairs})==400
selection=json.loads((ROOT/'selection.json').read_text())
assert len({p['event_group'] for p in pairs})==selection['heldout_event_groups']
assert not {p['event_group'] for p in pairs}&set(selection['old_k_event_groups_excluded'])
assert all(p['split']=='heldout' and p['b']['selected_token_id'] for p in pairs)
with (ROOT/'review-blind.csv').open(newline='') as f:reviews=list(csv.DictReader(f))
assert len(reviews)==400 and {r['pair_id'] for r in reviews}=={p['id'] for p in pairs}
assert all(not r['identity_label'] and not r['settlement_label'] for r in reviews)
assert not {'prediction','identity_gold','heuristic_decision'}&set(reviews[0])
count=Counter()
for path in (ROOT/'responses').glob('*/*.json'):
    r=json.loads(path.read_text());valid_answers(r['response']);count[path.parent.name]+=1
b=Budget(ROOT/'budget.sqlite');assert b.summary()['accounted_upper_usd']<=5
with b.connect() as db:
    reservations={r['id']:dict(r) for r in db.execute('SELECT * FROM calls')}
for path in (ROOT/'responses').glob('*/*.json'):
    r=json.loads(path.read_text());assert r['reservation'] in reservations
    assert reservations[r['reservation']]['payload_hash']==r['payload_sha256']
print(json.dumps({'integrity':'PASS','pairs':400,'event_groups':selection['heldout_event_groups'],
                  'response_counts':dict(count),'budget':b.summary()['accounted_upper_usd'],
                  'reference_labels':'pending independent review'}))
