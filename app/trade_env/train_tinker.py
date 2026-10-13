"""Train on trade_env tasks with tinker-cookbook's GRPO-style RL trainer.

Ours: the env adapter, the dataset, a full-match replay evaluator and the launcher.
The cookbook's (tinker_cookbook.rl.train.main): rollouts, advantages centred on the
group mean, the importance-sampling loss, checkpoints, resume, eval scheduling, logs.

    python -m app.trade_env.train_tinker                # print the estimate and stop
    python -m app.trade_env.train_tinker dry_run=true   # rehearse on a fake service
    python -m app.trade_env.train_tinker confirm=true   # train; needs TINKER_API_KEY

Install requirements-train.txt first. A run's directory is named after its settings and
the tasks file's contents, so the same command resumes its run and any change starts
another. The cookbook evaluates before a step, so the launcher adds one evaluation of
the final checkpoint; held-out metrics are logged under the eval split's name.
"""

# No `from __future__ import annotations`: chz cannot resolve a builder's annotations.
import asyncio
from dataclasses import dataclass
import functools
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import shutil
from types import SimpleNamespace
from unittest import mock

import chz
import tinker
from tinker_cookbook import checkpoint_utils, completers, renderers
from tinker_cookbook.eval.evaluators import SamplingClientEvaluator
from tinker_cookbook.exceptions import RendererError
from tinker_cookbook.rl import train, types as rl
from tinker_cookbook.rl.metric_util import RLTestSetEvaluator
from tinker_cookbook.tokenizer_utils import get_tokenizer

from .config import DEFAULTS, make_config
from .replay import by_match, replay_match
from .reward import score
from .stats import cents, mean
from .tasks import read_tasks

ANSWER_TOKENS = 80  # a JSON answer with a one-sentence reason; the cap is max_tokens
# $ per million tokens (prefill, sample, train): Tinker pricing page, read 2026-10-12
PRICES = {
    "Qwen/Qwen3-8B": (0.195, 0.60, 0.44),
    "Qwen/Qwen3.5-4B": (0.33, 1.005, 0.737),
    "Qwen/Qwen3.5-9B": (0.30, 0.807, 1.463),
    "Qwen/Qwen3.6-35B-A3B": (0.45, 1.335, 1.177),
}


def read_answer(renderer, tokens):
    """(completion text, cut off). A cut-off answer never reached the stop token; one
    with a second stop token cannot be parsed and counts as cut off too."""
    try:
        message, termination = renderer.parse_response(tokens)
    except RendererError:
        return renderer.tokenizer.decode(tokens), True
    return renderers.get_text_content(message), not termination.is_clean


class TradeEnv(rl.Env):
    """One task, one completion, one reward."""

    def __init__(self, task, renderer, config, reward_scale):
        self.task, self.renderer = task, renderer
        self.config, self.reward_scale = config, reward_scale

    async def initial_observation(self):
        prompt = self.renderer.build_generation_prompt(self.task["messages"])
        return prompt, self.renderer.get_stop_sequences()

    async def step(self, action, *, extra=None):
        text, truncated = read_answer(self.renderer, action)
        # a cut-off answer is invalid even if a JSON object got out before the limit
        out = score(self.task, None if truncated else text, self.config)
        # the same keys every time: the trainer averages a key over whoever sent it
        metrics = {
            "pnl": out["pnl"],
            "valid": float(out["valid"]),
            "traded": float(out["trades"] > 0),
            "noop": float(out["valid"] and not out["trades"]),  # chose to hold still
            "cost": out["cost"],
            "missed": out["missed"],
            "log_score_edge": out["log_score_edge"] or 0.0,
            "truncated": float(truncated),
        }
        logs = {"task_id": self.task["id"], "action": out["action"], "completion": text}
        return rl.StepResult(
            reward=out["reward"] * self.reward_scale,
            episode_done=True,
            next_observation=tinker.ModelInput.empty(),
            next_stop_condition=self.renderer.get_stop_sequences(),
            metrics=metrics,
            logs=logs,
        )


