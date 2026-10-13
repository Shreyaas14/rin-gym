"""Time-controlled tests of holdout governance; no real games or network calls."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app.forecast.governance import Ledger, TARGET, assert_development_matches, timestamp
from app.forecast.evaluate import select_temperature, run
from app.forecast.model import Simulator


def iso(second):
    return datetime.fromtimestamp(1900000000 + second, timezone.utc).isoformat()


def cohort():
    return {'name': 'future-soccer', 'target_definition': TARGET, 'baseline': 'baseline-v1',
            'candidate': 'candidate-v1', 'minimum_games': 2, 'noninferiority_margin': .01,
            'matches': [{'match_id': 'new-' + str(i), 'kickoff_utc': iso(100),
                         'resolution_not_before_utc': iso(500),
                         'cases': [{'case_id': f'{i}-{j}', 'observation_cutoff_utc': iso(150 + j * 50),
                                    'deadline_utc': iso(170 + j * 50)} for j in range(i + 1)]}
                        for i in range(2)]}


def observation(second=150):
    return {'source_sha256': 'a' * 64, 'received_utc': iso(second),
            'observation': {'cutoff': timestamp(iso(second)), 'home_goals': 0, 'away_goals': 0,
                            'phase': 'first_half', 'elapsed_period_minutes': 1,
                            'observed_events': 1, 'latest_event_timestamp': timestamp(iso(second - 1))}}


class GovernanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        clock = patch('app.forecast.governance.utc_now', return_value=iso(0))
        self.clock = clock.start()
        self.addCleanup(clock.stop)
        env = patch.dict('os.environ', {'RIN_GYM_WORKSPACE': str(self.root)})
        env.start()
        self.addCleanup(env.stop)
        self.ledger = Ledger(self.root)
        self.ledger.initialize()
        self.artifact = self.root / 'model.json'
        self.artifact.write_text('{"frozen":true}')
        for name in ['baseline-v1', 'candidate-v1']:
            self.ledger.register(name, [self.artifact])

    def enroll(self):
        self.ledger.enroll(cohort())

    def predict_all(self):
        for second, cases in [(150, ['0-0', '1-0']), (200, ['1-1'])]:
            self.clock.return_value = iso(second + 1)
            for cid in cases:
                self.ledger.record('baseline-v1', cid, observation(second), [.3, .3, .4])
                self.ledger.record('candidate-v1', cid, observation(second), [.8, .1, .1])

    def resolve_all(self):
        self.clock.return_value = iso(501)
        for mid in ['new-0', 'new-1']:
            self.ledger.resolve(mid, 0, iso(450), 'b' * 64)

    def test_init_is_idempotent_and_pending(self):
        status = self.ledger.initialize()
        self.assertEqual(status['state'], 'pending_enrollment')
        self.assertEqual(len(self.ledger.events()), 3)

    def test_enrollment_is_frozen_and_registration_closes(self):
        self.enroll()
        with self.assertRaisesRegex(ValueError, 'already frozen'):
            self.enroll()
        with self.assertRaisesRegex(ValueError, 'locked'):
            self.ledger.register('new', [self.artifact])

    def test_past_and_previously_inspected_matches_are_rejected(self):
        self.clock.return_value = iso(101)
        with self.assertRaisesRegex(ValueError, 'future matches'):
            self.enroll()
        self.clock.return_value = iso(0)
        path = self.root / 'data/manifests/forecast/manifest.json'
        path.parent.mkdir(parents=True)
        path.write_text('[{"match_id":"new-0"}]')
        with self.assertRaisesRegex(ValueError, 'previously inspected'):
            self.enroll()

    def test_duplicate_cases_and_wrong_target_are_rejected(self):
        c = cohort()
        c['matches'][1]['cases'][0]['case_id'] = '0-0'
        with self.assertRaisesRegex(ValueError, 'Duplicate case'):
            self.ledger.enroll(c)
        c = cohort()
        c['target_definition'] = 'trophy_winner'
        with self.assertRaisesRegex(ValueError, 'regulation'):
            self.ledger.enroll(c)

    def test_naive_and_impossible_timestamps_rejected(self):
        c = cohort()
        c['matches'][0]['kickoff_utc'] = '2030-01-01T10:00:00'
        with self.assertRaisesRegex(ValueError, 'timezone'):
            self.ledger.enroll(c)
        c = cohort()
        c['matches'][0]['cases'][0]['deadline_utc'] = iso(150)
        with self.assertRaisesRegex(ValueError, 'cutoff < deadline'):
            self.ledger.enroll(c)

    def test_all_development_entry_points_reject_reserved_ids(self):
        self.enroll()
        with self.assertRaisesRegex(ValueError, 'Protected holdout'):
            assert_development_matches(['new-0'], self.root)
        with self.assertRaisesRegex(ValueError, 'Protected holdout'):
            Simulator.fit([{'match_id': 'new-0', 'split': 'train'}], {})
        with self.assertRaisesRegex(ValueError, 'Protected holdout'):
            select_temperature([[.4, .3, .3]], [0], ['new-0'], ['validation'], {})
        with patch('app.forecast.evaluate.read', side_effect=[{}, [{'match_id': 'new-0'}]]):
            with self.assertRaisesRegex(ValueError, 'Protected holdout'):
                run()
        from app.forecast.data import prepare
        with patch('app.forecast.data.read', return_value={}), patch('app.forecast.data.group_matches', return_value=[{'IdMatch': 'new-0'}]):
            with self.assertRaisesRegex(ValueError, 'Protected holdout'):
                prepare()

    def test_issue_time_is_server_generated_and_overwrite_fails(self):
        self.enroll()
        self.clock.return_value = iso(151)
        receipt = self.ledger.record('candidate-v1', '0-0', observation(), [.4, .3, .3])
        self.assertEqual(receipt['recorded_utc'], iso(151))
        self.assertEqual(receipt['payload']['status'], 'ok')
        with self.assertRaisesRegex(ValueError, 'overwrites'):
            self.ledger.record('candidate-v1', '0-0', observation(), [.9, .05, .05])

    def test_future_window_is_closed_and_late_attempt_consumes_slot(self):
        self.enroll()
        with self.assertRaisesRegex(ValueError, 'not open'):
            self.ledger.record('candidate-v1', '0-0', observation(), [.4, .3, .3])
        self.clock.return_value = iso(171)
        result = self.ledger.record('candidate-v1', '0-0', observation(), [.4, .3, .3])
        self.assertEqual(result['payload']['status'], 'failed')
        self.assertIn('deadline', result['payload']['error'])

    def test_artifact_validation_cannot_backdate_a_late_receipt(self):
        self.enroll()
        self.clock.side_effect = [iso(151), iso(171), iso(172)]
        result = self.ledger.record('candidate-v1', '0-0', observation(), [.4, .3, .3])
        self.assertEqual(result['recorded_utc'], iso(172))
        self.assertEqual(result['payload']['status'], 'failed')

    def test_future_fields_and_retrospective_payloads_fail_closed(self):
        self.enroll()
        self.clock.return_value = iso(151)
        payload = observation()
        payload['observation']['target'] = 0
        a = self.ledger.record('baseline-v1', '0-0', payload, [.4, .3, .3])
        b = self.ledger.record('candidate-v1', '0-0', {'forecast_ready': False}, [.4, .3, .3])
        self.assertEqual(a['payload']['status'], 'failed')
        self.assertEqual(b['payload']['status'], 'failed')

    def test_future_source_receipt_is_rejected(self):
        self.enroll()
        self.clock.return_value = iso(151)
        payload = observation()
        payload['received_utc'] = iso(151)
        result = self.ledger.record('candidate-v1', '0-0', payload, [.4, .3, .3])
        self.assertEqual(result['payload']['status'], 'failed')

    def test_bad_probabilities_and_tool_errors_are_recorded(self):
        self.enroll()
        self.clock.return_value = iso(151)
        a = self.ledger.record('candidate-v1', '0-0', observation(), [float('nan'), .5, .5])
        b = self.ledger.record('baseline-v1', '0-0', observation(), error='tool timeout')
        self.assertEqual(a['payload']['status'], 'failed')
        self.assertEqual(b['payload']['error'], 'tool timeout')

    def test_changed_model_or_code_is_a_failed_attempt(self):
        self.enroll()
        self.clock.return_value = iso(151)
        self.artifact.write_text('{"frozen":false}')
        a = self.ledger.record('candidate-v1', '0-0', observation(), [.4, .3, .3])
        self.assertIn('artifact changed', a['payload']['error'])
        with patch('app.forecast.governance.code_fingerprint', return_value={}):
            b = self.ledger.record('baseline-v1', '0-0', observation(), [.4, .3, .3])
        self.assertIn('code changed', b['payload']['error'])

    def test_scoring_is_blocked_until_all_outcomes_resolve(self):
        self.enroll()
        self.predict_all()
        with self.assertRaisesRegex(ValueError, 'All enrolled'):
            self.ledger.score()
        self.clock.return_value = iso(501)
        self.ledger.resolve('new-0', 0, iso(450), 'b' * 64)
        with self.assertRaisesRegex(ValueError, 'All enrolled'):
            self.ledger.score()

    def test_early_resolution_and_post_outcome_forecasts_rejected(self):
        self.enroll()
        with self.assertRaises(ValueError):
            self.ledger.resolve('new-0', 0, iso(450), 'b' * 64)
        self.predict_all()
        self.clock.return_value = iso(501)
        with self.assertRaisesRegex(ValueError, 'after the declared outcome'):
            self.ledger.resolve('new-0', 0, iso(149), 'b' * 64)

    def test_missing_and_failed_predictions_block_promotion(self):
        self.enroll()
        self.clock.return_value = iso(151)
        self.ledger.record('baseline-v1', '0-0', observation(), [.3, .3, .4])
        self.ledger.record('candidate-v1', '0-0', observation(), error='timeout')
        self.resolve_all()
        report = self.ledger.score()
        self.assertEqual(report['coverage']['candidate-v1']['failed'], 1)
        self.assertEqual(report['coverage']['candidate-v1']['missing'], 2)
        self.assertFalse(report['gates']['complete_coverage'])
        self.assertFalse(report['gates']['improved'])
        self.assertIsNone(report['paired_brier_delta'])

    def test_complete_report_is_match_weighted_and_one_time(self):
        self.enroll()
        self.predict_all()
        self.resolve_all()
        report = self.ledger.score()
        self.assertTrue(report['gates']['complete_coverage'])
        self.assertTrue(report['gates']['improved'])
        self.assertAlmostEqual(report['metrics_common_cases_only']['candidate-v1']['brier'], .06)
        self.assertEqual(report['metrics_common_cases_only']['candidate-v1']['games'], 2)
        before = len(self.ledger.events())
        self.assertEqual(self.ledger.score(), report)
        self.assertEqual(len(self.ledger.events()), before)
        with self.assertRaisesRegex(ValueError, 'Protected holdout'):
            assert_development_matches(['new-0'], self.root)
        with self.assertRaises(ValueError):
            self.ledger.record('candidate-v1', '0-0', observation(), [.8, .1, .1])

    def test_scorer_change_blocks_first_readout(self):
        self.enroll()
        self.resolve_all()
        with patch('app.forecast.governance.code_fingerprint', return_value={}):
            with self.assertRaisesRegex(ValueError, 'Scoring code changed'):
                self.ledger.score()

    def test_sql_update_delete_blocked_and_hash_tampering_detected(self):
        with sqlite3.connect(self.ledger.path) as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE events SET body='{}' WHERE seq=1")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('DELETE FROM events WHERE seq=1')
            # Demonstrate detection when an owner bypasses the trigger but not the hash.
            db.execute('DROP TRIGGER no_update')
            db.execute("UPDATE events SET sha256='bad' WHERE seq=1")
        with self.assertRaisesRegex(ValueError, 'integrity'):
            self.ledger.events()

    def test_backwards_clock_rejected(self):
        self.clock.return_value = iso(-1)
        with self.assertRaisesRegex(ValueError, 'clock moved backwards'):
            self.ledger.register('new-name', [self.artifact])

    def test_concurrent_submissions_cannot_overwrite(self):
        self.enroll()
        self.clock.return_value = iso(151)
        def attempt(_):
            try:
                self.ledger.record('candidate-v1', '0-0', observation(), [.4, .3, .3])
                return 'ok'
            except ValueError:
                return 'duplicate'
        with ThreadPoolExecutor(2) as pool:
            self.assertCountEqual(list(pool.map(attempt, range(2))), ['ok', 'duplicate'])

    def test_partial_common_case_scores_never_pass_coverage_gate(self):
        self.enroll()
        self.clock.return_value = iso(151)
        for name in ['baseline-v1', 'candidate-v1']:
            self.ledger.record(name, '0-0', observation(), [.8, .1, .1])
        self.resolve_all()
        report = self.ledger.score()
        self.assertEqual(report['metrics_common_cases_only']['candidate-v1']['games'], 1)
        self.assertFalse(report['gates']['noninferior'])
        self.assertFalse(report['gates']['complete_coverage'])
