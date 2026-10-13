import unittest
from experiments.forecast_only.loss_audit import overlay
from app.trade_env.costs import sell_proceeds


class OverlayTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {'fee_rate': .05, 'half_spread': .01}
        self.trade = {'side':'home', 'entry_action':'buy_home', 'shown_cost':.6,
            'entry_cost':.6, 'edge':.05, 'pnl_per_share':.4,
            'checkpoints':[{'decision':'ht','shown':.5,'fill':.1,'prob':.6}]}

    def test_future_fill_does_not_trigger_stop(self):
        self.assertEqual(overlay(self.trade, {'stop':.25}, self.cfg), .4)

    def test_stop_executes_at_delayed_fill_not_threshold(self):
        self.trade['checkpoints'][0]['shown'] = .3
        self.assertAlmostEqual(overlay(self.trade, {'stop':.25}, self.cfg),
                               sell_proceeds(.1,self.cfg,'home')-.6)

    def test_entry_filter_uses_quote_not_eventual_outcome(self):
        self.assertIsNone(overlay(self.trade, {'cap':.5}, self.cfg))
        self.trade['pnl_per_share'] = -.6
        self.assertIsNone(overlay(self.trade, {'cap':.5}, self.cfg))

    def test_no_later_checkpoint_means_no_early_exit(self):
        self.trade['checkpoints'] = []
        self.assertEqual(overlay(self.trade, {'exit_decision':'h2+30'}, self.cfg),.4)

    def test_original_identity(self):
        self.assertEqual(overlay(self.trade, {}, self.cfg),.4)

if __name__ == '__main__': unittest.main()
