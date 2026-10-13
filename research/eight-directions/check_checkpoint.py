import json,hashlib,logging
from pathlib import Path
from datetime import datetime,timezone
import tinker
logging.disable(logging.CRITICAL)
root=Path('/Users/alexkan/Desktop/frontiers/rin-gym-research-eight')
p=Path('/Users/alexkan/Desktop/frontiers/training-results/run-3-results/forecast-rules-run-3/checkpoints.jsonl')
cp='tinker://d2426bc1-ec71-559d-bb81-e17da9372e58:train:0/sampler_weights/final'
rows=[json.loads(l) for l in p.read_text().splitlines()]
assert any(r['name']=='final' and r['batch']==48 and r['sampler_path']==cp for r in rows)
receipt={'checkpoint':cp,'checked_utc':datetime.now(timezone.utc).isoformat(),'local_provenance':{'source':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'batch':48},'paid_requests':0,'purpose':'Metadata availability only; no prediction, training, enrollment or scoring.'}
try:
    service=tinker.ServiceClient()
    result=service.create_rest_client().get_weights_info_by_tinker_path(cp).result(timeout=35)
    receipt['availability']='verified';receipt['weights_info']={k:getattr(result,k) for k in ('base_model','is_lora','lora_rank')}
except Exception as e:
    receipt['availability']='blocked';receipt['error_type']=type(e).__name__;receipt['status_code']=getattr(e,'status_code',None)
(root/'research/eight-directions/checkpoint-verification.json').write_text(json.dumps(receipt,indent=2)+'\n')
print(json.dumps(receipt))
