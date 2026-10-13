"""Cache World Cup timelines and export their event coordinates and timestamps."""
from .cache import ROOT
from .collect import load
from .fifa_join import batch, epoch
import collections
import csv
import json


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    batch({'fifa_calendar_2026': 'https://api.fifa.com/api/v3/calendar/matches?language=en&count=500&idSeason=285023'})
    matches = load('fifa_calendar_2026')['Results']
    batch({f'fifa_{m["IdMatch"]}_timeline': f'https://api.fifa.com/api/v3/timelines/{m["IdMatch"]}?language=en' for m in matches})
    census, records = [], []
    for match in matches:
        mid = match['IdMatch']
        events = load(f'fifa_{mid}_timeline')['Event']
        times = [epoch(e['Timestamp']) for e in events if e.get('Timestamp')]
        start = epoch(match['Date'])
        row = {'id': mid, 'home': match['Home']['TeamName'][0]['Description'], 'away': match['Away']['TeamName'][0]['Description'], 'date': match['Date'], 'events': len(events), 'coordinates': sum(e.get('PositionX') is not None and e.get('PositionY') is not None for e in events), 'utc_count': len(times), 'plausible_utc_count': sum(start - 600 <= t <= start + 18000 for t in times), 'utc_reversals': sum(b < a for a, b in zip(times, times[1:])), 'types': dict(collections.Counter(e['TypeLocalized'][0]['Description'] for e in events))}
        census.append(row)
        for event in events:
            records.append({'match_id': mid, 'fdh_match_id': match['Properties']['IdIFES'], 'home': row['home'], 'away': row['away'], 'scheduled_utc': match['Date'], 'event_id': event['EventId'], 'event_utc': event.get('Timestamp'), 'period': event.get('Period'), 'match_minute': event.get('MatchMinute'), 'event_type': event['TypeLocalized'][0]['Description'], 'team_id': event.get('IdTeam'), 'player_id': event.get('IdPlayer'), 'position_x_raw': event.get('PositionX'), 'position_y_raw': event.get('PositionY'), 'home_goals': event.get('HomeGoals'), 'away_goals': event.get('AwayGoals')})
    (ROOT / 'fifa-timeline-census.json').write_text(json.dumps(census, indent=2))
    with (ROOT / 'fifa-events.csv').open('w') as file:
        writer = csv.DictWriter(file, fieldnames=list(records[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(records)
    print(json.dumps({'matches': len(census), 'with_coordinates': sum(r['coordinates'] > 0 for r in census), **{k: sum(r[k] for r in census) for k in ['events', 'coordinates', 'utc_count', 'plausible_utc_count', 'utc_reversals']}}, indent=2))


if __name__ == '__main__':
    main()
