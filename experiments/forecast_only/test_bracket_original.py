import json
from experiments.forecast_only.test_closed_loop import ClosedLoopTests
from experiments.forecast_only.bracket_original import execute_bracket, observation
from app.trade_env.costs import buy_cost

class BracketTests(ClosedLoopTests):
    def completion(self,action):
        return json.dumps({'probs':{'home':.5,'draw':.3,'away':.2},'next_goal':{'home':0,'away':0,'none':1},'future_prices':{'home':.5,'draw':.3,'away':.2},'action':action,'reason':'test'})
    def test_entry_and_original_prompt_unchanged(self):
        task=self.replay.next_task()
        self.assertEqual(observation(task),task['messages'])
        expected=buy_cost(task['scoring']['fill_prices']['home'],self.config,'home')
        r,basis=execute_bracket(self.replay,self.completion('buy_home'),self.config,None)
        self.assertEqual(r['executed_action'],'buy_home');self.assertEqual(basis,expected)
    def test_take_profit_and_position_feedback(self):
        self.replay.step('buy_home')
        task=self.replay.next_task();task['scoring']['shown_prices']['home']=.8
        r,basis=execute_bracket(self.replay,self.completion('keep'),self.config,.6)
        self.assertEqual(r['execution_rejection'],'take_profit');self.assertIsNone(basis)
        self.assertEqual(self.replay.next_task()['position'],'flat')
    def test_stop_uses_displayed_quote_not_future_fill(self):
        self.replay.step('buy_home');task=self.replay.next_task()
        task['scoring']['shown_prices']['home']=.6;task['scoring']['fill_prices']['home']=.01
        r,basis=execute_bracket(self.replay,self.completion('keep'),self.config,.6)
        self.assertIsNone(r['execution_rejection']);self.assertEqual(basis,.6)
    def test_stop_overrides_switch_and_reentry_resets_basis(self):
        self.replay.step('buy_home');task=self.replay.next_task();task['scoring']['shown_prices']['home']=.2
        r,basis=execute_bracket(self.replay,self.completion('switch_away'),self.config,.6)
        self.assertEqual(r['executed_action'],'close');self.assertEqual(r['execution_rejection'],'stop_loss')
        task=self.replay.next_task();expected=buy_cost(task['scoring']['fill_prices']['away'],self.config,'away')
        r,basis=execute_bracket(self.replay,self.completion('buy_away'),self.config,basis)
        self.assertEqual(r['executed_action'],'buy_away');self.assertEqual(basis,expected)
    def test_voluntary_switch_preserved(self):
        self.replay.step('buy_home');task=self.replay.next_task();task['scoring']['shown_prices']['home']=.6
        r,basis=execute_bracket(self.replay,self.completion('switch_away'),self.config,.6)
        self.assertEqual(r['executed_action'],'switch_away');self.assertIsNone(r['execution_rejection'])
