"""Offline leakage and one-shot prediction tests; no paid requests."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.forecast import governance as gov
from app.forecast.tinker_bridge import load_datasets, messages, predict, read_manifest
from app.forecast.tests.test_governance import cohort, iso, observation


class TinkerBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        clock = patch.object(gov, 'utc_now', return_value=iso(0))
        self.clock = clock.start()
        self.addCleanup(clock.stop)
        self.ledger = gov.Ledger(self.root)
        self.ledger.initialize()
        self.manifest = self.root / 'tinker.json'
        self.manifest.write_text(json.dumps({'checkpoint': 'tinker://run/sampler_weights/final',
            'model_name': 'example/model', 'renderer': 'qwen3', 'max_tokens': 128, 'temperature': 0}))
        for name in ['baseline-v1', 'candidate-v1']:
            self.ledger.register(name, [self.manifest])
        self.train_path, self.val_path = self.root / 'train.json', self.root / 'validation.json'
        self.train = [self.row('train', -500, -400, -300, True)]
        self.validation = [self.row('val', -200, -150, -100)]

    def row(self, mid, kickoff, cutoff, available, training=False):
        row = {'match_id': mid, 'case_id': mid + '-case', 'kickoff_utc': iso(kickoff),
               'label_available_utc': iso(available), 'observation': observation(cutoff), 'target': 0}
        if training:
            row['teacher_probabilities'] = [.6, .2, .2]
        return row

    def load(self, workspace=None):
        self.train_path.write_text(json.dumps(self.train))
        self.val_path.write_text(json.dumps(self.validation))
        return load_datasets(self.train_path, self.val_path, workspace or self.root)

    def test_requires_initialized_ledger_and_audits_both_splits(self):
        with self.assertRaisesRegex(ValueError, 'not initialized'):
            self.load(self.root / 'absent')
        train, validation = self.load()
        self.assertEqual(train, self.train)
        self.assertEqual(validation, self.validation)
        self.assertEqual(self.ledger.events()[-1]['payload']['match_ids'], ['train', 'val'])
        size = len(self.ledger.events())
        self.load()
        self.assertEqual(size, len(self.ledger.events()))

    def test_overlap_and_chronology_are_rejected(self):
        self.validation[0]['match_id'] = 'train'
        with self.assertRaisesRegex(ValueError, 'disjoint'):
            self.load()
        self.validation[0]['match_id'] = 'val'
        self.train[0]['label_available_utc'] = iso(-180)
        with self.assertRaisesRegex(ValueError, 'precede'):
            self.load()

    def test_missing_ids_labels_and_future_features_rejected(self):
        for mutate in [lambda r: r.pop('match_id'), lambda r: r.update(target=True),
                       lambda r: r['observation']['observation'].update(target=0),
                       lambda r: r['observation'].update(received_utc=iso(10))]:
            original = deepcopy(self.validation)
            mutate(self.validation[0])
            with self.assertRaises(ValueError):
                self.load()
            self.validation = original

    def test_protected_ids_block_both_training_and_validation(self):
        self.ledger.enroll(cohort())
        for split in [self.train, self.validation]:
            original = split[0]['match_id']
            split[0]['match_id'] = 'new-0'
            with self.assertRaisesRegex(ValueError, 'Protected holdout'):
                self.load()
            split[0]['match_id'] = original

    def test_inspected_dataset_games_cannot_be_enrolled_later(self):
        self.train[0]['match_id'] = 'new-0'
        self.load()
        with self.assertRaisesRegex(ValueError, 'previously inspected'):
            self.ledger.enroll(cohort())

    def test_prompt_contains_only_allowed_observation(self):
        prompt = messages(self.train[0]['observation'])
        self.assertEqual(json.loads(prompt[1]['content']), self.train[0]['observation']['observation'])
        self.assertNotIn('teacher_probabilities', str(prompt))
        self.assertNotIn('target', str(prompt))

    def test_manifest_requires_saved_checkpoint_and_deterministic_sampling(self):
        config = read_manifest(self.manifest)
        for key, value in [('checkpoint', 'base-model'), ('temperature', 1), ('max_tokens', 0)]:
            self.manifest.write_text(json.dumps({**config, key: value}))
            with self.assertRaises(ValueError):
                read_manifest(self.manifest)

    def start(self):
        self.ledger.enroll(cohort())
        self.clock.return_value = iso(151)

    def test_claim_survives_interrupt_and_cannot_be_retried_or_bypassed(self):
        self.start()
        claim = self.ledger.begin_tinker_prediction('candidate-v1', '0-0', observation(), self.manifest)
        with self.assertRaisesRegex(ValueError, 'retries'):
            self.ledger.begin_tinker_prediction('candidate-v1', '0-0', observation(), self.manifest)
        with self.assertRaisesRegex(ValueError, 'original Tinker attempt'):
            self.ledger.record('candidate-v1', '0-0', observation(), [.8, .1, .1])
        result = self.ledger.record('candidate-v1', '0-0', observation(), [.8, .1, .1], attempt_sha256=claim['sha256'])
        self.assertEqual(result['payload']['attempt_sha256'], claim['sha256'])

    def test_invalid_observation_or_changed_manifest_blocks_before_claim(self):
        self.start()
        bad = observation()
        bad['observation']['target'] = 0
        with self.assertRaises(ValueError):
            self.ledger.begin_tinker_prediction('candidate-v1', '0-0', bad, self.manifest)
        self.manifest.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'frozen'):
            self.ledger.begin_tinker_prediction('candidate-v1', '0-0', observation(), self.manifest)
        self.assertFalse(any(e['kind'] == 'prediction_started' for e in self.ledger.events()))

    def test_prediction_error_is_redacted_and_consumes_slot(self):
        self.start()
        with patch('app.forecast.tinker_bridge.make_renderer', side_effect=RuntimeError('secret-header')):
            result = asyncio.run(predict(self.root, 'candidate-v1', '0-0', observation(), self.manifest))
        self.assertEqual(result['payload']['status'], 'failed')
        self.assertNotIn('secret-header', json.dumps(self.ledger.events()))
        with self.assertRaisesRegex(ValueError, 'retries'):
            asyncio.run(predict(self.root, 'candidate-v1', '0-0', observation(), self.manifest))
