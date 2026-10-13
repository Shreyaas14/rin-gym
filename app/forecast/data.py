from __future__ import annotations

import concurrent.futures
import datetime as dt
import hashlib
import itertools
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo
from rin_gym.paths import workspace_root

ROOT = Path(__file__).resolve().parent
REPOSITORY = workspace_root()
RAW = REPOSITORY / 'data' / 'raw' / 'forecast'
MANIFESTS = REPOSITORY / 'data' / 'manifests' / 'forecast'
ARTIFACTS = REPOSITORY / 'artifacts' / 'forecast-eval'
OUTPUT = REPOSITORY / 'data' / 'processed' / 'forecast'
FIFA = REPOSITORY / 'data' / 'raw' / 'sports'


def epoch(value: str) -> float:
    return dt.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def read(path: Path):
    return json.loads(path.read_text())


def write(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False))


def cached_get(name: str, url: str):
    """Cache by explicit name AND URL, keeping hashes and failed requests."""
    RAW.mkdir(parents=True, exist_ok=True)
    path = RAW / (name + '.json')
    meta_path = RAW / (name + '.meta.json')
    if path.exists():
        meta = read(meta_path)
        if meta['url'] != url:
            raise ValueError('Cached URL mismatch: ' + name)
        if hashlib.sha256(path.read_bytes()).hexdigest() != meta['sha256']:
            raise ValueError('Cached content hash mismatch: ' + name)
        return read(path)
    error = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'sports-forecast-research/1.0', 'Accept': 'application/json'})
            with urllib.request.urlopen(req, timeout=40) as response:
                body = response.read()
            value = json.loads(body)
            path.write_bytes(body)
            write(meta_path, {'url': url, 'sha256': hashlib.sha256(body).hexdigest(), 'retrieved_utc': dt.datetime.now(dt.timezone.utc).isoformat()})
            return value
        except (urllib.error.URLError, ValueError) as exc:
            error = str(exc)
            if isinstance(exc, urllib.error.HTTPError) and exc.code in (403, 404):
                break
            time.sleep(1 + attempt)
    write(RAW / (name + '.error.json'), {'url': url, 'error': error})
    raise RuntimeError(name + ': ' + str(error))


def split_for(date: str, config: dict) -> str:
    if date[:10] <= config['train_through_utc_date']:
        return 'train'
    if date[:10] <= config['validation_through_utc_date']:
        return 'validation'
    return 'test'


def group_matches():
    return sorted((m for m in read(FIFA / 'fifa_calendar_2026.json')['Results']
                   if m['StageName'][0]['Description'] == 'First Stage'), key=lambda m: (m['Date'], m['IdMatch']))


def normalize(text):
    text = text.casefold().replace('türkiye', 'turkey').replace('czech republic', 'czechia').replace('usa', 'united states')
    text = text.replace('bosnia and herzegovina', 'bosnia-herzegovina').replace('congo dr', 'dr congo')
    return re.sub(r'[^\w]', '', text)


def score_batches(events):
    """Resolve simultaneous stale save/shot score fields using the batch maximum.

    All members have the same observation time. No later batch is consulted.
    A decrease between batches is not silently repaired.
    """
    for timestamp, members in itertools.groupby(events, key=lambda e: epoch(e['Timestamp'])):
        batch = list(members)
        yield timestamp, max(e['HomeGoals'] for e in batch), max(e['AwayGoals'] for e in batch), batch


