import unittest
from experiments.forecast_only.risk_policy import RiskPolicy, choose_entry, replay_match
from app.trade_env.config import make_config

class RiskTests(unittest.TestCase):
    def setUp(self):
        self.config = make_config()
        self.task = {'id':'x','match_id':'m','position':'flat','t':1,'decision':'ht',
                     'scoring':{'shown_prices':{'home':.5,'draw':.3,'away':.2},
                                'fill_prices':{'home':.5,'draw':.3,'away':.2},'result':'away'}}
        self.probs = {'x':{'home':.8,'draw':.1,'away':.1}}
    def test_expensive_entries_rejected(self):
        self.assertIsNone(choose_entry({'home':.95,'draw':.03,'away':.02},
                                      {'home':.8,'draw':.1,'away':.1},self.config,RiskPolicy()))
    def test_delayed_fill_cannot_breach_limit(self):
        self.task['scoring']['fill_prices']['home'] = .9
        result = replay_match([self.task],self.probs,self.config,RiskPolicy())
        self.assertFalse(result['traded']); self.assertEqual(result['pnl'],0)
        self.assertFalse(result['attempts'][0]['accepted'])
    def test_loss_is_bounded_and_size_scales_both_outcomes(self):
        for outcome in ('home','away'):
            self.task['scoring']['result'] = outcome
            small = replay_match([self.task],self.probs,self.config,RiskPolicy())
            large = replay_match([self.task],self.probs,self.config,RiskPolicy(1,.05,.65))
            self.assertAlmostEqual(small['pnl'],large['pnl']*.25)
            self.assertGreaterEqual(small['pnl'],-.25*.65)
    def test_invalid_forecast_abstains(self):
        self.assertFalse(replay_match([self.task],{'x':None},self.config,RiskPolicy())['traded'])

if __name__ == '__main__': unittest.main()
