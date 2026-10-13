import json,tempfile,unittest
from pathlib import Path
from experiments.eight_directions.budget import Spending

class HardBudget(unittest.TestCase):
    def ledger(self,folder):
        p=Path(folder)/'spending.json'
        p.write_text(json.dumps({'hard_limit_usd':5,'reserved_max_usd':0,'token_cost_estimate_usd':0,'paid_requests':0,'entries':[]}))
        return Spending(p)
    def test_reservations_survive_reload_and_pending_remains_encumbered(self):
        with tempfile.TemporaryDirectory() as folder:
            b=self.ledger(folder);b.reserve('first',2,{'max_tokens':256})
            resumed=Spending(b.path)
            self.assertEqual(resumed.read()['reserved_max_usd'],2)
            with self.assertRaises(ValueError):resumed.reserve('second',2.51,{})
            self.assertEqual(resumed.read()['entries'][0]['status'],'reserved')
    def test_completed_request_does_not_release_maximum_and_cannot_repeat(self):
        with tempfile.TemporaryDirectory() as folder:
            b=self.ledger(folder);b.reserve('first',.2,{})
            b.complete('first',.1,{'input_tokens':100})
            self.assertEqual(b.read()['reserved_max_usd'],.2)
            self.assertEqual(b.read()['token_cost_estimate_usd'],.1)
            with self.assertRaises(ValueError):b.reserve('first',.2,{})
            with self.assertRaises(ValueError):b.complete('first',.1,{})
    def test_over_reservation_completion_fails_without_losing_pending(self):
        with tempfile.TemporaryDirectory() as folder:
            b=self.ledger(folder);b.reserve('first',.2,{})
            with self.assertRaises(ValueError):b.complete('first',.21,{})
            self.assertEqual(b.read()['entries'][0]['status'],'reserved')

if __name__=='__main__':unittest.main()
