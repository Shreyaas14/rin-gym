"""Real SDK/cookbook interfaces with deterministic fake transport and renderer."""
import asyncio
import importlib.util
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.forecast.tests import test_tinker_bridge as fixtures
from app.forecast.tinker_bridge import make_dataset_builder, make_evaluator, predict, sample_probabilities


@unittest.skipUnless(importlib.util.find_spec('tinker_cookbook'), 'Install the optional tinker extra')
class TinkerIntegrationTests(unittest.TestCase):
    setUp = fixtures.TinkerBridgeTests.setUp
    row = fixtures.TinkerBridgeTests.row
    load = fixtures.TinkerBridgeTests.load
    start = fixtures.TinkerBridgeTests.start

    def renderer(self, text='[0.6, 0.2, 0.2]', clean=True):
        import tinker
        import torch
        from tinker_cookbook.renderers.base import ParseTermination
        renderer = MagicMock()
        renderer.build_generation_prompt.return_value = tinker.ModelInput.from_ints([1, 2])
        renderer.get_stop_sequences.return_value = [3]
        renderer.parse_response.return_value = ({'role': 'assistant', 'content': text},
            ParseTermination.STOP_SEQUENCE if clean else ParseTermination.MALFORMED)
        renderer.build_supervised_example.return_value = (tinker.ModelInput.from_ints([1, 2, 3, 4]), torch.tensor([0., 0., 1., 1.]))
        return renderer

    def client(self):
        import tinker
        import numpy as np
        return SimpleNamespace(sample_async=AsyncMock(return_value=tinker.SampleResponse(
            sequences=[tinker.SampledSequence(tokens_np=np.array([1, 2, 3]), stop_reason='stop', sequence_id='fake')])) )

    def test_real_sdk_sampling_contract_and_malformed_responses(self):
        from app.forecast.tests.test_governance import observation
        client = self.client()
        result = asyncio.run(sample_probabilities(client, self.renderer(), observation(), 128))
        self.assertEqual(result, [.6, .2, .2])
        params = client.sample_async.call_args.kwargs['sampling_params']
        self.assertEqual(params.temperature, 0)
        for renderer in [self.renderer(clean=False), self.renderer('not json'), self.renderer('[1,1,1]'), self.renderer('[true,false,false]'), self.renderer('["0.6",0.2,0.2]')]:
            with self.assertRaises(ValueError):
                asyncio.run(sample_probabilities(client, renderer, observation(), 128))

    def test_builder_is_cookbook_type_masks_prompt_and_keeps_partial_batch(self):
        import chz
        from tinker_cookbook.supervised.types import SupervisedDatasetBuilder
        self.load()
        builder = make_dataset_builder(self.train_path, self.val_path, 'model', 'role_colon', self.root, batch_size=8)
        self.assertIsInstance(builder, SupervisedDatasetBuilder)
        self.assertIn('dataset_sha256', chz.asdict(builder))
        from tinker_cookbook.supervised import train
        config = train.Config(log_path=str(self.root), model_name='model', recipe_name='test', dataset_builder=builder)
        self.assertEqual(chz.asdict(config)['dataset_builder']['dataset_sha256'], builder.dataset_sha256)
        with patch('app.forecast.tinker_bridge.make_renderer', return_value=self.renderer()):
            dataset, validation = builder()
            self.assertEqual(len(dataset), 1)
            self.assertIsNone(validation)
            datum = dataset.get_batch(0)[0]
        weights = datum.loss_fn_inputs['weights'].to_torch().tolist()
        self.assertEqual(weights[0], 0)
        self.assertAlmostEqual(sum(weights), 1)
        self.train_path.write_text('[]')
        with self.assertRaises(ValueError):
            builder()

    def test_evaluator_is_native_type_and_returns_game_weighted_metrics(self):
        from tinker_cookbook.eval.evaluators import SamplingClientEvaluator
        self.load()
        factory = make_evaluator(self.train_path, self.val_path, 'model', 'role_colon', self.root)
        evaluator = factory()
        self.assertIsInstance(evaluator, SamplingClientEvaluator)
        with patch('app.forecast.tinker_bridge.make_renderer', return_value=self.renderer()):
            metrics = asyncio.run(evaluator(self.client()))
        self.assertAlmostEqual(metrics['forecast/brier'], .24)
        self.assertEqual(metrics['forecast/games'], 1.)
        self.assertTrue(all(isinstance(value, float) for value in metrics.values()))

    def test_success_uses_frozen_checkpoint_and_logs_receipt_after_sampling(self):
        from app.forecast.tests.test_governance import observation, iso
        self.start()
        client = self.client()
        async def sample(**kwargs):
            kinds = [e['kind'] for e in self.ledger.events()]
            self.assertEqual(kinds[-1], 'prediction_started')
            self.clock.return_value = iso(152)
            return self.client().sample_async.return_value
        client.sample_async.side_effect = sample
        with patch('tinker.ServiceClient') as service, patch('app.forecast.tinker_bridge.make_renderer', return_value=self.renderer()):
            service.return_value.create_sampling_client.return_value = client
            result = asyncio.run(predict(self.root, 'candidate-v1', '0-0', observation(), self.manifest))
            service.return_value.create_sampling_client.assert_called_once_with(model_path='tinker://run/sampler_weights/final')
        self.assertEqual(result['recorded_utc'], iso(152))
        self.assertEqual(result['payload']['status'], 'ok')
        self.assertTrue(result['payload']['attempt_sha256'])

    def test_timeout_or_late_sample_is_a_failed_slot(self):
        from app.forecast.tests.test_governance import observation, iso
        self.start()
        client = self.client()
        async def late(**kwargs):
            self.clock.return_value = iso(171)
            return self.client().sample_async.return_value
        client.sample_async.side_effect = late
        with patch('tinker.ServiceClient') as service, patch('app.forecast.tinker_bridge.make_renderer', return_value=self.renderer()):
            service.return_value.create_sampling_client.return_value = client
            result = asyncio.run(predict(self.root, 'candidate-v1', '0-0', observation(), self.manifest))
        self.assertEqual(result['payload']['status'], 'failed')
        self.assertIn('deadline', result['payload']['error'])