@dataclass(frozen=True)
class TradeGroupBuilder(rl.EnvGroupBuilder):
    """group_size attempts at one task, so every rollout in a group sees one state."""

    task: dict
    renderer: renderers.Renderer
    config: dict
    group_size: int
    reward_scale: float
    position_tag: bool = True

    async def make_envs(self):
        env = (self.task, self.renderer, self.config, self.reward_scale)
        return [TradeEnv(*env) for _ in range(self.group_size)]

    def logging_tags(self):
        held = "flat" if self.task["position"] == "flat" else "holding"
        return [self.task["split"], held] if self.position_tag else [self.task["split"]]


class TradeDataset(rl.RLDataset):
    """Every task once per epoch, in a seeded order that is reshuffled each epoch."""

    def __init__(self, tasks, make_group, groups_per_batch, epochs=1, seed=0):
        self.tasks, self.make_group, self.seed = tasks, make_group, seed
        self.groups_per_batch, self.epochs = groups_per_batch, epochs
        self.per_epoch = math.ceil(len(tasks) / groups_per_batch)

    def batch_tasks(self, index):
        epoch, i = divmod(index, self.per_epoch)
        order = list(self.tasks)
        random.Random(f"{self.seed}:{epoch}").shuffle(order)
        return order[i * self.groups_per_batch : (i + 1) * self.groups_per_batch]

    def get_batch(self, index):
        return [self.make_group(task) for task in self.batch_tasks(index)]

    def __len__(self):
        return self.per_epoch * self.epochs


def pick_tasks(tasks, split, seed, flat_share=None, limit=None):
    """One split's tasks. flat_share thins the holding tasks until that share is flat
    (it cannot go below the share as built); limit takes a seeded sample."""
    tasks = [task for task in tasks if task["split"] == split]
    rng = random.Random(f"{seed}:{split}")
    if flat_share is not None:
        if not 0 < flat_share <= 1:
            raise ValueError("flat_share must be in (0, 1]")
        flat = [task for task in tasks if task["position"] == "flat"]
        holding = [task for task in tasks if task["position"] != "flat"]
        keep = round(len(flat) * (1 - flat_share) / flat_share)
        tasks = flat + rng.sample(holding, min(len(holding), keep))
    if limit is not None and len(tasks) > limit:
        tasks = rng.sample(tasks, limit)
    return tasks


