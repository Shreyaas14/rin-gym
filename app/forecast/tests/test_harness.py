import copy
import unittest
from dataclasses import fields
import numpy as np
from app.forecast.data import ROOT, read, split_for, normalize
from app.forecast.replay import Replay, Observation, asof, market_vector
from app.forecast.model import Simulator, outcome_probabilities, temperature_scale
from app.forecast.metrics import scores, by_game, paired_delta
from app.forecast.evaluate import select_temperature


def event(timestamp, kind='Foul', period=3, home=0, away=0, identity='x'):
    return {'EventId': identity, 'Timestamp': timestamp, 'TypeLocalized': [{'Description': kind}], 'Period': period, 'HomeGoals': home, 'AwayGoals': away}


class ReplayTests(unittest.TestCase):
    def test_future_events_and_boundaries_cannot_change_past_observation(self):
        past = event('2026-06-01T10:00:00Z', 'Start Time')
        future = event('2026-06-01T10:20:00Z', 'Goal!', home=1)
        a = Replay([past, future]).observe(1780308600)  # 10:10 UTC
        future['HomeGoals'] = 9
        future['TypeLocalized'][0]['Description'] = 'End Time'
        b = Replay([past, future]).observe(1780308600)
        self.assertEqual(a, b)
        self.assertEqual(a.home_goals, 0)
        self.assertEqual(a.phase, 'first_half')

    def test_simultaneous_goal_and_stale_save_use_same_batch_only(self):
        events = [event('2026-06-01T10:00:00Z', 'Start Time'), event('2026-06-01T10:10:00Z', 'Goal!', home=1), event('2026-06-01T10:10:00Z', 'Goal Prevention', home=0)]
        replay = Replay(events)
        self.assertEqual(replay.observe(1780308599).home_goals, 0)
        self.assertEqual(replay.observe(1780308600).home_goals, 1)

    def test_halftime_is_visible_only_after_observed_whistle(self):
        events = [event('2026-06-01T10:00:00Z', 'Start Time'), event('2026-06-01T10:50:00Z', 'End Time'), event('2026-06-01T11:05:00Z', 'Start Time', 5)]
        self.assertEqual(Replay(events).observe(1780311300).phase, 'halftime')

    def test_observation_has_no_label_or_future_end_field(self):
        names = {f.name for f in fields(Observation)}
        self.assertNotIn('target', names)
        self.assertNotIn('regulation_end', names)

    def test_market_asof_never_uses_future_and_does_not_fill_stale(self):
        series = [{'t': 100, 'p': .3}, {'t': 160, 'p': .9}]
        self.assertEqual(asof(series, 159, 90)['p'], .3)
        self.assertIsNone(asof(series, 99, 90))
        self.assertIsNone(asof(series, 300, 90))
        self.assertEqual(asof(series, 160, 90)['p'], .9)

    def test_three_markets_must_all_be_fresh(self):
        histories = [[{'t': 100, 'p': .6}], [{'t': 100, 'p': .3}], [{'t': 10, 'p': .2}]]
        self.assertIsNone(market_vector(histories, 150, 90))
        histories[2][0]['t'] = 100
        result = market_vector(histories, 150, 90)
        np.testing.assert_allclose(result['probabilities'], np.array([.6, .3, .2]) / 1.1)
        self.assertAlmostEqual(result['mass'], 1.1)

    def test_dates_keep_simultaneous_matches_together(self):
        cfg = read(ROOT / 'config.json')
        self.assertEqual(split_for('2026-06-21T23:59:00Z', cfg), 'train')
        self.assertEqual(split_for('2026-06-22T00:00:00Z', cfg), 'validation')
        self.assertEqual(split_for('2026-06-25T00:00:00Z', cfg), 'test')

    def test_team_aliases(self):
        self.assertEqual(normalize('Bosnia and Herzegovina'), normalize('Bosnia-Herzegovina'))
        self.assertEqual(normalize('Congo DR'), normalize('DR Congo'))


class SimulatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = read(ROOT / 'config.json')
        cls.model = Simulator({'goal_rates_per_minute': [.02, .015], 'half_duration_minutes': [[48, 50, 52], [50, 52, 54]], 'mean_total_minutes': 102, 'climatology': [.4, .3, .3]}, cls.config)

    def test_zero_remaining_goals_resolves_score_correctly(self):
        np.testing.assert_allclose(outcome_probabilities([0, 0, 0], [0, 0, 0], [1, 0, -1]), np.eye(3))

    def test_swap_symmetry(self):
        a = outcome_probabilities(1.7, .6, 1)[0]
        b = outcome_probabilities(.6, 1.7, -1)[0]
        np.testing.assert_allclose(a, b[::-1])

    def test_poisson_monte_carlo_matches_exact_distribution(self):
        obs = Observation(0, 1, 0, 'second_half', 20, 10, 0)
        result = self.model.simulate(obs, n=100000, seed=991)
        np.testing.assert_allclose(result['monte_carlo_probabilities'], result['analytic_probabilities'], atol=.008)

    def test_remaining_time_is_not_future_whistle(self):
        obs = Observation(0, 0, 0, 'second_half', 51, 10, 0)
        self.assertEqual(self.model.remaining_minutes(obs), 2)  # residuals 1 and 3
        beyond = Observation(0, 0, 0, 'second_half', 99, 10, 0)
        self.assertGreater(self.model.remaining_minutes(beyond), 0)

    def test_pregame_fit_approximates_distribution(self):
        rates, err = self.model.fit_pregame([.55, .25, .20])
        self.assertLess(err, .001)
        self.assertTrue(np.all(rates > 0))

    def test_fit_rejects_test_rows(self):
        with self.assertRaises(ValueError):
            Simulator.fit([{'split': 'test'}], self.config)

    def test_temperature_identity_and_normalization(self):
        p = np.array([[.2, .3, .5], [0, 0, 1]])
        np.testing.assert_allclose(temperature_scale(p, 1), p, atol=1e-10)
        self.assertAlmostEqual(float(temperature_scale(p, 2)[0].sum()), 1)

    def test_test_targets_cannot_change_temperature_selection(self):
        probs = np.array([[.7, .2, .1], [.9, .05, .05], [.1, .8, .1]])
        ids = ['validation_game', 'test_game', 'test_game']
        splits = ['validation', 'test', 'test']
        a = select_temperature(probs, [0, 0, 1], ids, splits, self.config)
        b = select_temperature(probs, [0, 2, 0], ids, splits, self.config)
        self.assertEqual(a, b)

    def test_test_forecasts_cannot_change_temperature_selection(self):
        probs = np.array([[.7, .2, .1], [.9, .05, .05]])
        args = ([0, 0], ['v', 't'], ['validation', 'test'], self.config)
        a = select_temperature(probs, *args)
        probs[1] = [0, 0, 1]
        self.assertEqual(a, select_temperature(probs, *args))


class MetricTests(unittest.TestCase):
    def test_brier_scale_and_log_loss(self):
        result = scores([[1, 0, 0], [0, 1, 0], [1/3]*3], [0, 0, 0])
        np.testing.assert_allclose(result['brier'], [0, 2, 2/3])
        self.assertAlmostEqual(result['log_loss'][2], np.log(3))

    def test_game_weighting_is_not_snapshot_weighting(self):
        result = by_game([0, 0, 0, 1], ['a', 'a', 'a', 'b'])
        self.assertEqual(np.mean(list(result.values())), .5)

    def test_paired_bootstrap_preserves_pairing(self):
        config = {'bootstrap_replicates': 100, 'seed': 1}
        a = {'brier_by_game': {'a': .2, 'b': .4}}
        b = {'brier_by_game': {'a': .3, 'b': .5}}
        delta = paired_delta(a, b, 'brier', config)
        np.testing.assert_allclose(delta['ci95'], [-.1, -.1])

    def test_invalid_probability_vectors_rejected(self):
        with self.assertRaises(ValueError):
            scores([[.8, .8, .8]], [0])


if __name__ == '__main__':
    unittest.main()
