"""Acquire public event/360 records and generate a portable play-by-play viewer."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request

from rin_gym.paths import workspace_root
from .state import build_states

BASE = 'https://raw.githubusercontent.com/hudl/open-data/master/data/'


def cached_json(root, name, url):
    path = root / (name + '.json')
    meta_path = root / (name + '.meta.json')
    if path.exists():
        meta = json.loads(meta_path.read_text())
        body = path.read_bytes()
        if meta['url'] != url or meta['sha256'] != hashlib.sha256(body).hexdigest():
            raise ValueError('Cache provenance mismatch: ' + str(path))
    else:
        req = urllib.request.Request(url, headers={'User-Agent': 'rin-gym/0.1', 'Accept': 'application/json'})
        with urllib.request.urlopen(req, timeout=60) as response:
            body = response.read()
        json.loads(body)
        root.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        meta = {'url': url, 'sha256': hashlib.sha256(body).hexdigest(),
                'retrieved_utc': datetime.now(timezone.utc).isoformat()}
        meta_path.write_text(json.dumps(meta, indent=2))
    return json.loads(body), meta


def build(match_id=3943043, competition=55, season=282, output=None, market_slug=None):
    workspace = workspace_root()
    raw = workspace / 'data/raw/play-by-play'
    sources = {}
    def get(name, url):
        data, metadata = cached_json(raw, name, url)
        sources[name] = metadata
        return data
    matches = get(f'matches_{competition}_{season}', BASE + f'matches/{competition}/{season}.json')
    match = next((m for m in matches if m['match_id'] == match_id), None)
    if match is None:
        raise ValueError('Match is absent from the selected competition/season')
    events = get(f'events_{match_id}', BASE + f'events/{match_id}.json')
    try:
        frames = get(f'frames_{match_id}', BASE + f'three-sixty/{match_id}.json')
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        frames = []
    payload = build_states(events, frames, match)
    lineups = get(f'lineups_{match_id}', BASE + f'lineups/{match_id}.json')
    for lineup in lineups:
        for person in lineup['lineup']:
            profile = payload['players'].setdefault(person['player_id'], {'id': person['player_id'], 'name': person['player_name'], 'team_id': lineup['team_id']})
            profile['jersey_number'] = person.get('jersey_number')
            profile['nickname'] = person.get('player_nickname')
    # Lineup profiles supply names/numbers only; future playing intervals, cards,
    # and positions in the lineup response never become event-prefix state.
    if not payload['summary']['final_score_matches_catalog']:
        raise ValueError('Reconstructed score disagrees with match catalog')
    payload['market'] = {'alignment': 'unverified', 'series': []}
    slug = market_slug or ('euro-finals-spain-vs-england' if match_id == 3943043 else None)
    if slug:
        event = get('market_' + str(match_id), 'https://gamma-api.polymarket.com/events/slug/' + urllib.parse.quote(slug, safe=''))
        series = []
        for contract in event['markets']:
            start_string = contract.get('gameStartTime')
            if not start_string:
                continue
            start = int(datetime.fromisoformat(start_string.replace('Z', '+00:00')).timestamp())
            tokens = json.loads(contract['clobTokenIds'])
            labels = json.loads(contract['outcomes'])
            for token, label in zip(tokens, labels):
                # Binary Yes/No contracts need only the Yes series; team-outcome markets need both.
                if label == 'No':
                    continue
                query = urllib.parse.urlencode({'market': token, 'startTs': start-300, 'endTs': start+14400, 'fidelity': 1})
                history = get('prices_' + token, 'https://clob.polymarket.com/prices-history?' + query)['history']
                if any(not 0 <= p['p'] <= 1 for p in history) or any(b['t'] <= a['t'] for a, b in zip(history, history[1:])):
                    raise ValueError('Invalid market price history')
                series.append({'label': contract['question'] if label == 'Yes' else label,
                               'market_id': contract['id'], 'token_id': token, 'history': history,
                               'rules': contract['description']})
        payload['market'] = {'alignment': 'unverified', 'slug': slug, 'series': series,
                             'note': 'Separate UTC history. Period-start anchors are unverified; no price is assigned to an event.'}
    payload['sources'] = sources
    destination = Path(output).expanduser().resolve() if output else workspace / 'artifacts/play-by-play' / str(match_id)
    destination.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    (destination / 'state.json').write_text(text, encoding='utf-8')
    template = Path(__file__).with_name('viewer.html').read_text(encoding='utf-8')
    safe = text.replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    (destination / 'index.html').write_text(template.replace('__REPLAY_DATA__', safe), encoding='utf-8')
    (destination / 'manifest.json').write_text(json.dumps({'match': payload['match'], 'summary': payload['summary'],
                                                        'sources': sources, 'forecast_ready': False}, indent=2))
    print(json.dumps(payload['summary'], indent=2))
    print(destination / 'index.html')
    return destination
