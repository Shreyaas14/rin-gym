"""Scripted model and fake service for offline tests and dry runs."""

import json
import random
import re
from types import SimpleNamespace

import tinker
from tinker_cookbook import completers


class ScriptedCompleter(completers.TokenCompleter):
    """Dry-run policy: a made-up answer per call, mostly legal, sometimes broken."""

    def __init__(self, tokenizer, seed=0):
        self.tokenizer, self.rng = tokenizer, random.Random(seed)

    def answer(self, prompt):
        """(text, finished) for a decoded prompt."""
        legal = re.findall(r"Legal actions: ([a-z_, ]+)", prompt)[-1].split(", ")
        roll = self.rng.random()
        if roll < 0.1:
            return "I would rather wait and see.", True
        action = "buy_moon" if roll < 0.2 else self.rng.choice(legal)
        probs = {"home": 0.5, "draw": 0.3, "away": 0.2}
        answer = {"probs": probs, "action": action, "reason": "scripted"}
        if '"future_prices"' in prompt:
            answer.update(
                next_goal={"home": 0.2, "away": 0.2, "none": 0.6}, future_prices=probs
            )
        return json.dumps(answer), roll < 0.9

    async def __call__(self, model_input, stop, *, max_tokens=None):
        text, finished = self.answer(self.tokenizer.decode(model_input.to_ints()))
        tokens = self.tokenizer.encode(text, add_special_tokens=False)
        if max_tokens is not None and len(tokens) >= max_tokens:
            tokens, finished = tokens[:max_tokens], False
        if finished:
            tokens = tokens + [stop[0]]  # the stop token is part of the sampled action
        logprobs = [-self.rng.uniform(0.05, 1.0) for _ in tokens]
        reason = "stop" if finished else "length"
        return completers.TokensWithLogprobs(tokens, logprobs, reason)


def done(**fields):
    """An already finished Tinker API future."""

    async def result_async():
        return SimpleNamespace(**fields)

    return SimpleNamespace(result_async=result_async)


class FakeTinker:
    """Dry-run stand-in for the Tinker service, training and sampling clients: just what
    the cookbook's sync loop calls. It keeps what it is sent and learns nothing."""

    last = None

    def __init__(self, policy):
        self.policy, self.batches, self.optim_steps = policy, [], []
        FakeTinker.last = self

    async def create_lora_training_client_async(self, base_model, rank=32, **more):
        return self

    async def save_weights_and_get_sampling_client_async(self):
        return self

    def create_sampling_client(self, model_path=None, **more):
        return self

    async def sample_async(self, prompt, num_samples, sampling_params):
        limit = sampling_params.max_tokens
        out = await self.policy(prompt, sampling_params.stop, max_tokens=limit)
        sequence = SimpleNamespace(
            tokens=out.tokens, logprobs=out.logprobs, stop_reason=out.stop_reason
        )
        return SimpleNamespace(sequences=[sequence])

    async def forward_backward_async(self, data, loss_fn, loss_fn_config=None):
        self.batches.append({"loss_fn": loss_fn, "data": data})
        sampled = [datum.loss_fn_inputs["logprobs"].to_numpy() for datum in data]
        learner = [tinker.TensorData.from_numpy(q - 0.01) for q in sampled]
        outputs = [{"logprobs": logprobs} for logprobs in learner]
        return done(loss_fn_outputs=outputs, metrics={})

    async def optim_step_async(self, adam_params):
        self.optim_steps.append(adam_params)
        return done(metrics={})

    async def save_state_async(self, name, ttl_seconds=None):
        return done(path=f"tinker://dry-run/weights/{name}")

    async def save_weights_for_sampler_async(self, name, ttl_seconds=None):
        return done(path=f"tinker://dry-run/sampler_weights/{name}")


def run_dry(config, tokenizer, train_then_evaluate):
    """Exercise the real cookbook loop against a disposable, offline service."""
    import asyncio
    from pathlib import Path
    import shutil
    from unittest import mock

    path = Path(config.log_path)
    if not path.name.startswith("dry-") or path.is_symlink():
        raise SystemExit(f"Refusing unsafe dry-run directory: {path}")
    shutil.rmtree(path, ignore_errors=True)
    fake = FakeTinker(ScriptedCompleter(tokenizer))
    with mock.patch.object(tinker, "ServiceClient", return_value=fake):
        asyncio.run(train_then_evaluate(config))
    print("Dry run done: fake service, nothing billed. Logs in", path)
