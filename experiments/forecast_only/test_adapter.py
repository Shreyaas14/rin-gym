import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import tinker
from tinker_cookbook import renderers
from tinker_cookbook.rl import train
from tinker_cookbook.tokenizer_utils import get_tokenizer
from app.trade_env.tasks import read_tasks
from experiments.forecast_only.pilot import Builder, ForecastEnv
from tests.trade_env.fake_tinker import FakeTinker, ScriptedCompleter

class ForecastCompleter(ScriptedCompleter):
    def answer(self,prompt):
        p=self.rng.uniform(.1,.8)
        return json.dumps({'probs':{'home':p,'draw':(1-p)/2,'away':(1-p)/2}}),True

class AdapterTests(unittest.TestCase):
    def test_real_cookbook_loop_with_fake_service(self):
        tokenizer=get_tokenizer('Qwen/Qwen3-8B');fake=FakeTinker(ForecastCompleter(tokenizer))
        with tempfile.TemporaryDirectory() as tmp:
            cfg=train.Config(recipe_name='forecast_only_test',model_name='Qwen/Qwen3-8B',renderer_name='qwen3_disable_thinking',
                learning_rate=1e-5,max_tokens=128,max_steps=1,eval_every=0,save_every=0,log_path=tmp,
                remove_constant_reward_groups=False,dataset_builder=Builder(path='artifacts/forecast-only',model='Qwen/Qwen3-8B',
                    renderer_name='qwen3_disable_thinking',group_size=2,groups_per_batch=1,seed=20261013))
            # CoreTests exercises the real ledger guard separately. This fake-service
            # adapter test must not depend on the run author's machine-local ledger.
            with mock.patch.object(tinker, 'ServiceClient', return_value=fake), mock.patch(
                'experiments.forecast_only.pilot.guard', return_value={}
            ):
                asyncio.run(train.main(cfg))
            self.assertEqual(len(fake.optim_steps),1)
            self.assertGreater(len(fake.batches[0]['data']),0)
            self.assertTrue((Path(tmp)/'checkpoints.jsonl').exists())

if __name__=='__main__':unittest.main()
