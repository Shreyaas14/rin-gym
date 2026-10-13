import copy
import unittest

from app.play_by_play.state import build_states


MATCH = {'match_id': 1, 'match_date': '2024-07-14',
         'home_team': {'home_team_id': 1, 'home_team_name': 'Home'},
         'away_team': {'away_team_id': 2, 'away_team_name': 'Away'},
         'home_score': 0, 'away_score': 0}


def event(i, kind='Pass', team=1, **extra):
    return {'id': str(i), 'index': i, 'type': {'name': kind}, 'team': {'id': team},
            'player': {'id': 10 * team, 'name': 'Player'}, 'period': 1,
            'timestamp': f'00:00:{i:02}.000', 'minute': 0, 'second': i,
            'possession': 1, 'possession_team': {'id': 1}, 'play_pattern': {'name': 'Regular Play'}, **extra}


def lineup(i, team):
    return event(i, 'Starting XI', team, tactics={'formation': 433, 'lineup': [
        {'player': {'id': 10 * team, 'name': 'Starter'}, 'position': {'name': 'Midfield'}, 'jersey_number': 8}]})


class StateTests(unittest.TestCase):
    def test_future_events_do_not_change_past_states(self):
        prefix = [lineup(1, 1), lineup(2, 2), event(3, location=[40, 30], **{'pass': {'end_location': [60, 30]}})]
        future = event(4, 'Shot', shot={'outcome': {'name': 'Goal'}})
        short = build_states(prefix, [], MATCH)
        long = build_states(prefix + [future], [], MATCH)
        self.assertEqual(short['events'], long['events'][:len(prefix)])
        self.assertEqual(short['events'][-1]['score_after'], {1: 0, 2: 0})

    def test_anonymous_360_players_are_not_assigned_identities(self):
        e = event(1, location=[40, 30])
        frame = {'event_uuid': '1', 'visible_area': [], 'freeze_frame': [
            {'actor': True, 'teammate': True, 'keeper': False, 'location': [40, 30]},
            {'actor': False, 'teammate': True, 'keeper': False, 'location': [50, 30]},
            {'actor': False, 'teammate': False, 'keeper': False, 'location': [70, 40]}]}
        people = build_states([e], [frame], MATCH)['events'][0]['visible_players']
        self.assertEqual([p['player_id'] for p in people], [10, None, None])
        self.assertEqual([p['team_id'] for p in people], [1, 1, 2])

    def test_missing_frame_does_not_reuse_previous_positions(self):
        frame = {'event_uuid': '1', 'visible_area': [], 'freeze_frame': [
            {'actor': True, 'teammate': True, 'location': [40, 30]}]}
        rows = build_states([event(1), event(2)], [frame], MATCH)['events']
        self.assertEqual(rows[1]['visible_players'], [])
        self.assertIsNone(rows[1]['frame_kind'])

    def test_substitutions_and_dismissals_update_active_roster(self):
        es = [lineup(1, 1), event(2, 'Substitution', substitution={'replacement': {'id': 11, 'name': 'Replacement'}}),
              event(3, 'Bad Behaviour', player={'id': 11, 'name': 'Replacement'}, bad_behaviour={'card': {'name': 'Red Card'}})]
        rows = build_states(es, [], MATCH)['events']
        self.assertEqual(set(rows[0]['active_players'][1]), {10})
        self.assertEqual(set(rows[1]['active_players'][1]), {11})
        self.assertEqual(rows[2]['active_players'][1], {})

    def test_same_team_new_possession_is_not_a_turnover(self):
        rows = build_states([event(1), event(2, possession=2), event(3, possession=3, possession_team={'id': 2})], [], MATCH)['events']
        self.assertTrue(rows[1]['new_possession'])
        self.assertFalse(rows[1]['possession_team_changed'])
        self.assertTrue(rows[2]['possession_team_changed'])

    def test_score_does_not_double_count_own_goals_or_shootout(self):
        es = [event(1, 'Own Goal Against'), event(2, 'Own Goal For', team=2),
              event(3, 'Shot', period=5, shot={'outcome': {'name': 'Goal'}})]
        rows = build_states(es, [], MATCH)['events']
        self.assertEqual(rows[-1]['score_after'], {1: 0, 2: 1})
        self.assertEqual(rows[-1]['shootout_score_after'], {1: 1, 2: 0})

    def test_pressure_location_is_not_a_ball_observation(self):
        es = [event(1, location=[40, 30]), event(2, 'Pressure', team=2, location=[80, 50]),
              event(3, 'Half Start', period=2)]
        rows = build_states(es, [], MATCH)['events']
        self.assertEqual(rows[1]['last_on_ball_event']['event_id'], '1')
        self.assertIsNone(rows[2]['last_on_ball_event'])

    def test_source_order_and_duplicate_frames_fail_closed(self):
        with self.assertRaises(ValueError):
            build_states([event(2), event(1)], [], MATCH)
        frame = {'event_uuid': '1', 'freeze_frame': []}
        with self.assertRaises(ValueError):
            build_states([event(1)], [frame, copy.deepcopy(frame)], MATCH)

    def test_output_is_explicitly_retrospective_and_preserves_source(self):
        e = event(1, 'Carry', duration=4.5, carry={'end_location': [50, 30]})
        payload = build_states([e], [], MATCH)
        self.assertFalse(payload['forecast_ready'])
        self.assertEqual(payload['events'][0]['source_event'], e)
        self.assertEqual(payload['events'][0]['end_location'], [50, 30])


if __name__ == '__main__':
    unittest.main()
