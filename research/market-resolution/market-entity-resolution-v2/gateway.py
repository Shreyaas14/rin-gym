"""Reserve paid-tool LLM calls before dispatch; ingest immutable fetch receipts."""
from pathlib import Path
from datetime import datetime,timezone
import argparse
import json
import re
from budget import Budget
from evaluate import ROOT,dump,digest,sha,verify,llm_messages,valid_answers,summarize

CONFIG={'endpoint':'https://x402-services-production.up.railway.app/api/ai/chat',
        'requested_model':'gpt-4o-mini','reported_model':'openai/gpt-4o-mini',
        'maxTokens':512,'temperature':0,'per_request_ceiling_usd':0.01,
        'route':'AgentCash fetch; no direct API credential',
        'provenance_limit':'Gateway-reported servedBy/model; upstream provider/version not independently verified.',
        'parser':'Accept bare JSON or one fenced JSON object; no repairs or repeated inference.'}

def body(pair):
    messages=llm_messages(pair)
    messages[0]['content']+=' Return exactly the JSON object without Markdown fences or an introductory sentence.'
    return {'model':CONFIG['requested_model'],'messages':messages,
            'maxTokens':CONFIG['maxTokens'],'temperature':CONFIG['temperature']}

def initialize():
    verify()
    record={'frozen_utc':datetime.now(timezone.utc).isoformat(),'config':CONFIG,
            'gateway_py_sha256':sha(Path(__file__)), 'base_freeze_sha256':sha(ROOT/'freeze.json')}
    p=ROOT/'gateway-freeze.json'
    if p.exists():
        old=json.loads(p.read_text())
        for k in ('config','gateway_py_sha256','base_freeze_sha256'):
            if old[k]!=record[k]:raise RuntimeError('Gateway configuration changed: '+k)
    else:dump(p,record)

def reserve_batch(n):
    initialize();budget=Budget(ROOT/'budget.sqlite');pairs=json.loads((ROOT/'pairs.json').read_text())
    jobs=[]
    for p in pairs:
        if (ROOT/'responses/llm'/(p['id']+'.json')).exists():continue
        claim=ROOT/'gateway-claims'/(p['id']+'.json')
        if claim.exists():continue
        payload=body(p)
        ident=budget.reserve_fixed('llm',p['id'],CONFIG['reported_model'],digest(payload),10000000)
        record={'pair_id':p['id'],'reservation':ident,'payload_sha256':digest(payload),'body':payload}
        dump(claim,record);jobs.append(record)
        if len(jobs)==n:break
    budget.export(ROOT/'budget-summary.json')
    print(json.dumps(jobs))

def ingest(path):
    receipt=json.loads(Path(path).read_text());pid=receipt['pair_id']
    claim=json.loads((ROOT/'gateway-claims'/(pid+'.json')).read_text())
    assert receipt['reservation']==claim['reservation']
    result=receipt['tool_result'];blocks=result.get('content',[])
    raw=None;metadata=[]
    for block in blocks:
        if block.get('type')!='text':continue
        try:obj=json.loads(block['text'])
        except json.JSONDecodeError:continue
        if isinstance(obj,dict) and 'content' in obj and ('model' in obj or 'servedBy' in obj):raw=obj
        else:metadata.append(obj)
    if raw is None:raise RuntimeError('Gateway returned no recognized model response; reservation retained')
    if raw.get('model')!=CONFIG['reported_model'] or raw.get('servedBy')!=CONFIG['reported_model']:
        raise RuntimeError('Gateway model mismatch; reservation retained and no valid assessment result')
    content=raw['content'].strip()
    if content.startswith('```'):
        m=re.fullmatch(r'```(?:json)?\s*(.*?)\s*```',content,re.S)
        if not m:raise RuntimeError('Invalid fenced JSON')
        content=m[1]
    response=json.loads(content);valid_answers(response)
    row={'pair_id':pid,'method':'llm','payload_sha256':claim['payload_sha256'],
         'reservation':claim['reservation'],'response':response,'raw_response':raw,
         'elapsed_seconds':receipt['elapsed_seconds'],'gateway_metadata':metadata,
         'provenance_limit':CONFIG['provenance_limit']}
    dump(ROOT/'responses/llm'/(pid+'.json'),row)
    # AgentCash includes explicit payment details when it pays. Preserve the full
    # response for audit. Conservatively keep reservation if payment metadata
    # exists: do not infer an amount from an ambiguous receipt schema.
    serialized=json.dumps(metadata).lower()
    if 'payment' not in serialized and 'txhash' not in serialized and not result.get('isError'):
        budget=Budget(ROOT/'budget.sqlite')
        budget.settle_fixed(claim['reservation'],0,{'agentcash_receipt':str(Path(path).relative_to(ROOT)),
             'payment':'No payment metadata or receipt returned; endpoint served without wallet payment.'})
        budget.export(ROOT/'budget-summary.json')
    print(json.dumps({'ingested':pid,'model':raw.get('model')}))

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--reserve-batch',type=int);ap.add_argument('--ingest')
    ap.add_argument('--initialize',action='store_true');args=ap.parse_args()
    if args.initialize:initialize()
    elif args.reserve_batch:reserve_batch(args.reserve_batch)
    elif args.ingest:ingest(args.ingest)
    else:ap.error('Select a command')

if __name__=='__main__':main()
