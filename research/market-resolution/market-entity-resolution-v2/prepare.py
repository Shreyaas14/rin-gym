"""Freeze 400 previously unevaluated real pairs and a blinded adjudication sheet."""
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
import csv
import hashlib
import json
import random
import re
from urllib.parse import urlparse
from features import features,retrieval_score

ROOT=Path(__file__).resolve().parent
OLD=ROOT.parent/'market-entity-resolution-real'
SEED=20261013

def dump(name,obj):
    (ROOT/name).write_text(json.dumps(obj,indent=2)+'\n')

def group(m):
    if m['venue']=='kalshi': return m['raw']['event_ticker']
    slug=m['raw']['slug']
    s=re.match(r'^(nba|nfl|nhl|mlb)-([a-z]+)-([a-z]+)-(\d{4}-\d{2}-\d{2})',slug)
    if s:return 'sports:'+':'.join((s[1],s[4],'-'.join(sorted([s[2],s[3]]))))
    return m['raw'].get('events',[{}])[0].get('slug',slug)

def catalogs():
    c=json.loads((ROOT/'catalogs.json').read_text());k=[];p=[]
    for st,block in c['kalshi'].items():
        info=block['series'];url=info.get('contract_terms_url');terms=''
        if url: terms=(ROOT/'sources'/(Path(urlparse(url).path).stem+'.txt')).read_text()
        for raw in block['markets']:
            k.append({'venue':'kalshi','id':raw['ticker'],'title':raw['title'],
                      'selected_outcome':'Yes','selected_outcome_description':raw.get('yes_sub_title',''),
                      'outcomes':['Yes','No'],'rules':raw['rules_primary']+'\n\n'+raw.get('rules_secondary',''),
                      'contract_terms_text':terms,'contract_terms_url':url,
                      'settlement_sources':info.get('settlement_sources',[]),
                      'url':'https://kalshi.com/markets/'+st.lower()+'/'+raw['event_ticker'].lower(),
                      'source_url':'https://external-api.kalshi.com/trade-api/v2/markets/'+raw['ticker'],
                      'raw':raw})
    for raw in c['polymarket']:
        outcomes=json.loads(raw['outcomes']) if isinstance(raw['outcomes'],str) else raw['outcomes']
        tokens=json.loads(raw.get('clobTokenIds','[]')) if isinstance(raw.get('clobTokenIds'),str) else raw.get('clobTokenIds',[])
        if len(outcomes)!=2:continue
        # Every selected outcome has an explicit token. Both actual outcome records
        # are candidates; no text is rewritten to manufacture an opposite-side case.
        for i,outcome in enumerate(outcomes):
            p.append({'venue':'polymarket','id':str(raw['id']),'title':raw['question'],
                      'selected_outcome':outcome,'outcomes':outcomes,'outcome_index':i,
                      'selected_token_id':tokens[i] if len(tokens)>i else None,
                      'rules':raw.get('description',''),'resolution_source':raw.get('resolutionSource',''),
                      'url':'https://polymarket.com/event/'+raw.get('events',[{}])[0].get('slug',raw['slug']),
                      'source_url':'https://gamma-api.polymarket.com/markets/'+str(raw['id']),
                      'raw':raw})
    return k,p

def main():
    if (ROOT/'freeze.json').exists():raise SystemExit('Dataset already frozen; do not overwrite')
    k,p=catalogs();old=json.loads((OLD/'reference.json').read_text())
    old_k_groups={group(r['a']) for r in old};old_p_groups={group(r['b']) for r in old}
    old_p_ids={r['b']['id'] for r in old};old_k_ids={r['a']['id'] for r in old}
    k=[m for m in k if group(m) not in old_k_groups and m['id'] not in old_k_ids]
    p=[m for m in p if group(m) not in old_p_groups and m['id'] not in old_p_ids and m['selected_token_id']]
    rng=random.Random(SEED);rng.shuffle(k)
    # Stratified benchmark of naturally retrieved candidates. Selection uses source
    # domains/ranker only, never labels or model responses. Not deployment prevalence.
    quotas={'nfl':88,'nba':88,'nhl':144,'mlb':40,'btc_point':40}
    rows=[];anchor_stats=[];seen=set()
    for domain,quota in quotas.items():
        anchors=[m for m in k if features(m)['domain']==domain]
        if domain=='btc_point':
            candidates=[m for m in p if 'bitcoin' in m['title'].lower()]
        else:candidates=[m for m in p if features(m)['domain']==domain]
        count=0
        for a in anchors:
            if count>=quota:break
            ranked=sorted(candidates,key=lambda b:(-retrieval_score(a,b),b['id'],b['outcome_index']))
            picked=0
            for rank,b in enumerate(ranked,1):
                key=(a['id'],b['id'],b['selected_outcome'])
                if key in seen:continue
                seen.add(key);count+=1;picked+=1
                rows.append({'id':f'v2_{len(rows)+1:04d}','family':domain,'split':'heldout',
                             'event_group':group(a),'candidate_rank':rank,'a':a,'b':b})
                if picked==4 or count==quota:break
        anchor_stats.append({'family':domain,'available_anchors':len(anchors),
                             'candidate_outcomes':len(candidates),'selected_pairs':count})
        if count!=quota:raise RuntimeError(f'Not enough real candidates for {domain}: {count}/{quota}')
    assert len(rows)==400 and len(seen)==400
    dump('pairs.json',rows)
    dump('selection.json',{'seed':SEED,'quotas':quotas,'anchors':anchor_stats,
                          'old_k_event_groups_excluded':sorted(old_k_groups),
                          'old_p_event_groups_excluded':sorted(old_p_groups),
                          'heldout_event_groups':len({r['event_group'] for r in rows}),
                          'selection':'Four highest-ranked outcome-specific candidates per shuffled anchor, with frozen family quotas. No reference labels used.',
                          'development':'Original 60 pairs only. All 400 new pairs held out. Shared rule templates are not held out.',
                          'limitations':'Bounded catalogs; correlated event/template pairs; no independently exhaustive retrieval gold.'})
    fields=['pair_id','family','kalshi_id','kalshi_title','kalshi_selected','kalshi_url',
            'poly_id','poly_title','poly_selected','poly_token_id','poly_url',
            'identity_label','settlement_label','identity_evidence','settlement_counterexample_or_proof',
            'reviewer','reviewed_at','notes']
    with (ROOT/'review-blind.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        shuffled=rows.copy();random.Random(SEED+1).shuffle(shuffled)
        for r in shuffled:
            a,b=r['a'],r['b'];writer.writerow({'pair_id':r['id'],'family':r['family'],
                'kalshi_id':a['id'],'kalshi_title':a['title'],
                'kalshi_selected':a.get('selected_outcome_description') or 'Yes','kalshi_url':a['source_url'],
                'poly_id':b['id'],'poly_title':b['title'],'poly_selected':b['selected_outcome'],
                'poly_token_id':b['selected_token_id'],'poly_url':b['url']})
    dump('reference-template.json',[{'pair_id':r['id'],'identity_label':None,'settlement_label':None,
                                   'reviewer':None,'identity_evidence':None,
                                   'settlement_counterexample_or_proof':None} for r in rows])
    print(json.dumps({'pairs':len(rows),'families':dict(Counter(r['family'] for r in rows)),
                      'event_groups':len({r['event_group'] for r in rows}),
                      'unique_kalshi':len({r['a']['id'] for r in rows}),
                      'unique_poly_outcomes':len({(r['b']['id'],r['b']['selected_outcome']) for r in rows}),
                      'labels':'pending independent review; no circular heuristic-derived gold'}))

if __name__=='__main__':main()
