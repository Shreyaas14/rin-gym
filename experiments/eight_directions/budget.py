"""Exclusive per-direction durable maximum-cost reservations; no automatic resume."""
import json,os
from pathlib import Path
from datetime import datetime,timezone

class Spending:
    def __init__(self,path):
        self.path=Path(path)
    def read(self):
        return json.loads(self.path.read_text())
    def write(self,s):
        tmp=self.path.with_suffix('.tmp')
        with tmp.open('w') as f:
            json.dump(s,f,indent=2,allow_nan=False);f.write('\n');f.flush();os.fsync(f.fileno())
        os.replace(tmp,self.path)
    def reserve(self,request_id,max_estimate,details):
        s=self.read()
        if not 0 < max_estimate:raise ValueError('Maximum estimate required')
        if any(e['request_id']==request_id for e in s['entries']):raise ValueError('Duplicate request; reconcile, never automatically restart')
        # Do not release successful maximum reservations during this study.
        if s['reserved_max_usd']+max_estimate > s['hard_limit_usd']-0.50:raise ValueError('Budget lacks uncertainty headroom')
        s['entries'].append({'request_id':request_id,'maximum_reserved_usd':max_estimate,'status':'reserved','utc':datetime.now(timezone.utc).isoformat(),'details':details})
        s['reserved_max_usd']+=max_estimate;self.write(s)
    def complete(self,request_id,token_cost_estimate,usage):
        s=self.read();e=next(e for e in s['entries'] if e['request_id']==request_id)
        if e['status']!='reserved':raise ValueError('Request already reconciled')
        if token_cost_estimate>e['maximum_reserved_usd']:raise ValueError('Estimate exceeds reservation; stop and reconcile')
        e.update(status='completed',token_cost_estimate_usd=token_cost_estimate,usage=usage)
        s['token_cost_estimate_usd']+=token_cost_estimate;s['paid_requests']+=1
        s['note']='Full-rate logged-token estimate; verified invoice billing unavailable. Reservations retained; pending requests remain encumbered.'
        self.write(s)