@chz.chz
class TradeDatasetBuilder(rl.RLDatasetBuilder):
    tasks_path: str = "data/processed/trade_env/tasks.jsonl.gz"
    model_name: str = "Qwen/Qwen3-8B"
    renderer_name: str = "qwen3_disable_thinking"
    groups_per_batch: int = 64  # tasks per training step
    group_size: int = 8  # completions per task
    epochs: int = 1
    train_split: str = "train"
    eval_split: str = "val"
    eval_max_tasks: int = 200
    seed: int = 0
    flat_share: float | None = None  # None: all four position variants, as built
    # Raw rewards are dollars per share with an sd near 0.13 and the cookbook's numbers
    # assume 0/1 rewards, so rewards are scaled up. Under Adam this does not change the
    # step size (learning_rate alone sets it): it moves the logged reward/total, the
    # fixed 0.5 cut behind by_group/frac_all_good and the weight against a KL penalty.
    # Logged pnl stays in dollars.
    reward_scale: float = 5.0
    # True drops unanimous groups before the train step: the same gradient for fewer
    # train tokens. The cookbook then logs train env/* over the surviving groups only
    # and pairs tags with the wrong groups, so training groups lose their position tag.
    # iteration_*/train_rollout_summaries.jsonl holds the whole batch either way.
    remove_constant_reward_groups: bool = False
    # scoring rules that are not baked into the tasks file
    invalid_penalty: float = DEFAULTS["invalid_penalty"]
    w_log: float = DEFAULTS["w_log"]
    w_missed: float = DEFAULTS["w_missed"]

    def env_config(self):
        keys = ("invalid_penalty", "w_log", "w_missed")
        return make_config({key: getattr(self, key) for key in keys})

    def renderer(self):
        tokenizer = get_tokenizer(self.model_name)
        return renderers.get_renderer(self.renderer_name, tokenizer)

    def datasets(self, tasks, renderer):
        """(train, eval). The eval set is a seeded sample, one completion per task."""
        chosen = pick_tasks(tasks, self.train_split, self.seed, self.flat_share)
        held = pick_tasks(tasks, self.eval_split, self.seed, limit=self.eval_max_tasks)
        if not chosen:
            raise ValueError(f"No {self.train_split} tasks in {self.tasks_path}")
        config, scale = self.env_config(), self.reward_scale

        def group(size, tag=True):
            return lambda task: TradeGroupBuilder(
                task, renderer, config, size, scale, tag
            )

        trained = TradeDataset(
            chosen,
            group(self.group_size, not self.remove_constant_reward_groups),
            self.groups_per_batch,
            self.epochs,
            self.seed,
        )
        return trained, TradeDataset(held, group(1), len(held)) if held else None

    async def __call__(self):
        # returned here, the eval set would be logged as test/ whatever its split is;
        # the launcher hands it to the trainer under the split's own name instead
        return self.datasets(read_tasks(self.tasks_path), self.renderer())[0], None


def replay_matches(tasks, split, seed, limit):
    """Up to limit whole matches of a split, as lists of tasks; a seeded sample."""
    matches = by_match([task for task in tasks if task["split"] == split])
    ids = sorted(matches)
    if len(ids) > limit:
        ids = sorted(random.Random(f"{seed}:replay").sample(ids, limit))
    return [matches[match_id] for match_id in ids]


class Unanswered(Exception):
    """Raised inside a replay at a decision that has no sampled answer yet."""


class MatchReplayEvaluator(SamplingClientEvaluator):
    """Full-match replay: one sampled completion per decision, carrying the position."""

    def __init__(self, matches, renderer, config, max_tokens, split="val"):
        self.matches, self.renderer, self.config = matches, renderer, config
        self.max_tokens, self.split = max_tokens, split

    async def answer(self, policy, task):
        prompt = self.renderer.build_generation_prompt(task["messages"])
        sampled = await policy(prompt, self.renderer.get_stop_sequences())
        text, truncated = read_answer(self.renderer, sampled.tokens)
        # replay_match takes a bare action as already valid; the env wants the JSON
        return None if truncated or text in task["legal_actions"] else text

    async def play(self, policy, match):
        # replay_match takes a plain function, so it is rerun from the start each
        # time it reaches a decision that has not been sampled yet
        answers = {}

        def recall(task):
            if task["id"] not in answers:
                raise Unanswered(task)
            return answers[task["id"]]

        while True:
            try:
                return replay_match(match, recall, self.config)
            except Unanswered as pending:
                task = pending.args[0]
                answers[task["id"]] = await self.answer(policy, task)

    async def eval_token_completer(self, policy):
        played = await asyncio.gather(*[self.play(policy, m) for m in self.matches])
        steps = sum(len(result["steps"]) for result in played)
        invalid = sum(result["invalid"] for result in played)
        metrics = {
            "pnl_per_match_c": mean([cents(result["pnl"]) for result in played]),
            "cost_per_match_c": mean([cents(result["cost"]) for result in played]),
            "trades_per_match": mean([float(result["trades"]) for result in played]),
            "valid_rate": 1 - invalid / steps if steps else 0.0,
        }
        return {f"replay/{self.split}/{key}": value for key, value in metrics.items()}

    async def __call__(self, sampling_client):
        policy = completers.TinkerTokenCompleter(sampling_client, self.max_tokens)
        return await self.eval_token_completer(policy)


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


