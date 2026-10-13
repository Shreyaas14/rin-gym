import copy
import json
import unittest
from pathlib import Path

from app.trade_env.replay import Replay, by_match
from app.trade_env.tasks import read_tasks
from experiments.forecast_only.closed_loop import Budget, execute, observation, SOURCE


class ClosedLoopTests(unittest.TestCase):
    def setUp(self):
        self.config=json.loads((SOURCE/'comparison.json').read_text())['config']
        self.match=copy.deepcopy(next(iter(by_match(read_tasks(SOURCE/'tasks.jsonl.gz')).values())))
        self.replay=Replay(self.match,self.config)
        t=self.replay.next_task()
        self.side=min(t['scoring']['shown_prices'],key=t['scoring']['shown_prices'].get)
        self.answer=json.dumps({'probs':{o:float(o==self.side) for o in ['home','draw','away']},
            'next_goal':{'home':0,'away':0,'none':1},'future_prices':{'home':.3,'draw':.3,'away':.4},
            'action':'buy_'+self.side,'reason':'test'})
    def test_next_observation_uses_executed_position_and_quantity(self):
        out=execute(self.replay,self.answer,'conservative',self.config)
        self.assertEqual(out['executed_action'],'buy_'+self.side)
        next_task=self.replay.next_task()
        self.assertEqual(next_task['position'],self.side)
        content=observation(next_task,'conservative')[1]['content']
        self.assertIn('Position: 0.25 shares of '+self.side,content)
        self.assertNotIn('Position: 1 share of',content)
    def test_rejected_order_keeps_next_observation_flat(self):
        self.replay.next_task()['scoring']['fill_prices'][self.side]=.99
        out=execute(self.replay,self.answer,'conservative',self.config)
        self.assertEqual(out['execution_rejection'],'delayed_fill_above_limit')
        self.assertEqual(self.replay.next_task()['position'],'flat')
    def test_original_observation_unchanged_and_targets_hidden(self):
        task=self.replay.next_task();self.assertEqual(observation(task,'original'),task['messages'])
        before=observation(task,'conservative')
        task['scoring']['result']='SENTINEL';task['scoring']['fill_prices']={'SENTINEL':1}
        self.assertEqual(observation(task,'conservative'),before)
    def test_budget_refuses_extra_call(self):
        b=Budget(1);b.reserve(1000)
        with self.assertRaises(ValueError):b.reserve(1000)
        b=Budget(100,ceiling=.00001)
        with self.assertRaises(ValueError):b.reserve(1000)

if __name__=='__main__':unittest.main()
