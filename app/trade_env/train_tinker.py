"""Train whole-match trajectories with tinker-cookbook's GRPO-style RL trainer.

Ours: the env adapter, the dataset, a full-match replay evaluator and the launcher.
The cookbook's (tinker_cookbook.rl.train.main): rollouts, advantages centred on the
group mean, the importance-sampling loss, checkpoints, resume, eval scheduling, logs.

    uv run --locked --extra train python -m app.trade_env.train_tinker                # print the estimate and stop
    uv run --locked --extra train python -m app.trade_env.train_tinker dry_run=true   # rehearse on a fake service
    uv run --locked --extra train python -m app.trade_env.train_tinker confirm=true   # train; needs TINKER_API_KEY

Install with `uv sync --locked --extra train` first. A run's directory is named after its settings and
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

import chz
import tinker
from tinker_cookbook import checkpoint_utils, completers, renderers
from tinker_cookbook.utils import logtree
from tinker_cookbook.eval.evaluators import SamplingClientEvaluator
from tinker_cookbook.exceptions import RendererError
from tinker_cookbook.rl import train, types as rl
from tinker_cookbook.rl.metric_util import RLTestSetEvaluator
from tinker_cookbook.tokenizer_utils import get_tokenizer
from tinker_cookbook.stores import LocalStorage, TrainingRunStore

from .config import DEFAULTS, make_config, config_hash
from .replay import by_match, Replay
from .reward import score
from .stats import cents, mean
from .tasks import read_tasks, validate_tasks

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
        metrics.update({"forecast/" + k: v for k, v in out.get("forecast", {}).items()})
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

    async def make_envs(self):
        env = (self.task, self.renderer, self.config, self.reward_scale)
        return [TradeEnv(*env) for _ in range(self.group_size)]

    def logging_tags(self):
        held = "flat" if self.task["position"] == "flat" else "holding"
        return [self.task["split"], held]


class MatchEnv(TradeEnv):
    """One whole match, starting flat; fresh observations, carried position."""

    def __init__(self, tasks, renderer, config, reward_scale):
        self.states = {(t["t"], t["position"]): t for t in tasks}
        self.times = sorted({t["t"] for t in tasks})
        self.index = 0
        super().__init__(
            self.states[self.times[0], "flat"], renderer, config, reward_scale
        )

    async def step(self, action, *, extra=None):
        if self.index == len(self.times):
            raise ValueError("Match is complete")
        result = await super().step(action, extra=extra)
        position = self.task["scoring"]["next_position"][result.logs["action"]]
        self.index += 1
        result.episode_done = self.index == len(self.times)
        if not result.episode_done:
            self.task = self.states[self.times[self.index], position]
            (
                result.next_observation,
                result.next_stop_condition,
            ) = await self.initial_observation()
        return result


@dataclass(frozen=True)
class MatchGroupBuilder(rl.EnvGroupBuilder):
    """Independent full trajectories of the same match, all starting flat."""

    tasks: list
    renderer: renderers.Renderer
    config: dict
    group_size: int
    reward_scale: float

    async def make_envs(self):
        return [
            MatchEnv(self.tasks, self.renderer, self.config, self.reward_scale)
            for _ in range(self.group_size)
        ]

    def logging_tags(self):
        return [self.tasks[0]["split"]]

    async def compute_group_rewards(self, trajectories, envs):
        # Step rewards already sum to the full return. Never add P&L twice.
        return [
            (
                0.0,
                {
                    **{
                        key + "_per_match": math.fsum(
                            t.metrics.get("forecast/" + key, 0.0)
                            for t in traj.transitions
                        )
                        for key in ("result_reward", "goal_reward", "price_reward")
                    },
                    "pnl_per_match": math.fsum(
                        t.metrics["pnl"] for t in traj.transitions
                    ),
                    "cost_per_match": math.fsum(
                        t.metrics["cost"] for t in traj.transitions
                    ),
                },
            )
            for traj in trajectories
        ]


class TradeDataset(rl.RLDataset):
    """Every item once per epoch: matches for training, tasks for diagnostic eval."""

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


def pick_tasks(tasks, split, seed, limit=None):
    """One split, optionally taking a seeded sample."""
    tasks = [task for task in tasks if task["split"] == split]
    rng = random.Random(f"{seed}:{split}")
    if limit is not None and len(tasks) > limit:
        tasks = rng.sample(tasks, limit)
    return tasks


@chz.chz
class TradeDatasetBuilder(rl.RLDatasetBuilder):
    tasks_path: str = "data/processed/trade_env/tasks.jsonl.gz"
    model_name: str = "Qwen/Qwen3-8B"
    renderer_name: str = "qwen3_disable_thinking"
    groups_per_batch: int = 8  # matches per optimizer step
    group_size: int = 8  # complete trajectories per match
    epochs: int = 1
    train_split: str = "train"
    eval_split: str = "val"
    eval_max_tasks: int = 200
    seed: int = 0
    # Adam approximately cancels a uniform gradient scale (apart from epsilon,
    # clipping and regularization). Keep reward logs in dollars by default.
    reward_scale: float = 1.0
    config_path: str | None = None  # optional task-build metadata for nondefault rules
    # scoring rules that are not baked into the tasks file
    invalid_penalty: float = DEFAULTS["invalid_penalty"]
    w_log: float = DEFAULTS["w_log"]
    w_missed: float = DEFAULTS["w_missed"]

    w_result: float = DEFAULTS["w_result"]
    w_goal: float = DEFAULTS["w_goal"]
    w_price: float = DEFAULTS["w_price"]

    def env_config(self):
        keys = ("invalid_penalty", "w_log", "w_missed", "w_result", "w_goal", "w_price")
        built = (
            json.loads(Path(self.config_path).read_text())["config"]
            if self.config_path
            else {}
        )
        return make_config({**built, **{key: getattr(self, key) for key in keys}})

    def renderer(self):
        tokenizer = get_tokenizer(self.model_name)
        return renderers.get_renderer(self.renderer_name, tokenizer)

    def datasets(self, tasks, renderer):
        """Train on matches; keep single-decision eval as a separate diagnostic."""
        chosen = list(by_match(pick_tasks(tasks, self.train_split, self.seed)).values())
        held = pick_tasks(tasks, self.eval_split, self.seed, limit=self.eval_max_tasks)
        if not chosen:
            raise ValueError(f"No {self.train_split} tasks in {self.tasks_path}")
        config, scale = self.env_config(), self.reward_scale

        def group(size):
            return lambda task: TradeGroupBuilder(task, renderer, config, size, scale)

        trained = TradeDataset(
            chosen,
            lambda match: MatchGroupBuilder(
                match, renderer, config, self.group_size, scale
            ),
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
        replay = Replay(match, self.config)
        while (task := replay.next_task()) is not None:
            answer = await self.answer(policy, task)
            replay.step(answer)
            replay.steps[-1]["completion"] = answer
        return replay.result()

    async def eval_token_completer(self, policy):
        played = await asyncio.gather(*[self.play(policy, m) for m in self.matches])
        # Persist per-match outcomes in the cookbook evaluation log for paired
        # confidence intervals and trade audits; P&L remains unshaped.
        logtree.log_text(json.dumps({"match_replays": played}))
        steps = sum(len(result["steps"]) for result in played)
        invalid = sum(result["invalid"] for result in played)
        metrics = {
            "pnl_per_match_c": mean([cents(result["pnl"]) for result in played]),
            "cost_per_match_c": mean([cents(result["cost"]) for result in played]),
            "flat_baseline_pnl_c": 0.0,
            "trades_per_match": mean([float(result["trades"]) for result in played]),
            "valid_rate": 1 - invalid / steps if steps else 0.0,
        }
        return {f"replay/{self.split}/{key}": value for key, value in metrics.items()}

    async def __call__(self, sampling_client):
        policy = completers.TinkerTokenCompleter(sampling_client, self.max_tokens)
        return await self.eval_token_completer(policy)


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
    least = dict(
        groups_per_batch=1,
        group_size=2,
        epochs=1,
        max_tokens=1,
        max_steps=1,
        lora_rank=1,
    )
    for key in (*least, "eval_every", "save_every", "replay_matches", "eval_max_tasks"):
        value = getattr(cli, key)
        if value is not None and (type(value) is not int or value < least.get(key, 0)):
            raise SystemExit(f"{key} must be at least {least.get(key, 0)}, not {value}")
    for key in (
        "w_result",
        "w_goal",
        "w_price",
        "learning_rate",
        "reward_scale",
        "invalid_penalty",
        "w_log",
        "w_missed",
    ):
        value = getattr(cli, key)
        if (
            not math.isfinite(value)
            or value < 0
            or (key in ("learning_rate", "reward_scale") and value == 0)
        ):
            raise SystemExit(
                f"{key} must be finite and "
                + (
                    "positive"
                    if key in ("learning_rate", "reward_scale")
                    else "non-negative"
                )
            )
    if cli.loss_fn not in ("importance_sampling", "ppo"):
        raise SystemExit(
            "loss_fn must be importance_sampling or ppo for this RL adapter"
        )
    config = cli.env_config()
    if config["mark"] != "mid" or config["start_mark"] != "decision":
        raise SystemExit(
            "Trajectory training requires mark=mid and start_mark=decision for additive P&L"
        )
    if cli.w_log or cli.w_missed:
        raise SystemExit(
            "Trajectory training requires w_log=0 and w_missed=0; optimize P&L directly"
        )
    if not os.path.exists(cli.tasks_path):
        raise SystemExit(f"No tasks file at {cli.tasks_path}: build-tasks writes it")
    tasks = read_tasks(cli.tasks_path)
    validate_tasks(tasks)
    if config["forecast_rewards"]:
        from .forecast import validate_targets

        validate_targets(tasks)
    if {task["scoring"]["config_hash"] for task in tasks} != {config_hash(config)}:
        raise SystemExit(
            "Tasks were built under another config; pass config_path=tasks.meta.json or rebuild"
        )
    splits = {task["split"] for task in tasks}
    if cli.eval_every and cli.eval_split == cli.train_split:
        raise SystemExit("eval_split must differ from train_split")
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
    # Refuse to resume across changed scoring/training code or dependency versions.
    sources = sorted(Path(__file__).parent.glob("*.py"))
    sources.append(Path(__file__).resolve().parents[2] / "uv.lock")
    kept["code_sha256"] = hashlib.sha256(
        b"".join(p.name.encode() + b"\0" + p.read_bytes() for p in sources)
    ).hexdigest()
    kept["training_unit"] = "match_trajectory/v1"
    kept["env_config"] = cli.env_config()
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
        remove_constant_reward_groups=False,
        num_substeps=1,
        temperature=1.0,
        loss_fn=cli.loss_fn,
        max_steps=cli.max_steps,
        log_path=os.path.expanduser(os.path.join(cli.runs_dir, run_name(cli))),
    )


async def train_then_evaluate(config):
    """The cookbook's loop evaluates before a step, so never the weights it ends on:
    score the final checkpoint once and log it at the step after the last."""
    await train.main(config)
    last = checkpoint_utils.get_last_checkpoint(config.log_path, "sampler_path")
    if not (config.evaluator_builders and last):
        return
    store = TrainingRunStore(LocalStorage(config.log_path))
    if any(
        row.get("final_evaluation") == last.sampler_path and row["step"] == last.batch
        for row in store.read_metrics()
    ):
        return  # a rerun of a finished run
    service = tinker.ServiceClient(base_url=config.base_url)
    client = service.create_sampling_client(model_path=last.sampler_path)
    evaluators = [build() for build in config.evaluator_builders]
    metrics = await train.run_evaluations_parallel(
        evaluators,
        client,
        config,
        last.batch,
        store=store,
    )
    metrics.update(step=last.batch, final_evaluation=last.sampler_path)
    store.write_metrics(metrics)
    print(f"Final checkpoint evaluated: step {last.batch} in {config.log_path}")


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
    episodes = [match for i in left for match in trained.batch_tasks(i)]
    trajectories = cli.group_size * len(episodes)
    rollouts = cli.group_size * sum(len({t["t"] for t in match}) for match in episodes)
    # one before every eval_every-th step, and one of the final checkpoint
    evals = (
        sum(i % cli.eval_every == 0 for i in left) + 1
        if cli.eval_every and (cli.eval_max_tasks or cli.replay_matches)
        else 0
    )
    replayed = replay_matches(tasks, cli.eval_split, cli.seed, cli.replay_matches)
    decisions = sum(len({task["t"] for task in match}) for match in replayed)
    sampled = rollouts + evals * (len(held.tasks if held else []) + decisions)
    each, counted = prompt_tokens([t for match in trained.tasks for t in match], cli)
    prices = PRICES.get(cli.model_name)
    rates = "prefill {}, sample {}, train {}".format(*prices) if prices else "unknown"
    built = sorted({task["scoring"]["config_hash"] for task in tasks})
    out = {
        "model": cli.model_name,
        "tasks_file": f"sha256 {settings(cli)['tasks_sha256'][:12]}, built under "
        + ", ".join(built),
        "train_matches": len(trained.tasks),
        "train_trajectories": trajectories,
        "steps": f"{len(left)} ({start} to {steps})" if start else steps,
        "train_rollouts": rollouts,
        "evals": evals,
        "eval_rollouts": sampled - rollouts,
        "prompt_tokens_each": f"{each:.0f} ({counted})",
        "prompt_tokens": round(sampled * each),
        "usd_per_million_tokens": rates,
    }
    for suffix, answer in (
        (
            "",
            min(
                180 if cli.env_config()["forecast_rewards"] else ANSWER_TOKENS,
                cli.max_tokens,
            ),
        ),
        ("_max", cli.max_tokens),
    ):
        learned = round(rollouts * (each + answer))
        tokens = (out["prompt_tokens"], sampled * answer, learned)
        out["completion_tokens" + suffix], out["train_tokens" + suffix] = tokens[1:]
        billed = sum(n * price for n, price in zip(tokens, prices or ()))
        out["dollars" + suffix] = round(billed / 1e6, 2) if prices else "unknown"
    return out


def main(argv=None):
    cli = chz.entrypoint(CLI, argv=argv)
    try:
        check(cli)
    except (ValueError, OSError, KeyError) as error:
        raise SystemExit(str(error)) from error
    if cli.dry_run:
        cli = chz.replace(cli, max_steps=cli.max_steps or 2)
    config = train_config(cli)
    start = 0 if cli.dry_run else resume_step(cli, config.log_path)
    if (
        cli.confirm
        and not cli.dry_run
        and not os.environ.get("TINKER_API_KEY", "").strip()
    ):
        raise SystemExit("TINKER_API_KEY is required for confirm=true")
    figures = estimate(cli, start)
    print("ESTIMATE ONLY, not a quote. Nothing has been sent to Tinker.")
    for key, value in figures.items():
        print(f"  {key:<24}{value if isinstance(value, str) else format(value, ',')}")
    print(
        f"  Assumes {180 if cli.env_config()['forecast_rewards'] else ANSWER_TOKENS} tokens per answer (_max rows: max_tokens), every "
        "prompt at the full prefill rate and no group dropped. Prices read 2026-10-12."
    )
    if start:
        print(f"  Resuming from step {start}: {config.log_path}")
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
    from tests.trade_env.fake_tinker import run_dry

    run_dry(config, tokenizer, train_then_evaluate)


if __name__ == "__main__":
    main()
