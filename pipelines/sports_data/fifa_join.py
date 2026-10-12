"""Download two FIFA/Polymarket examples and join event coordinates to preceding prices."""
from .cache import fetch, ROOT
from .collect import load
import bisect
import concurrent.futures
import csv
import datetime as dt
import json

MATCHES = {'400021504': 'fifwc-uzb-col-2026-06-17', '400021440': 'fifwc-cze-rsa-2026-06-18'}


def epoch(value):
    return dt.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def utc(value):
    return dt.datetime.fromtimestamp(value, dt.timezone.utc).isoformat()


def batch(urls):
    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        for name, data in pool.map(lambda item: fetch(*item), urls.items()):
            if isinstance(data, dict) and 'error' in data:
                raise RuntimeError(f'{name}: {data["error"]}')


def main():
    urls = {'fifa_calendar_2026': 'https://api.fifa.com/api/v3/calendar/matches?language=en&count=500&idSeason=285023'}
    for mid, slug in MATCHES.items():
        urls[f'fifa_{mid}_timeline'] = f'https://api.fifa.com/api/v3/timelines/{mid}?language=en'
        urls[f'fifa_poly_{mid}'] = 'https://gamma-api.polymarket.com/events/slug/' + slug
    batch(urls)
    calendar = {m['IdMatch']: m for m in load('fifa_calendar_2026')['Results']}
    urls = {}
    for mid in MATCHES:
        events = load(f'fifa_{mid}_timeline')['Event']
        start = int(min(epoch(e['Timestamp']) for e in events)) - 300
        end = int(max(epoch(e['Timestamp']) for e in events)) + 300
        for market in load(f'fifa_poly_{mid}')['markets']:
            assert market['sportsMarketType'] == 'moneyline'
            assert json.loads(market['outcomes'])[0] == 'Yes'
            token = json.loads(market['clobTokenIds'])[0]
            urls['fifa_prices_' + market['id']] = f'https://clob.polymarket.com/prices-history?market={token}&startTs={start}&endTs={end}&fidelity=1'
    batch(urls)
    rows = []
    summaries = []
    for mid in MATCHES:
        match = calendar[mid]
        events = load(f'fifa_{mid}_timeline')['Event']
        home = match['Home']['TeamName'][0]['Description']
        away = match['Away']['TeamName'][0]['Description']
        markets = load(f'fifa_poly_{mid}')['markets']
        start = epoch(match['Date'])
        assert all(epoch(m['gameStartTime']) == start for m in markets)
        times = [epoch(e['Timestamp']) for e in events]
        assert len({e['EventId'] for e in events}) == len(events)
        assert all(start - 600 <= t <= start + 4 * 3600 for t in times)
        summary = {'fifa_match_id': mid, 'fdh_match_id': match['Properties']['IdIFES'], 'match': home + ' vs ' + away, 'scheduled_utc': match['Date'], 'events': len(events), 'events_with_coordinates': sum(e.get('PositionX') is not None and e.get('PositionY') is not None for e in events), 'timestamp_reversals': sum(b < a for a, b in zip(times, times[1:])), 'markets': []}
        for market in markets:
            prices = load('fifa_prices_' + market['id'])['history']
            ptimes = [p['t'] for p in prices]
            assert ptimes == sorted(set(ptimes)) and len(prices) > 1
            ages = []
            for event in events:
                etime = epoch(event['Timestamp'])
                # Strictly preceding sample, never nearest-neighbor or interpolation.
                idx = bisect.bisect_left(ptimes, etime) - 1
                price = prices[idx] if idx >= 0 else None
                age = etime - price['t'] if price else None
                usable = age is not None and age <= 90
                if age is not None:
                    ages.append(age)
                rows.append({'fifa_match_id': mid, 'match': summary['match'], 'event_id': event['EventId'], 'event_utc': event['Timestamp'], 'period': event['Period'], 'match_minute': event['MatchMinute'], 'event_type': event['TypeLocalized'][0]['Description'], 'event_description': next((d['Description'] for d in event.get('EventDescription', [])), ''), 'team_id': event.get('IdTeam'), 'player_id': event.get('IdPlayer'), 'position_x_raw': event.get('PositionX'), 'position_y_raw': event.get('PositionY'), 'home_goals': event['HomeGoals'], 'away_goals': event['AwayGoals'], 'market_id': market['id'], 'market_question': market['question'], 'yes_token_id': json.loads(market['clobTokenIds'])[0], 'price_utc': utc(price['t']) if price else None, 'preceding_yes_price': price['p'] if price else None, 'price_age_seconds': round(age, 3) if age is not None else None, 'within_90s': usable})
            summary['markets'].append({'id': market['id'], 'question': market['question'], 'price_samples': len(prices), 'distinct_prices': len({p['p'] for p in prices}), 'max_gap_seconds': max(b-a for a,b in zip(ptimes, ptimes[1:])), 'events_with_preceding_price': len(ages), 'events_with_price_within_90s': sum(age <= 90 for age in ages), 'max_price_age_seconds': round(max(ages), 3) if ages else None})
        summaries.append(summary)
    with (ROOT / 'fifa-polymarket-joined.csv').open('w') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    (ROOT / 'fifa-polymarket-summary.json').write_text(json.dumps(summaries, indent=2))
    print(json.dumps(summaries, indent=2))
    print('Joined rows:', len(rows))


if __name__ == '__main__':
    main()
