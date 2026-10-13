import copy
import json
import unittest

from app.trade_env.tasks import read_tasks
from app.trade_env.replay import by_match, Replay
from experiments.forecast_only.quarter_original import observation, SOURCE, QUANTITY
from experiments.forecast_only.closed_loop import execute


class QuarterOriginalTests(unittest.TestCase):
    def setUp(self):
        self.config=json.loads((SOURCE/'comparison.json').read_text())['config']
        self.match=copy.deepcopy(next(m for m in by_match(read_tasks(SOURCE/'tasks.jsonl.gz')).values() if len({t['t'] for t in m})==5))
    def answer(self, action):
        return json.dumps({'probs':{'home':.4,'draw':.3,'away':.3},'action':action,'reason':'test',
                          'next_goal':{'home':0,'away':0,'none':1},'future_prices':{'home':.4,'draw':.3,'away':.3}})
    def test_original_close_switch_and_reentry_remain_executable(self):
        replay=Replay(self.match,self.config)
        for action,position in [('buy_home','home'),('switch_away','away'),('close','flat'),('buy_draw','draw'),('keep','draw')]:
            result=execute(replay,self.answer(action),'original',self.config)
            self.assertEqual(result['executed_action'],action)
            self.assertEqual(replay.position,position)
            if replay.next_task() is not None:
                prompt=observation(replay.next_task())[1]['content']
                self.assertIn('Position: none' if position=='flat' else 'Position: 0.25 shares of '+position,prompt)
    def test_no_conservative_price_cap_or_edge_filter(self):
        replay=Replay(self.match,self.config)
        replay.next_task()['scoring']['shown_prices']['home']=.99
        replay.next_task()['scoring']['fill_prices']['home']=.99
        out=execute(replay,self.answer('buy_home'),'original',self.config)
        self.assertEqual(out['executed_action'],'buy_home')
        self.assertIsNone(out['execution_rejection'])
    def test_legal_actions_and_future_data_boundary_unchanged(self):
        task=self.match[0];before=observation(task)
        self.assertEqual(before[1]['content'].split('Legal actions:')[1],task['messages'][1]['content'].split('Legal actions:')[1])
        task['scoring']={'result':'SECRET','fill_prices':'SECRET'}
        self.assertEqual(observation(task),before)
        self.assertNotIn('external executor',before[0]['content'])
        self.assertEqual(QUANTITY,.25)

if __name__=='__main__':unittest.main()