@chz.chz
class CLI(TradeDatasetBuilder):
    """The dataset settings above, plus the trainer's."""

    lora_rank: int = 32
    learning_rate: float = 1e-5
    max_tokens: int = 256
    eval_every: int = 10  # 0: no evaluation at all
    save_every: int = 20
    replay_matches: int = 72  # eval_split matches replayed at each eval; 0 for none
    loss_fn: tinker.types.LossFnType = "importance_sampling"
    max_steps: int | None = None
    runs_dir: str = "runs/trade_env"
    name: str | None = None  # run directory; by default derived from the settings
    confirm: bool = False
    dry_run: bool = False


def check(cli):
    """Stop on settings that would crash late, teach nothing or skew the estimate."""
    # a group of one has no group mean to beat: it would be paid for and teach nothing
    least = dict(groups_per_batch=1, group_size=2, epochs=1, max_tokens=1, max_steps=1)
    for key in (*least, "eval_every", "save_every", "replay_matches", "eval_max_tasks"):
        value = getattr(cli, key)
        if value is not None and value < least.get(key, 0):
            raise SystemExit(f"{key} must be at least {least.get(key, 0)}, not {value}")
    if not os.path.exists(cli.tasks_path):
        raise SystemExit(f"No tasks file at {cli.tasks_path}: build-tasks writes it")
    splits = {task["split"] for task in read_tasks(cli.tasks_path)}
    needed = [cli.train_split, cli.eval_split] if cli.eval_every else [cli.train_split]
    for split in needed:
        if split not in splits:
            found = ", ".join(sorted(splits))
            raise SystemExit(f"No {split} tasks in {cli.tasks_path}, only {found}")


def dataset_builder(cli):
    fields = chz.chz_fields(TradeDatasetBuilder)
    return TradeDatasetBuilder(**{name: getattr(cli, name) for name in fields})


def settings(cli):
    """What makes a run that run: the settings that shape it and the tasks' bytes."""
    skipped = ("name", "confirm", "max_steps", "runs_dir")
    kept = {k: v for k, v in chz.asdict(cli).items() if k not in skipped}
    kept["tasks_sha256"] = hashlib.sha256(Path(cli.tasks_path).read_bytes()).hexdigest()
    return kept


def run_name(cli):
    """Never a timestamp, so a rerun of the same settings finds its checkpoints."""
    digest = hashlib.sha256(json.dumps(settings(cli), sort_keys=True).encode())
    derived = f"{cli.model_name.split('/')[-1]}-{digest.hexdigest()[:10]}"
    name = derived if cli.name is None else cli.name
    # a dry run wipes its directory, so no name may reach outside runs_dir or into dry-*
    if name in ("", ".", "..") or name != os.path.basename(name) or name[:4] == "dry-":
        raise SystemExit(f"name must be a plain directory name, not dry-*: {name!r}")
    return "dry-" + name if cli.dry_run else name


def resume_step(cli, log_path):
    """The step a run in log_path would carry on from; 0 for a new run. The cookbook
    resumes whatever a directory holds, so one started on other settings is refused."""
    saved = Path(log_path, "settings.json")
    stored = json.loads(saved.read_text()) if saved.exists() else {}
    last = checkpoint_utils.get_last_checkpoint(log_path)
    now = settings(cli)
    changed = sorted(k for k in {**stored, **now} if now.get(k) != stored.get(k))
    if (stored or last) and changed:
        raise SystemExit(
            f"{log_path} holds a run started with another {', '.join(changed)}. "
            "Pick another name= or remove that directory."
        )
    return last.batch if last else 0


def held_out_evaluator(builder, max_tokens):
    held = builder.datasets(read_tasks(builder.tasks_path), builder.renderer())[1]
    return RLTestSetEvaluator(held, max_tokens=max_tokens, name=builder.eval_split)


