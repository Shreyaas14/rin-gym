"""Acquire new, public catalogs; never uses exchange or model credentials."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import subprocess
from urllib.parse import urlencode, urlparse

ROOT = Path(__file__).resolve().parent
KBASE = 'https://external-api.kalshi.com/trade-api/v2/'
PBASE = 'https://gamma-api.polymarket.com/'
SERIES = ['KXNFLGAME','KXNBAGAME','KXNHLGAME','KXMLBGAME','KXFEDDECISION',
          'KXFED','KXCPIYOY','KXCPI','KXCPICOREYOY','KXBTCMAXMON','KXBTCD','KXALIENS']

def now():
    return datetime.now(timezone.utc).isoformat()

def fetch(name, url, pdf=False):
    folder = ROOT / ('sources' if pdf else 'catalog')
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (name + ('.pdf' if pdf else '.json'))
    receipt = path.with_suffix(path.suffix + '.receipt.json')
    if path.exists() and receipt.exists():
        meta = json.loads(receipt.read_text())
        assert meta['url'] == url
        assert meta['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
        return (None if pdf else json.loads(path.read_text())), meta
    start = now()
    tmp = path.with_suffix(path.suffix + '.download')
    r = subprocess.run(['curl','--silent','--show-error','--location','--max-time','45',
                        '--retry','2','--retry-delay','1',url,'--output',str(tmp),
                        '--write-out','%{http_code}'], capture_output=True, text=True)
    if r.returncode or r.stdout != '200':
        raise RuntimeError(f'public download failed: {name}, HTTP {r.stdout}, exit {r.returncode}')
    data = tmp.read_bytes()
    if pdf:
        assert data.startswith(b'%PDF'), name
        obj = None
    else:
        obj = json.loads(data)
    tmp.replace(path)
    if pdf:
        subprocess.run(['pdftotext','-layout',str(path),str(path.with_suffix('.txt'))],check=True)
    meta = {'name':name,'url':url,'path':str(path.relative_to(ROOT)),
            'retrieved_start_utc':start,'retrieved_end_utc':now(),
            'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
    receipt.write_text(json.dumps(meta,indent=2)+'\n')
    return obj, meta

def kalshi(st):
    info,_ = fetch('series_'+st, KBASE+'series/'+st)
    series = info['series']
    url = series.get('contract_terms_url')
    if url:
        fetch(Path(urlparse(url).path).stem,url,True)
    markets = {}; cursor = None; pages = 0
    for page in range(4):
        query = {'series_ticker':st,'limit':1000,'status':'open','mve_filter':'exclude'}
        if cursor: query['cursor'] = cursor
        data,_ = fetch(f'kalshi_{st}_{page}', KBASE+'markets?'+urlencode(query))
        for row in data['markets']: markets[row['ticker']] = row
        pages += 1; cursor = data.get('cursor')
        if not cursor: break
    return st, {'series':series,'markets':list(markets.values()),'pages':pages,
                'complete_at_acquisition':not bool(cursor),'remaining_cursor':cursor}

def main():
    catalog = {'started_utc':now(),'kalshi':{},'polymarket':[]}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for future in as_completed([pool.submit(kalshi,st) for st in SERIES]):
            st,data = future.result(); catalog['kalshi'][st] = data
            print(json.dumps({'series':st,'markets':len(data['markets']),
                              'complete':data['complete_at_acquisition']}),flush=True)
    # Cursor pagination fixes both the capped page size and offset-depth limit.
    # Four league catalogs plus a bounded general catalog; never claim global completeness.
    sports,_ = fetch('poly_sports', PBASE+'sports')
    (ROOT/'sports.json').write_text(json.dumps(sports,indent=2)+'\n')
    tags = {r['sport']:r['primaryTagId'] for r in sports if r.get('sport') in ('nba','nfl','nhl','mlb')}
    poly = {}; summaries = []
    sources = [('general',None,30,None)] + [(league+'_moneyline',tags[league],30,'moneyline') for league in sorted(tags)] + [(league+'_mixed',tags[league],10,None) for league in sorted(tags)]
    for name,tag,maxpages,kind in sources:
        cursor = None; count = 0; duplicates = 0
        for page in range(maxpages):
            query = {'limit':100,'closed':'false','order':'id','ascending':'false'}
            if cursor: query['after_cursor'] = cursor
            if tag:
                # Sports metadata tags contains comma-separated tag IDs.
                query['tag_id'] = str(tag)
            if kind: query['sports_market_types'] = kind
            data,_ = fetch(f'poly_keyset_{name}_{page}',PBASE+'markets/keyset?'+urlencode(query))
            rows = data['markets']; count += len(rows)
            for r in rows:
                duplicates += str(r['id']) in poly
                poly[str(r['id'])] = r
            cursor = data.get('next_cursor')
            if page % 10 == 0:
                print(json.dumps({'poly_source':name,'page':page,'source_rows':count,'distinct_total':len(poly)}),flush=True)
            if not cursor: break
        summaries.append({'source':name,'tag':tag,'sports_market_type':kind,'pages':page+1,'rows':count,
                          'overlap_rows':duplicates,'complete_at_acquisition':not bool(cursor),
                          'remaining_cursor':cursor})
    catalog['polymarket'] = list(poly.values())
    catalog['poly_sources'] = summaries
    catalog['poly_capped'] = any(not x['complete_at_acquisition'] for x in summaries)
    catalog['ended_utc'] = now()
    (ROOT/'catalogs.json').write_text(json.dumps(catalog,indent=2)+'\n')
    receipts = [json.loads(p.read_text()) for p in ROOT.rglob('*.receipt.json')]
    (ROOT/'manifest.json').write_text(json.dumps(receipts,indent=2)+'\n')
    print(json.dumps({'kalshi_markets':sum(len(x['markets']) for x in catalog['kalshi'].values()),
                      'poly_markets':len(poly),'receipts':len(receipts),'poly_capped':catalog['poly_capped']}))

if __name__ == '__main__': main()
