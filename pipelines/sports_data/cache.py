import concurrent.futures, hashlib, json, pathlib, urllib.request, urllib.error, datetime

from rin_gym.paths import workspace_root

REPOSITORY = workspace_root()
ROOT = REPOSITORY / 'data' / 'processed' / 'sports'
RAW = REPOSITORY / 'data' / 'raw' / 'sports'
MANIFESTS = REPOSITORY / 'data' / 'manifests' / 'sports'

def fetch(name, url):
    RAW.mkdir(parents=True, exist_ok=True)
    path = RAW / (name + '.json')
    if path.exists():
        return name, json.loads(path.read_text())
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'sports-data-research/1.0', 'Accept': 'application/json'})
        with urllib.request.urlopen(req, timeout=40) as r:
            body = r.read()
            status = r.status
        data = json.loads(body)
        path.write_bytes(body)
        meta = {'url': url, 'status': status, 'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest(), 'fetched_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
        (RAW / (name + '.meta.json')).write_text(json.dumps(meta, indent=2))
        return name, data
    except Exception as e:
        error = {'error': str(e), 'url': url}
        (RAW / (name + '.error.json')).write_text(json.dumps(error, indent=2))
        return name, error