def prepare():
    config = read(ROOT / 'config.json')
    matches = group_matches()
    from .governance import assert_development_matches
    assert_development_matches(m['IdMatch'] for m in matches)
    # Freeze split membership independently of market retrieval or model scores.
    split = [{'match_id': m['IdMatch'], 'scheduled_utc': m['Date'], 'split': split_for(m['Date'], config)} for m in matches]
    write(MANIFESTS / 'splits.json', split)
    split_lookup = {r['match_id']: r['split'] for r in split}
    catalog = []
    required = {(normalize(m['Home']['TeamName'][0]['Description']), normalize(m['Away']['TeamName'][0]['Description'])) for m in matches}
    for offset in range(0, 2000, 100):
        page = cached_get('catalog_' + str(offset), f'https://gamma-api.polymarket.com/events?tag_id=102232&closed=true&limit=100&offset={offset}')
        catalog.extend(page)
        pairs = {tuple(normalize(t) for t in e['title'].split(' vs. ')) for e in catalog if e['slug'].startswith('fifwc-') and not e['slug'].endswith('-more-markets')}
        if required <= pairs or len(page) < 100:
            break

    def one(match):
        mid = match['IdMatch']
        home = match['Home']['TeamName'][0]['Description']
        away = match['Away']['TeamName'][0]['Description']
        row = {'match_id': mid, 'home': home, 'away': away, 'scheduled_utc': match['Date'], 'split': split_lookup[mid]}
        try:
            candidates = [e for e in catalog if e['slug'].startswith('fifwc-') and not e['slug'].endswith('-more-markets')
                          and tuple(normalize(t) for t in e['title'].split(' vs. ')) == (normalize(home), normalize(away))
                          and any(m.get('gameStartTime') and abs(epoch(m['gameStartTime']) - epoch(match['Date'])) <= 900 for m in e.get('markets', []))]
            if len(candidates) != 1:
                raise ValueError(f'Expected one catalog match by teams/start; found {len(candidates)}')
            event = candidates[0]
            slug = event['slug']
            markets = [m for m in event['markets'] if m.get('sportsMarketType') == 'moneyline' and json.loads(m.get('outcomes', '[]')) == ['Yes', 'No']]
            if len(markets) != 3:
                raise ValueError(f'Expected three binary moneylines, found {len(markets)}')
            # Question grammar is anchored to the exact team names and date; never rely on response order.
            mapped = {}
            for market in markets:
                question = normalize(market['question'])
                if 'endinadraw' in question:
                    outcome = 'draw'
                elif question.startswith('will' + normalize(home) + 'winon'):
                    outcome = 'home'
                elif question.startswith('will' + normalize(away) + 'winon'):
                    outcome = 'away'
                else:
                    raise ValueError('Unmapped question: ' + market['question'])
                if outcome in mapped:
                    raise ValueError('Duplicate outcome: ' + outcome)
                if abs(epoch(market['gameStartTime']) - epoch(match['Date'])) > 900:
                    raise ValueError('Scheduled start mismatch')
                if '90 minutes' not in market['description'] or 'stoppage' not in market['description']:
                    raise ValueError('Regulation-time rule not verified')
                mapped[outcome] = market
            if set(mapped) != {'home', 'draw', 'away'}:
                raise ValueError('Incomplete outcome set')
            fifa_path = FIFA / f'fifa_{mid}_timeline.json'
            events = read(fifa_path)['Event']
            starts = [e for e in events if e['TypeLocalized'][0]['Description'] == 'Start Time']
            ends = [e for e in events if e['TypeLocalized'][0]['Description'] == 'End Time']
            if len(starts) != 2 or len(ends) != 2:
                raise ValueError('Expected exactly two regulation periods')
            kickoff = epoch(starts[0]['Timestamp'])
            terminal = ends[-1]
            # Labels come from regulation end, never from a feature field or market price.
            hg, ag = terminal['HomeGoals'], terminal['AwayGoals']
            target = 0 if hg > ag else 1 if hg == ag else 2
            if (hg, ag) != (match['HomeTeamScore'], match['AwayTeamScore']):
                raise ValueError('Timeline/scoreboard disagreement')
            tids = [epoch(e['Timestamp']) for e in events]
            if any(b < a for a, b in zip(tids, tids[1:])):
                raise ValueError('Nonmonotonic timeline')
            if len({e['EventId'] for e in events}) != len(events):
                raise ValueError('Duplicate event IDs')
            batches = list(score_batches(events))
            if any(b[1] < a[1] or b[2] < a[2] for a, b in zip(batches, batches[1:])):
                raise ValueError('Score correction requires explicit handling')
            if not all(kickoff - 600 <= t <= kickoff + 18000 for t in tids):
                raise ValueError('Implausible event timestamp')
            ordered = [mapped[k] for k in ['home', 'draw', 'away']]
            settlements = [json.loads(m['outcomePrices'])[0] for m in ordered]
            if [float(p) for p in settlements] != [float(i == target) for i in range(3)]:
                raise ValueError('Contract settlement disagrees with regulation outcome')
            series = []
            for market in ordered:
                token = json.loads(market['clobTokenIds'])[0]
                start = int(epoch(match['Date']) - 3600)
                end = int(epoch(terminal['Timestamp']) + 120)
                name = 'history_' + market['id']
                url = f'https://clob.polymarket.com/prices-history?market={token}&startTs={start}&endTs={end}&fidelity=1'
                history = cached_get(name, url)['history']
                times = [p['t'] for p in history]
                if not history or times != sorted(set(times)) or any(not 0 <= p['p'] <= 1 for p in history):
                    raise ValueError('Invalid price history: ' + name)
                series.append({'outcome': ['home', 'draw', 'away'][len(series)], 'market_id': market['id'], 'question': market['question'], 'token_id': token, 'file': name + '.json', 'points': len(history)})
            row.update(status='ok', slug=slug, target=target, final_score=[hg, ag], kickoff_utc=starts[0]['Timestamp'], regulation_end_utc=terminal['Timestamp'], markets=series,
                       simultaneous_score_conflicts=sum(len({(e['HomeGoals'], e['AwayGoals']) for e in b[3]}) > 1 for b in batches),
                       fifa_sha256=hashlib.sha256(fifa_path.read_bytes()).hexdigest())
        except (RuntimeError, ValueError, KeyError, IndexError) as exc:
            row.update(status='excluded', reason=str(exc))
        print(f"{row['split']:10} {mid} {home}–{away}: {row['status']} {row.get('reason', '')}", flush=True)
        return row

    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        manifest = list(pool.map(one, matches))
    write(MANIFESTS / 'manifest.json', manifest)
    source_index = []
    for path in sorted(RAW.glob('*.meta.json')):
        source_index.append({'file': str((RAW / path.name.replace('.meta.json', '.json')).relative_to(REPOSITORY)), **read(path)})
    write(MANIFESTS / 'source-index.json', source_index)
    print(json.dumps({s: {'planned': sum(r['split'] == s for r in manifest), 'available': sum(r['split'] == s and r['status'] == 'ok' for r in manifest)} for s in ['train', 'validation', 'test']}, indent=2))
