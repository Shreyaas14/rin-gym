"""Independent terminal portfolio algebra checks for the scientific audit."""
import random
import unittest

from app.trade_env.config import OUTCOMES
from app.trade_env.costs import buy_cost, sell_proceeds
from app.trade_env.reward import legal_actions, next_position, noop
from experiments.eight_directions.d1_consistency.analyze import values, select


class PortfolioAlgebra(unittest.TestCase):
    def setUp(self):
        self.config = {'fee_rate': .05, 'half_spread': .01}

    def test_all_actions_against_direct_cash_and_terminal_payoff(self):
        rng = random.Random(202610131)
        for _ in range(100):
            raw = {o: rng.random() for o in OUTCOMES}
            probs = {o: raw[o]/sum(raw.values()) for o in OUTCOMES}
            prices = {o: rng.random() for o in OUTCOMES}
            for position in ['flat', *OUTCOMES]:
                table = values(probs, prices, position, self.config)
                self.assertEqual(set(table), set(legal_actions(position)))
                for action in legal_actions(position):
                    cash = 0.
                    if action == 'close' or action.startswith('switch_'):
                        cash += sell_proceeds(prices[position], self.config, position)
                    if action.startswith(('buy_', 'switch_')):
                        side = action.split('_', 1)[1]
                        cash -= buy_cost(prices[side], self.config, side)
                    after = next_position(position, action)
                    final_ev = sum(probs[outcome]*(cash + float(outcome == after)) for outcome in OUTCOMES)
                    before_ev = probs.get(position, 0.)
                    self.assertAlmostEqual(table[action], final_ev-before_ev)
                selected = select(probs, prices, position, self.config, .02)
                self.assertGreaterEqual(table[selected], 0.)
                if selected != noop(position):
                    self.assertGreater(table[selected], .02)

    def test_switch_positive_new_edge_can_destroy_full_position_value(self):
        probs = {'home': .7, 'draw': .15, 'away': .15}
        prices = {'home': .5, 'draw': .1, 'away': .4}
        self.assertGreater(probs['draw'] - buy_cost(prices['draw'], self.config, 'draw'), 0.)
        table = values(probs, prices, 'home', self.config)
        self.assertLess(table['switch_draw'], 0.)
        self.assertEqual(select(probs, prices, 'home', self.config, .02), 'keep')

    def test_invalid_forecast_holds_and_delayed_execution_can_flip_edge(self):
        for position in ['flat', *OUTCOMES]:
            self.assertEqual(select(None, {}, position, self.config, .02), noop(position))
        probs = {'home': .5, 'draw': .3, 'away': .2}
        shown = {'home': .4, 'draw': .3, 'away': .3}
        delayed = {'home': .6, 'draw': .3, 'away': .3}
        action = select(probs, shown, 'flat', self.config, .02)
        self.assertEqual(action, 'buy_home')
        self.assertGreater(values(probs, shown, 'flat', self.config)[action], 0)
        self.assertLess(values(probs, delayed, 'flat', self.config)[action], 0)


if __name__ == '__main__':
    unittest.main()
