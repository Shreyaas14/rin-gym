import unittest

from experiments.eight_directions.d8_specialization.analyze import membership, probabilities, summary


class ObservableScreenTests(unittest.TestCase):
    def test_slice_inputs_and_prefix_red_parsing(self):
        shown = {'home': .5, 'draw': .3, 'away': .2}
        slices, evidence = membership('h2+30', {'home': 1, 'away': 1},
            'Red cards: Côte d’Ivoire 1, South Africa 0\n', shown, {'home': .7, 'draw': .2, 'away': .1})
        self.assertEqual(slices, {'tied_late': True, 'observed_red_card': True,
                                  'halftime': False, 'large_observed_price_move': True})
        self.assertEqual(evidence['red_counts'], [1, 0])

    def test_no_first_quote_movement_or_system_rule_false_positive(self):
        shown = {'home': .5, 'draw': .3, 'away': .2}
        slices, evidence = membership('ht', {'home': 1, 'away': 0},
                                     'A red card removes one player.\nRed cards: A 0, B 0\n', shown, None)
        self.assertFalse(slices['large_observed_price_move'])
        self.assertFalse(slices['observed_red_card'])
        self.assertTrue(slices['halftime'])
        self.assertIsNone(evidence['max_observed_price_change'])
        _, unsupported = membership('ht', None, 'red card in future\n', shown, None)
        self.assertFalse(unsupported['red_parse_supported'])

    def test_exact_predeclared_decimal_movement_boundary(self):
        slices, evidence = membership('ht', None, '', {'home': .6, 'draw': .2, 'away': .2},
                                       {'home': .45, 'draw': .25, 'away': .3})
        self.assertTrue(slices['large_observed_price_move'])
        self.assertEqual(evidence['max_observed_price_change'], .15)

    def test_original_validity_tolerance_and_truncation(self):
        step = {'valid': True, 'probs': {'home': .5, 'draw': .3, 'away': .19}}
        self.assertAlmostEqual(sum(probabilities(step).values()), 1.)
        self.assertIsNone(probabilities({**step, 'truncated': True}))
        self.assertIsNone(probabilities({**step, 'valid': False}))
        self.assertIsNone(probabilities({**step, 'probs': {'home': .5, 'draw': .3, 'away': float('nan')}}))

    def test_equal_match_weight_preserves_repeats_and_singleton_uncertainty(self):
        def row(mid, value):
            return {'match_id': mid, 'model_probabilities': {'home': 1},
                    'model': {'brier': value, 'log_loss': value}, 'market': {'brier': 0., 'log_loss': 0.}}
        rows = [row('a', 1)] * 9 + [row('b', 0)]
        result = summary(rows, 2, 10)
        self.assertEqual(result['paired_improvement']['brier']['mean'], -.5)
        singleton = summary(rows[:9], 1, 9)
        self.assertIsNone(singleton['paired_improvement']['brier']['match_bootstrap_95'])
        self.assertFalse(singleton['bootstrap_estimable'])


if __name__ == '__main__':
    unittest.main()
