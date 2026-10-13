"""Save only usage attributable to this study; never save credentials/account data."""
import json,logging
from pathlib import Path
from datetime import datetime,timezone,timedelta
import tinker
logging.disable(logging.CRITICAL)
now=datetime.now(timezone.utc);end=now.replace(minute=0,second=0,microsecond=0)+timedelta(hours=1);start=now.replace(hour=0,minute=0,second=0,microsecond=0)
out={'queried_utc':now.isoformat(),'starting_on':start.isoformat(),'ending_before':end.isoformat(),'verified_invoice_usd':None,'note':'Usage endpoint may lag hours; gross provider estimates are distinct from invoices. No absent data interpreted as zero.','study_events':[]}
try:
    service=tinker.ServiceClient()
    result=service.create_rest_client().get_billing_usage(start,end).result(timeout=35)
    sessions={sid:entry.user_metadata for sid,entry in result.sessions.items() if (entry.user_metadata or {}).get('study')=='soccer-eight-directions-20261013'}
    out['status']='retrieved';out['study_sessions']=sessions
    out['cost_data_through']=result.cost_data_through.isoformat() if result.cost_data_through else None
    for event in result.data:
        if event.session_id in sessions:
            out['study_events'].append({'direction':sessions[event.session_id].get('direction'),'session_id':event.session_id,'base_model':event.base_model,'bucket_start':event.bucket_start.isoformat(),'bucket_end':event.bucket_end.isoformat(),'event_info':event.event_info.model_dump(),'estimated_gross_cost_usd':event.estimated_cost_usd,'effective_rate_usd_per_million_tokens':event.effective_rate_usd_per_million_tokens,'effective_rate_usd_per_gigabyte_month':event.effective_rate_usd_per_gigabyte_month})
except Exception as e:
    out['status']='unavailable';out['safe_error_class']=type(e).__name__;out['status_code']=getattr(e,'status_code',None)
Path('research/eight-directions/provider-usage.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps({'status':out['status'],'study_events':len(out['study_events']),'verified_invoice_usd':None,'safe_error_class':out.get('safe_error_class')}))