def replay_evaluator(builder, max_tokens, limit):
    tasks = read_tasks(builder.tasks_path)
    matches = replay_matches(tasks, builder.eval_split, builder.seed, limit)
    config, split = builder.env_config(), builder.eval_split
    return MatchReplayEvaluator(matches, builder.renderer(), config, max_tokens, split)


def train_config(cli):
    builder = dataset_builder(cli)
    evaluators = []
    if cli.eval_every and cli.eval_max_tasks:
        evaluators.append(
            functools.partial(held_out_evaluator, builder, cli.max_tokens)
        )
    if cli.eval_every and cli.replay_matches:
        replays = (replay_evaluator, builder, cli.max_tokens, cli.replay_matches)
        evaluators.append(functools.partial(*replays))
    return train.Config(
        recipe_name="trade_env",
        model_name=cli.model_name,
        renderer_name=cli.renderer_name,
        lora_rank=cli.lora_rank,
        learning_rate=cli.learning_rate,
        max_tokens=cli.max_tokens,
        dataset_builder=builder,
        evaluator_builders=evaluators,
        eval_every=cli.eval_every,
        save_every=cli.save_every,
        remove_constant_reward_groups=cli.remove_constant_reward_groups,
        loss_fn=cli.loss_fn,
        max_steps=cli.max_steps,
        log_path=os.path.expanduser(os.path.join(cli.runs_dir, run_name(cli))),
    )


async def train_then_evaluate(config):
    """The cookbook's loop evaluates before a step, so never the weights it ends on:
    score the final checkpoint once and log it at the step after the last."""
    await train.main(config)
    last = checkpoint_utils.get_last_checkpoint(config.log_path, "sampler_path")
    log = Path(config.log_path, "metrics.jsonl")
    if not (config.evaluator_builders and last and log.exists()):
        return
    if json.loads(log.read_text().splitlines()[-1])["step"] == last.batch:
        return  # a rerun of a finished run
    service = tinker.ServiceClient(base_url=config.base_url)
    client = service.create_sampling_client(model_path=last.sampler_path)
    evaluators = [build() for build in config.evaluator_builders]
    metrics = {"step": last.batch}
    for result in await asyncio.gather(*[evaluate(client) for evaluate in evaluators]):
        metrics.update(result)
    with log.open("a") as out:
        out.write(json.dumps(metrics) + "\n")
    print(f"Final checkpoint evaluated: step {last.batch} in {log}")


def prompt_tokens(tasks, cli):
    """Mean prompt length over a seeded sample of tasks, and how it was counted."""
    sample = random.Random(cli.seed).sample(tasks, min(len(tasks), 300))
    try:
        tokenizer = get_tokenizer(cli.model_name)
    except Exception as error:  # no cached tokenizer and no network, or a new model
        sizes = [sum(len(m["content"]) for m in t["messages"]) / 3.5 for t in sample]
        return mean(sizes), f"characters / 3.5, no tokenizer: {type(error).__name__}"
    renderer = renderers.get_renderer(cli.renderer_name, tokenizer)
    sizes = [renderer.build_generation_prompt(t["messages"]).length for t in sample]
    return mean(sizes), "tokenizer"


