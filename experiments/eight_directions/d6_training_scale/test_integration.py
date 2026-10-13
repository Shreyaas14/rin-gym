"""Meaningful offline checks for loss, serialization and sealed dataset boundaries."""
import json
import unittest

import numpy as np

from experiments.forecast_only.core import parse
from experiments.eight_directions.d6_training_scale.analyze import OUT, extract, proper_probability_loss, serialize


class IntegrationTests(unittest.TestCase):
    def test_proper_scores_minimize_expected_outcome_loss(self):
        true = np.array([.2, .3, .5])
        for kind in ('nll', 'brier'):
            def expected(predicted):
                return sum(true[i] * proper_probability_loss(np.log(predicted), np.eye(3)[i], kind)[0] for i in range(3))
            for wrong in ([.4, .3, .3], [.1, .1, .8], [1 / 3] * 3):
                self.assertLess(expected(true), expected(np.array(wrong)))

    def test_gradient_matches_finite_difference(self):
        logits, target = np.array([-.3, .2, .9]), np.array([0., 1., 0.])
        for kind in ('nll', 'brier'):
            _, grad = proper_probability_loss(logits, target, kind)
            numeric = []
            for i in range(3):
                delta = np.eye(3)[i] * 1e-6
                numeric.append((proper_probability_loss(logits + delta, target, kind)[0] - proper_probability_loss(logits - delta, target, kind)[0]) / 2e-6)
            np.testing.assert_allclose(grad, numeric, atol=1e-7)

    def test_serialized_distributions_have_valid_sum_and_bounded_error(self):
        rng = np.random.default_rng(20261013)
        values = [np.array([0., 0., 1.]), np.array([1 / 3] * 3), np.array([.21, .59, .195])]
        values.extend(rng.dirichlet([.2, .2, .2], 500))
        for value in values:
            parsed = parse(serialize(value))
            self.assertIsNotNone(parsed)
            np.testing.assert_allclose([parsed[o] for o in ('home', 'draw', 'away')], value / value.sum(), atol=1.1e-6)

    def test_tolerance_does_not_allow_extra_keys_duplicate_keys_or_boolean(self):
        for text in ['{"probs":{"home":0.4,"draw":0.3,"away":0.3},"action":"buy_home"}',
                     '{"probs":{"home":0.4,"home":0.4,"draw":0.3,"away":0.3}}',
                     '{"probs":{"home":true,"draw":0,"away":0}}']:
            self.assertIsNone(extract({'completion': text}, .05)[0])
        record = {'completion': '{"probs":{"home":0.21,"draw":0.59,"away":0.195}}'}
        self.assertIsNone(extract(record)[0])
        self.assertIsNotNone(extract(record, .05)[0])

    def test_training_records_keep_scoring_labels_separate_and_match_weights_equal(self):
        records = [json.loads(line) for line in (OUT / 'train_only_integration.jsonl').read_text().splitlines()]
        self.assertEqual(len(records), 80)
        weight = {}
        for row in records:
            self.assertEqual(row['split'], 'train')
            self.assertNotIn('scoring', row)
            self.assertNotIn('training_only_class_target', json.dumps(row['messages']))
            self.assertEqual(set(row['messages'][0]), {'role', 'content'})
            self.assertEqual(sum(row['training_only_class_target']), 1.)
            weight[row['match_id']] = weight.get(row['match_id'], 0) + row['match_weight']
        self.assertEqual(len(weight), 16)
        np.testing.assert_allclose(list(weight.values()), np.ones(16) / 16)


if __name__ == '__main__':
    unittest.main()
