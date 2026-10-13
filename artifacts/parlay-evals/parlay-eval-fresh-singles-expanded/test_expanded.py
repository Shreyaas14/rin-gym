import json
import unittest
from expanded import OUT, OLD, digest


class ExpansionTests(unittest.TestCase):
    def setUp(self):
        self.p=json.loads((OUT/'protocol.json').read_text())
        self.points=json.loads((OUT/'observations.json').read_text())
        self.pairs=json.loads((OUT/'pairs.json').read_text())

    def test_cohort_frozen_and_tied(self):
        self.assertEqual(digest(self.points),self.p['points_sha256'])
        self.assertEqual(digest(self.pairs),self.p['pairs_sha256'])
        self.assertEqual(len({p['id'] for p in self.points}),132)
        for p in self.points:self.assertEqual(p['visible_score'][0],p['visible_score'][1])

    def test_pairs_distinct_same_phase_and_original_cases_flagged(self):
        seen=set()
        original={(tuple(sorted(c['match_ids'])),c['phase']) for c in json.loads((OLD/'cases.json').read_text())}
        for pair in self.pairs:
            a,b=[self.points[i] for i in pair['indices']]
            self.assertNotEqual(a['match_id'],b['match_id'])
            self.assertEqual(a['phase'],b['phase'])
            key=(tuple(sorted(pair['match_ids'])),pair['phase'])
            self.assertNotIn(key,seen);seen.add(key)
            self.assertEqual(pair['in_original_24'],key in original)
        self.assertEqual(sum(not p['in_original_24'] for p in self.pairs),2146)

    def test_prompt_unchanged_and_no_action_or_hidden_label_fields(self):
        for p in self.points:
            self.assertEqual(digest(p['messages']),p['observation_sha256'])
            self.assertEqual([m['role'] for m in p['messages']],['system','user'])
            self.assertNotIn('Legal actions:',p['messages'][1]['content'])
            self.assertNotIn('Position:',p['messages'][1]['content'])
            self.assertNotIn('evaluation',p['messages'])

    def test_budget_and_request_count(self):
        self.assertEqual(self.p['maximum_requests'],len(self.points)*3)
        self.assertLess(self.p['max_token_cost_estimate_usd']*2,2.5)


if __name__=='__main__':unittest.main()
