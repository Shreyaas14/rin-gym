import concurrent.futures
import json
from pathlib import Path
import tempfile
import unittest
from budget import Budget, BudgetStop
from features import features, identity, period
from score import identity_metric, settlement_metric, read_labels

ROOT=Path(__file__).resolve().parent
OLD=ROOT.parent/'market-entity-resolution-real'

class BudgetTests(unittest.TestCase):
    def test_concurrent_calls_never_reserve_above_cap(self):
        with tempfile.TemporaryDirectory() as td:
            b=Budget(Path(td)/'budget.db')
            def reserve(_):
                try: return b.reserve('llm','test','gpt-4.1-mini-2025-04-14','hash')
                except BudgetStop: return None
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                accepted=list(pool.map(reserve,range(20)))
            self.assertGreater(sum(x is not None for x in accepted),0)
            self.assertLessEqual(b.summary()['accounted_upper_usd'],5)
            self.assertLess(b.summary()['remaining_usd'],1)
            # Restart retains reservations; uncertainty does not refund funds.
            self.assertEqual(b.summary(),Budget(Path(td)/'budget.db').summary())

    def test_usage_refunds_only_known_cost_and_rejects_invalid_usage(self):
        with tempfile.TemporaryDirectory() as td:
            b=Budget(Path(td)/'budget.db')
            ident=b.reserve('jev','test','jev-1.13.0','hash')
            with self.assertRaises(BudgetStop): b.settle(ident,None,2)
            self.assertGreater(b.summary()['uncertain_or_inflight_reserved_usd'],0)
            b.settle(ident,1000,100)
            self.assertAlmostEqual(b.summary()['reported_usage_cost_usd'],0.000042)
            self.assertEqual(b.summary()['uncertain_or_inflight_reserved_usd'],0)

    def test_overbound_halts_all_future_calls(self):
        with tempfile.TemporaryDirectory() as td:
            b=Budget(Path(td)/'budget.db'); ident=b.reserve('jev','test','jev-1.13.0','hash')
            with self.assertRaises(BudgetStop): b.settle(ident,65537,0)
            with self.assertRaises(BudgetStop): b.reserve('jev','next','jev-1.13.0','hash')

class HeuristicTests(unittest.TestCase):
    def test_abbreviated_month_end_and_word_date(self):
        self.assertEqual(period('Oct 31, 2026'), '2026-10')
        self.assertEqual(period('October 2026'), '2026-10')
        m={'venue':'polymarket','title':'Will Bitcoin be above $90,000?',
           'rules':'The Binance candle closing at 12:00 PM ET.',
           'raw':{'slug':'bitcoin-above-90000-on-october-14-2026'}}
        self.assertEqual(features(m)['instant_utc'],'2026-10-14T16:00:00+00:00')

    def test_prior_explicit_near_misses_and_matches(self):
        pairs=json.loads((OLD/'reference.json').read_text())
        # Old pilot is development/regression data only.
        for p in pairs:
            if p['family'] in ('sports_nfl','sports_nba','sports_nhl','sports_near_miss','bitcoin_monthly'):
                self.assertEqual(identity(p['a'],p['b'])[0],p['identity_gold'],p['id'])

class ReferenceTests(unittest.TestCase):
    def test_unknown_settlement_is_not_scored_as_false_or_true(self):
        labels={'fixture':{'identity_label':'match','settlement_label':'unknown'}}
        predictions={'fixture':{'identity':'match','settlement':'compatible'}}
        result=settlement_metric(labels,predictions)
        self.assertIsNone(result['compatible_precision_on_known'])
        self.assertIsNone(result['compatible_recall'])
        self.assertEqual(result['known_false_compatible_accepts'],0)
        self.assertEqual(result['compatible_accepts_on_unknown'],1)

    def test_missing_predictions_remain_in_coverage_denominator(self):
        labels={k:{'identity_label':'match','settlement_label':'unknown'} for k in ('one','two')}
        result=identity_metric(labels,{'one':{'identity':'match'}})
        self.assertEqual(result['decision_coverage'],0.5)
        self.assertEqual(result['missing_reviewed_predictions'],1)

    def test_blank_review_cannot_be_used_as_ground_truth(self):
        pairs=json.loads((ROOT/'pairs.json').read_text())
        with self.assertRaisesRegex(ValueError,'No reviewed labels'):
            read_labels(ROOT/'review-blind.csv',{p['id'] for p in pairs})

if __name__=='__main__': unittest.main()
