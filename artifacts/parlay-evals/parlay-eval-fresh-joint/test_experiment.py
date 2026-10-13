import copy
import json
import unittest

from experiment import OUT, KEYS, parse, observation, REPO, read_tasks, digest


class ExperimentTests(unittest.TestCase):
    def test_joint_requires_all_nine_outcomes(self):
        p=parse(json.dumps({'joint':{k:1/9 for k in KEYS}}),'joint')
        self.assertAlmostEqual(sum(p.values()),1)
        bad={'joint':{KEYS[0]:1}}
        self.assertIsNone(parse(json.dumps(bad),'joint'))

    def test_rejects_duplicate_keys_nan_boolean_and_extra_fields(self):
        self.assertIsNone(parse('{"probs":{"home":0.5,"home":0.4,"draw":0.3,"away":0.3}}','single_a'))
        for p in [{'home':True,'draw':0,'away':0},{'home':float('nan'),'draw':.5,'away':.5}]:
            self.assertIsNone(parse(json.dumps({'probs':p}),'single_a'))
        self.assertIsNone(parse('{"probs":{"home":0.4,"draw":0.3,"away":0.3},"action":"buy_home"}','single_a'))

    def test_small_rounding_tolerance_and_invalid_sum(self):
        p=parse('{"probs":{"home":0.33,"draw":0.33,"away":0.33}}','single_a')
        self.assertAlmostEqual(sum(p.values()),1)
        self.assertIsNone(parse('{"probs":{"home":0.4,"draw":0.4,"away":0.4}}','single_a'))

    def test_scoring_metadata_never_changes_prompt(self):
        ts=[t for t in read_tasks(REPO/'artifacts/earlier-run-risk/tasks.jsonl.gz') if t['position']=='flat']
        a,b=copy.deepcopy(ts[0]),copy.deepcopy(ts[1])
        before=observation(a,b,'joint')
        for t in [a,b]:t['scoring']={'result':'SENTINEL','fill_prices':'SENTINEL'}
        self.assertEqual(before,observation(a,b,'joint'))
        self.assertNotIn('Position:',before[1]['content'])
        self.assertNotIn('Legal actions:',before[1]['content'])

    def test_frozen_cohort_disjoint_and_messages_hashed(self):
        cases=json.loads((OUT/'cases.json').read_text());p=json.loads((OUT/'protocol.json').read_text())
        ids=[mid for c in cases for mid in c['match_ids']]
        self.assertEqual(len(ids),len(set(ids)))
        self.assertEqual(digest(cases),p['cases_sha256'])
        self.assertLess(p['max_token_cost_estimate_usd']*2,2.5)
        for c in cases:
            for arm,msgs in c['messages'].items():self.assertEqual(digest(msgs),c['observation_sha256'][arm])


if __name__=='__main__':unittest.main()
