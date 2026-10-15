"""Persistent shared USD cap. Uncertain/failed requests retain full reservation."""
from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path
import json
import sqlite3
import uuid

CAP_NANOUSD = 5_000_000_000
# Rates: official published USD per 1M tokens, converted to nanodollars/token.
# Reserve the entire documented input context, not an optimistic token estimate.
PRICES = {
    'jev-1.13.0': {'input_nano':42,'output_nano':0,'max_input':65536,'max_output':0,
                  'source':'https://docs.typesafe.ai/models'},
    'gpt-4.1-mini-2025-04-14': {'input_nano':400,'output_nano':1600,
                             'max_input':1047576,'max_output':512,
                             'source':'https://developers.openai.com/api/docs/models/gpt-4.1-mini'},
}

class BudgetStop(RuntimeError): pass

class Budget:
    def __init__(self,path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS calls (id TEXT PRIMARY KEY, method TEXT, pair_id TEXT, model TEXT, payload_hash TEXT, reserved INTEGER, charged INTEGER, state TEXT, usage TEXT, created TEXT)')
            db.execute('INSERT OR IGNORE INTO meta VALUES (?,?)',('cap',str(CAP_NANOUSD)))
            if int(db.execute("SELECT value FROM meta WHERE key='cap'").fetchone()[0]) != CAP_NANOUSD:
                raise BudgetStop('Budget cap mismatch')

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=30)
        db.row_factory=sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def reserve(self,method,pair_id,model,payload_hash):
        price=PRICES[model]
        amount=price['max_input']*price['input_nano']+price['max_output']*price['output_nano']
        # 25% tariff/billing safety cushion, held until actual usage is known.
        amount=(amount*125+99)//100
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM meta WHERE key='halt'").fetchone():
                raise BudgetStop('Budget halted; manual investigation required')
            total=db.execute('SELECT COALESCE(SUM(charged),0) FROM calls').fetchone()[0]
            if total+amount > CAP_NANOUSD:
                raise BudgetStop('Shared $5 cap: insufficient unreserved funds')
            ident=str(uuid.uuid4())
            db.execute('INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?,?)',
                       (ident,method,pair_id,model,payload_hash,amount,amount,'reserved',None,
                        datetime.now(timezone.utc).isoformat()))
            return ident

    def settle(self,ident,input_tokens,output_tokens):
        if not isinstance(input_tokens,int) or not isinstance(output_tokens,int) or min(input_tokens,output_tokens)<0:
            raise BudgetStop('Missing/invalid usage: full reservation retained')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM calls WHERE id=?',(ident,)).fetchone()
            if row is None or row['state']!='reserved': raise BudgetStop('Invalid reservation state')
            price=PRICES[row['model']]
            actual=input_tokens*price['input_nano']+output_tokens*price['output_nano']
            if actual>row['reserved'] or input_tokens>price['max_input'] or (price['max_output'] and output_tokens>price['max_output']):
                db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',('halt','usage exceeded published bound'))
                db.execute('UPDATE calls SET state=? WHERE id=?',('bound_error',ident))
                db.commit()
                raise BudgetStop('Usage exceeded reservation; all further calls disabled')
            db.execute('UPDATE calls SET charged=?,state=?,usage=? WHERE id=?',
                       (actual,'reported',json.dumps({'input_tokens':input_tokens,'output_tokens':output_tokens}),ident))

    def reserve_fixed(self,method,pair_id,model,payload_hash,max_nano):
        if max_nano<=0:raise BudgetStop('Invalid fixed payment bound')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM meta WHERE key='halt'").fetchone():raise BudgetStop('Budget halted')
            total=db.execute('SELECT COALESCE(SUM(charged),0) FROM calls').fetchone()[0]
            if total+max_nano>CAP_NANOUSD:raise BudgetStop('Shared $5 cap: insufficient unreserved funds')
            ident=str(uuid.uuid4())
            db.execute('INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?,?)',
                       (ident,method,pair_id,model,payload_hash,max_nano,max_nano,'reserved',None,
                        datetime.now(timezone.utc).isoformat()))
            return ident

    def settle_fixed(self,ident,paid_nano,receipt):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM calls WHERE id=?',(ident,)).fetchone()
            if not row or row['state']!='reserved' or not isinstance(paid_nano,int) or not 0<=paid_nano<=row['reserved']:
                raise BudgetStop('Invalid payment receipt: reservation retained')
            db.execute('UPDATE calls SET charged=?,state=?,usage=? WHERE id=?',
                       (paid_nano,'reported',json.dumps({'payment_receipt':receipt}),ident))

    def summary(self):
        with self.connect() as db:
            rows=[dict(r) for r in db.execute('SELECT * FROM calls ORDER BY created')]
        reported=sum(r['charged'] for r in rows if r['state']=='reported')
        uncertain=sum(r['charged'] for r in rows if r['state']!='reported')
        return {'cap_usd':5.0,'reported_usage_cost_usd':reported/1e9,
                'uncertain_or_inflight_reserved_usd':uncertain/1e9,
                'accounted_upper_usd':(reported+uncertain)/1e9,
                'remaining_usd':(CAP_NANOUSD-reported-uncertain)/1e9,'attempts':len(rows),
                'reported_calls':sum(r['state']=='reported' for r in rows),
                'billing_note':'Computed from published rates and returned usage; not a provider invoice. Unknown requests retain worst-case cost plus 25% cushion.',
                'prices':PRICES}

    def export(self,target):
        Path(target).write_text(json.dumps(self.summary(),indent=2)+'\n')
