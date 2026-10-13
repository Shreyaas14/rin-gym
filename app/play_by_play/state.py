"""Reconstruct event-prefix state without inventing tracking or player identities.

Rows describe a recorded action and state after applying that record. They are
retrospective annotations, not verified live observations or forecasting inputs.
"""
from collections import Counter
from copy import deepcopy

ON_BALL = {'Pass', 'Carry', 'Shot', 'Ball Receipt*', 'Dribble', 'Miscontrol',
           'Dispossessed', 'Ball Recovery', 'Interception', 'Clearance', 'Goal Keeper'}


def seconds(timestamp):
    hour, minute, second = timestamp.split(':')
    return int(hour) * 3600 + int(minute) * 60 + float(second)


def build_states(events, frames, match):
    home = match['home_team']['home_team_id']
    away = match['away_team']['away_team_id']
    teams = [home, away]
    frame_map = {f['event_uuid']: f for f in frames}
    if len(frame_map) != len(frames):
        raise ValueError('Duplicate 360 event IDs')
    if len({e['id'] for e in events}) != len(events):
        raise ValueError('Duplicate event IDs')
    if any(a['index'] >= b['index'] for a, b in zip(events, events[1:])):
        raise ValueError('Events must be in strict source-index order')
    if any((a['period'], seconds(a['timestamp'])) > (b['period'], seconds(b['timestamp']))
           for a, b in zip(events, events[1:])):
        raise ValueError('Period clocks reverse; review this match before replay')
    score = {team: 0 for team in teams}
    shootout = {team: 0 for team in teams}
    active = {team: {} for team in teams}
    formations = {team: None for team in teams}
    cards = {}
    card_keys = set()
    rows, players = [], {}
    last_ball = None
    previous_possession = None
    previous_possession_team = None
    for event in events:
        kind = event['type']['name']
        team = event.get('team', {}).get('id')
        actor = event.get('player')
        actor_id = actor['id'] if actor else None
        t = seconds(event['timestamp'])
        period = event['period']
        if actor:
            players.setdefault(actor_id, {**actor, 'team_id': team})
        if kind in {'Starting XI', 'Tactical Shift'}:
            formations[team] = event['tactics']['formation']
            active[team] = {p['player']['id']: p['position']['name'] for p in event['tactics']['lineup']}
            for p in event['tactics']['lineup']:
                players[p['player']['id']] = {**p['player'], 'team_id': team, 'jersey_number': p.get('jersey_number')}
        if kind == 'Substitution':
            replacement = event['substitution']['replacement']
            position = active[team].pop(actor_id, event.get('position', {}).get('name', 'Unknown'))
            active[team][replacement['id']] = position
            players.setdefault(replacement['id'], {**replacement, 'team_id': team})
        if kind == 'Player Off':
            active[team].pop(actor_id, None)
        if kind == 'Player On':
            active[team][actor_id] = event.get('position', {}).get('name', 'Unknown')
        card = (event.get('foul_committed', {}).get('card') or event.get('bad_behaviour', {}).get('card') or {}).get('name')
        key = (period, t, actor_id, card)
        if card and key not in card_keys:
            card_keys.add(key)
            cards.setdefault(actor_id, []).append(card)
            if card in {'Red Card', 'Second Yellow'}:
                active[team].pop(actor_id, None)
        before = dict(score)
        goal_team = None
        if kind == 'Shot' and event['shot']['outcome']['name'] == 'Goal':
            goal_team = team
        elif kind == 'Own Goal Against':
            goal_team = away if team == home else home
        if goal_team:
            (shootout if period == 5 else score)[goal_team] += 1
        action = event.get(kind.lower().replace(' ', '_').replace('*', ''), {})
        if kind == 'Ball Receipt*':
            action = event.get('ball_receipt', {})
        end = action.get('end_location')
        outcome = action.get('outcome', {}).get('name')
        if kind == 'Pass' and outcome is None:
            outcome = 'Complete'
        source_frame = frame_map.get(event['id'])
        frame_kind = '360' if source_frame else None
        visible = []
        if source_frame:
            for person in source_frame['freeze_frame']:
                person_team = team if person['teammate'] else (away if team == home else home)
                # 360 supplies an identity only through the linked event actor.
                visible.append({**person, 'team_id': person_team,
                                'player_id': actor_id if person.get('actor') else None})
        elif kind == 'Shot' and action.get('freeze_frame'):
            frame_kind = 'shot'
            for person in action['freeze_frame']:
                person_team = team if person['teammate'] else (away if team == home else home)
                visible.append({**person, 'team_id': person_team,
                                'player_id': person.get('player', {}).get('id'), 'actor': False,
                                'keeper': person.get('position', {}).get('name') == 'Goalkeeper'})
        if event.get('location') and kind in ON_BALL:
            last_ball = {'location': event['location'], 'team_id': team, 'period': period,
                         'period_seconds': t, 'event_id': event['id'], 'event_type': kind}
        # Do not carry a position through halftime, or pretend it is the current ball.
        if last_ball and last_ball['period'] != period:
            last_ball = None
        possession = event['possession']
        possession_team = event['possession_team']['id']
        rows.append({
            'id': event['id'], 'index': event['index'], 'period': period,
            'period_seconds': t, 'minute': event['minute'], 'second': event['second'],
            'type': kind, 'team_id': team, 'player_id': actor_id,
            'possession': possession, 'possession_team_id': possession_team,
            'new_possession': previous_possession is not None and possession != previous_possession,
            'possession_team_changed': previous_possession_team is not None and possession_team != previous_possession_team,
            'play_pattern': event['play_pattern']['name'],
            'location': event.get('location'), 'end_location': end,
            'duration': event.get('duration'), 'outcome': outcome,
            'recipient': action.get('recipient'), 'replacement': event.get('substitution', {}).get('replacement'),
            'under_pressure': event.get('under_pressure', False), 'counterpress': event.get('counterpress', False),
            'off_camera': event.get('off_camera', False), 'out': event.get('out', False),
            'card': card, 'goal_team_id': goal_team,
            'score_before': before, 'score_after': dict(score), 'shootout_score_after': dict(shootout),
            'active_players': deepcopy(active), 'formations': dict(formations), 'cards': deepcopy(cards),
            'last_on_ball_event': dict(last_ball) if last_ball else None,
            'visible_players': visible, 'frame_kind': frame_kind,
            'visible_area': source_frame.get('visible_area', []) if source_frame else [],
            'source_event': event,
        })
        previous_possession = possession
        previous_possession_team = possession_team
    counts = Counter(e['type']['name'] for e in events)
    return {
        'schema_version': 1, 'mode': 'retrospective_recorded_actions', 'forecast_ready': False,
        'coordinate_system': 'StatsBomb 120 x 80; actor team attacks towards x=120',
        'state_semantics': 'State after the selected source record. Action outcomes and retrospective labels are included; live availability is not established.',
        'match': {'id': match['match_id'], 'date': match['match_date'],
                  'home': {'id': home, 'name': match['home_team']['home_team_name']},
                  'away': {'id': away, 'name': match['away_team']['away_team_name']}},
        'players': players, 'events': rows,
        'summary': {'events': len(rows), 'event_types': dict(counts),
                    'possession_sequences': len({e['possession'] for e in events}),
                    'possession_team_changes': sum(e['possession_team_changed'] for e in rows),
                    'events_with_360': sum(e['frame_kind'] == '360' for e in rows),
                    'events_with_shot_frame': sum(e['frame_kind'] == 'shot' for e in rows),
                    'coordinate_events': sum(e['location'] is not None for e in rows),
                    'unmatched_360_frames': len(set(frame_map) - {e['id'] for e in events}),
                    'reconstructed_score': score,
                    'final_score_matches_catalog': [score[home], score[away]] == [match['home_score'], match['away_score']]},
    }
