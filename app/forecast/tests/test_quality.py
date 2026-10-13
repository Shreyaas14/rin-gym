"""Evaluate the evaluator using independent arithmetic and adversarial inputs."""
import math
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from app.forecast.evaluate import select_temperature
from app.forecast.metrics import scores, summarize, paired_delta
from app.forecast.replay import Replay, Observation, market_vector
from app.forecast.model import Simulator
from app.forecast.tests.test_harness import event

CONFIG = {'log_loss_clip': 1e-6, 'bootstrap_replicates': 200, 'seed': 9,
          'temperature_candidates': [.7, 1., 1.3]}


class QualityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict('os.environ', {'RIN_GYM_WORKSPACE': self.tmp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_independent_scalar_score_reference(self):
        rng = np.random.default_rng(42)
        p = rng.dirichlet([1, 2, 3], 80)
        y = rng.integers(0, 3, 80)
        actual = scores(p, y)
        expected = [sum((float(v) - int(k == int(target))) ** 2 for k, v in enumerate(row))
                    for row, target in zip(p, y)]
        np.testing.assert_allclose(actual['brier'], expected, rtol=0, atol=1e-14)
        np.testing.assert_allclose(actual['log_loss'], [-math.log(max(1e-6, row[target])) for row, target in zip(p, y)])

    def test_duplicate_all_checkpoints_in_one_game_does_not_reweight_it(self):
        p = [[.6, .3, .1], [.4, .3, .3], [.1, .2, .7]]
        a = summarize(p, [0, 0, 1], ['a', 'a', 'b'], CONFIG)
        b = summarize(p[:2] * 20 + p[2:], [0, 0] * 20 + [1], ['a', 'a'] * 20 + ['b'], CONFIG)
        for key in ['brier', 'log_loss', 'brier_ci95', 'log_loss_ci95']:
            np.testing.assert_allclose(a[key], b[key])

    def test_overconfidence_is_penalized_on_known_distribution(self):
        # Equal class frequency is known here; any direction-specific overconfidence is wrong.
        y = [0, 1, 2] * 20
        calibrated = scores([[1 / 3] * 3] * len(y), y)
        overconfident = scores([[.98, .01, .01]] * len(y), y)
        for key in ['brier', 'log_loss']:
            self.assertGreater(np.mean(overconfident[key]), np.mean(calibrated[key]))

    def test_labels_and_probability_contract_fail_closed(self):
        for target in [-1, 3, .5, True, '1']:
            with self.subTest(target=target), self.assertRaises(ValueError):
                scores([[.4, .3, .3]], [target])
        for p in [[float('nan'), .5, .5], [float('inf'), 0, 0], [-.1, .5, .6], [.5, .5, .000001]]:
            with self.subTest(p=p), self.assertRaises(ValueError):
                scores([p], [0])
        with self.assertRaises(ValueError):
            scores([], [])
        with self.assertRaises(ValueError):
            scores([[.4, .3, .3]], [0], clip=0)

    def test_split_leak_and_length_mismatch_rejected(self):
        with self.assertRaisesRegex(ValueError, 'whole match'):
            select_temperature([[.5, .2, .3]] * 2, [0, 0], ['same', 'same'], ['train', 'validation'], CONFIG)
        with self.assertRaisesRegex(ValueError, 'align'):
            select_temperature([[.5, .2, .3]], [0, 1], ['a'], ['validation'], CONFIG)

    def test_extreme_test_labels_and_predictions_do_not_change_selection(self):
        # Test values are intentionally invalid: selection must not inspect them.
        a = select_temperature([[.5, .2, .3], [.2, .3, .5]], [0, 2], ['v', 't'], ['validation', 'test'], CONFIG)
        b = select_temperature([[.5, .2, .3], [float('nan')] * 3], [0, 999], ['v', 't'], ['validation', 'test'], CONFIG)
        self.assertEqual(a, b)

    def test_paired_comparison_rejects_different_games(self):
        with self.assertRaises(ValueError):
            paired_delta({'brier_by_game': {'a': .2}}, {'brier_by_game': {'b': .2}}, 'brier', CONFIG)

    def test_future_event_append_and_mutation_cannot_change_prefix(self):
        past = [event('2026-06-01T10:00:00Z', 'Start Time')]
        cutoff = 1780308600
        a = Replay(past).observe(cutoff)
        future = event('2026-06-01T10:20:00Z', 'Goal!', home=9)
        self.assertEqual(a, Replay(past + [future]).observe(cutoff))
        future['TypeLocalized'][0]['Description'] = 'End Time'
        self.assertEqual(a, Replay(past + [future]).observe(cutoff))

    def test_future_price_cannot_change_current_market(self):
        series = [[{'t': 100, 'p': p}] for p in [.5, .3, .2]]
        a = market_vector(series, 110, 30)
        for history in series:
            history.append({'t': 111, 'p': .99})
        b = market_vector(series, 110, 30)
        np.testing.assert_array_equal(a['probabilities'], b['probabilities'])
        self.assertIsNone(market_vector(series, 200, 30))