def estimate(cli, start=0):
    """Size and rough cost of the steps from start on, worked out from the tasks file.
    Nothing is sent.

    Every prompt is counted at the full prefill rate, prompt tokens are counted as
    trained on, and no group is dropped for constant reward. The plain rows assume
    ANSWER_TOKENS per answer; the _max rows assume every answer runs to max_tokens.
    """
    tasks = read_tasks(cli.tasks_path)
    trained, held = dataset_builder(cli).datasets(tasks, None)
    steps = len(trained) if cli.max_steps is None else min(len(trained), cli.max_steps)
    left = range(start, steps)
    rollouts = cli.group_size * sum(len(trained.batch_tasks(i)) for i in left)
    # one before every eval_every-th step, and one of the final checkpoint
    evals = sum(i % cli.eval_every == 0 for i in left) + 1 if cli.eval_every else 0
    replayed = replay_matches(tasks, cli.eval_split, cli.seed, cli.replay_matches)
    decisions = sum(len({task["t"] for task in match}) for match in replayed)
    sampled = rollouts + evals * (len(held.tasks if held else []) + decisions)
    each, counted = prompt_tokens(trained.tasks, cli)
    prices = PRICES.get(cli.model_name)
    rates = "prefill {}, sample {}, train {}".format(*prices) if prices else "unknown"
    built = sorted({task["scoring"]["config_hash"] for task in tasks})
    out = {
        "model": cli.model_name,
        "tasks_file": f"sha256 {settings(cli)['tasks_sha256'][:12]}, built under "
        + ", ".join(built),
        "train_tasks": len(trained.tasks),
        "steps": f"{len(left)} ({start} to {steps})" if start else steps,
        "train_rollouts": rollouts,
        "evals": evals,
        "eval_rollouts": sampled - rollouts,
        "prompt_tokens_each": f"{each:.0f} ({counted})",
        "prompt_tokens": round(sampled * each),
        "usd_per_million_tokens": rates,
    }
    for suffix, answer in (("", ANSWER_TOKENS), ("_max", cli.max_tokens)):
        learned = round(rollouts * (each + answer))
        tokens = (out["prompt_tokens"], sampled * answer, learned)
        out["completion_tokens" + suffix], out["train_tokens" + suffix] = tokens[1:]
        billed = sum(n * price for n, price in zip(tokens, prices or ()))
        out["dollars" + suffix] = round(billed / 1e6, 2) if prices else "unknown"
    return out


def main(argv=None):
    cli = chz.entrypoint(CLI, argv=argv)
    check(cli)
    if cli.dry_run:
        cli = chz.replace(cli, max_steps=cli.max_steps or 2)
    config = train_config(cli)
    start = 0 if cli.dry_run else resume_step(cli, config.log_path)
    figures = estimate(cli, start)
    print("ESTIMATE ONLY, not a quote. Nothing has been sent to Tinker.")
    for key, value in figures.items():
        print(f"  {key:<24}{value if isinstance(value, str) else format(value, ',')}")
    print(
        f"  Assumes {ANSWER_TOKENS} tokens per answer (_max rows: max_tokens), every "
        "prompt at the full prefill rate and no group dropped. Prices read 2026-10-12."
    )
    if start:
        print(f"  RESUMING {config.log_path} at step {start}: the rest is counted.")
    else:
        print(f"  New run: {config.log_path}")
    if not (cli.confirm or cli.dry_run):
        raise SystemExit("Not started: add confirm=true, or dry_run=true to rehearse.")
    # stop here on a bad renderer or a missing tokenizer, not after the cookbook has
    # opened a training session
    tokenizer = dataset_builder(cli).renderer().tokenizer
    if not cli.dry_run:
        if cli.model_name not in PRICES:
            raise SystemExit(
                f"No price for {cli.model_name}: add its (prefill, sample, train) to "
                "PRICES before confirm=true."
            )
        os.makedirs(config.log_path, exist_ok=True)
        Path(config.log_path, "settings.json").write_text(json.dumps(settings(cli)))
        return asyncio.run(train_then_evaluate(config))
    if not os.path.basename(config.log_path).startswith("dry-"):
        raise SystemExit(f"Not wiping {config.log_path}: it is not a dry run's")
    shutil.rmtree(config.log_path, ignore_errors=True)
    fake = FakeTinker(ScriptedCompleter(tokenizer))
    # the trainer builds its own tinker.ServiceClient, so the fake is swapped in for it
    with mock.patch.object(tinker, "ServiceClient", return_value=fake):
        asyncio.run(train_then_evaluate(config))
    print("Dry run done: fake service, nothing billed. Logs in", config.log_path)


if __name__ == "__main__":
    main()
