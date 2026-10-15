"""Freeze bounded public market catalogs and linked Kalshi contract PDFs."""
import concurrent.futures
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import urllib.parse

ROOT = Path(__file__).resolve().parent
SERIES = ['KXFEDDECISION', 'KXFED', 'KXFEDFUNDSYEAR', 'KXCPI', 'KXCPIYOY', 'KXCPICOREYOY', 'KXGDP', 'KXBTCD', 'KXBTCMAXMON', 'KXNFLGAME', 'KXNBAGAME', 'KXNHLGAME', 'KXALIENS']


def fetch(name, url, binary=False):
    path = ROOT / ('sources' if binary else 'catalog') / (name + ('.pdf' if binary else '.json'))
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            if not binary:
                json.loads(path.read_text())
            elif not path.read_bytes().startswith(b'%PDF'):
                raise ValueError('Not a PDF')
            return {'name': name, 'url': url, 'path': str(path.relative_to(ROOT)), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'bytes': path.stat().st_size, 'cached': True}
        except (ValueError, UnicodeError):
            pass
    started = datetime.now(timezone.utc).isoformat()
    result = subprocess.run(['curl', '--silent', '--show-error', '--location', '--max-time', '60', '--retry', '2', '--retry-delay', '1', url, '--output', str(path), '--write-out', '%{http_code}'], capture_output=True, text=True)
    if result.returncode != 0 or result.stdout != '200':
        return {'name': name, 'url': url, 'error': 'download_failed', 'exit_code': result.returncode, 'status': result.stdout}
    try:
        if not binary:
            json.loads(path.read_text())
        elif not path.read_bytes().startswith(b'%PDF'):
            raise ValueError('Not a PDF')
    except (ValueError, UnicodeError):
        return {'name': name, 'url': url, 'error': 'invalid_content'}
    if binary:
        subprocess.run(['pdftotext', '-layout', str(path), str(path.with_suffix('.txt'))], check=True)
    return {'name': name, 'url': url, 'path': str(path.relative_to(ROOT)), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'bytes': path.stat().st_size, 'retrieved_start_utc': started, 'retrieved_end_utc': datetime.now(timezone.utc).isoformat()}


def main():
    series = {r['ticker']: r for r in json.loads((ROOT/'kalshi_series.json').read_text())['series']}
    jobs=[]
    for ticker in SERIES:
        query=urllib.parse.urlencode({'series_ticker':ticker,'limit':200,'status':'open','mve_filter':'exclude'})
        jobs.append(('kalshi_'+ticker,'https://external-api.kalshi.com/trade-api/v2/markets?'+query,False))
    for offset in range(0,600,100):
        query=urllib.parse.urlencode({'limit':100,'offset':offset,'active':'true','closed':'false','order':'volume24hr','ascending':'false'})
        jobs.append(('polymarket_'+str(offset),'https://gamma-api.polymarket.com/markets?'+query,False))
    urls={series[t]['contract_terms_url'] for t in SERIES if t in series and series[t].get('contract_terms_url')}
    for url in sorted(urls):
        name=Path(urllib.parse.urlparse(url).path).stem
        jobs.append((name,url,True))
    manifest=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        tasks={pool.submit(fetch,*job):job[0] for job in jobs}
        for future in concurrent.futures.as_completed(tasks):
            row=future.result();manifest.append(row)
            print(json.dumps({k:row.get(k) for k in ('name','status','bytes','error','cached')}),flush=True)
            (ROOT/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps({'downloads':len(manifest),'errors':sum('error' in r for r in manifest)}),flush=True)


if __name__=='__main__':main()
