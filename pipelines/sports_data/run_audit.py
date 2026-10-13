"""Reproduce the fixed cohort audit using public endpoints; existing downloads are cached."""
from .cache import fetch, RAW, ROOT, MANIFESTS
from .collect import collect
from .analyze import run as analyze
import concurrent.futures
import datetime
import json
import shutil
from rin_gym.paths import seed_sports_manifests


def batch(urls):
    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        for name, data in pool.map(lambda item: fetch(*item), urls.items()):
            if isinstance(data, dict) and 'error' in data:
                raise RuntimeError(f'{name}: {data["error"]}')


def run():
    ROOT.mkdir(parents=True, exist_ok=True)
    RAW.mkdir(parents=True, exist_ok=True)
    seed_sports_manifests()
    base = 'https://api.elections.kalshi.com/trade-api/v2/historical/markets'
    batch({
        'kalshi_nba_all': base + '?limit=1000&series_ticker=KXNBAGAME',
        'kalshi_epl_all': base + '?limit=1000&series_ticker=KXEPLGAME',
        'poly_euro_events': 'https://gamma-api.polymarket.com/events?tag_id=100268&closed=true&limit=100',
        'statsbomb_matches': 'https://raw.githubusercontent.com/statsbomb/open-data/master/data/matches/55/282.json',
    })
    urls = {}
    for sport, catalog, league in [('nba', 'kalshi_nba_all', 'basketball/nba'), ('soccer', 'kalshi_epl_all', 'soccer/eng.1')]:
        sample = RAW / (sport + '_sample_markets.json')
        frozen = MANIFESTS / sample.name
        if not sample.exists() and frozen.exists():
            shutil.copyfile(frozen, sample)
        if not sample.exists():
            events = {}
            for market in json.loads((RAW / (catalog + '.json')).read_text())['markets']:
                events.setdefault(market['event_ticker'], market)
                if len(events) == 20:
                    break
            sample.write_text(json.dumps(list(events.values()), indent=2))
        for market in json.loads(sample.read_text()):
            day = datetime.datetime.strptime(market['event_ticker'].split('-')[1][:7], '%y%b%d').strftime('%Y%m%d')
            urls[sport + '_schedule_' + day] = f'https://site.api.espn.com/apis/site/v2/sports/{league}/scoreboard?dates={day}'
    urls['soccer_schedule_20260515'] = 'https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/scoreboard?dates=20260515'
    euro_matches = json.loads((RAW / 'statsbomb_matches.json').read_text())
    for match in sorted(euro_matches, key=lambda g: (g['match_date'], g['kick_off'], g['match_id']), reverse=True)[:20]:
        day = match['match_date'].replace('-', '')
        urls['euro_schedule_' + day] = 'https://site.api.espn.com/apis/site/v2/sports/soccer/uefa.euro/scoreboard?dates=' + day
    batch(urls)
    collect()
    analyze()


if __name__ == '__main__':
    run()
