"""The Tinker trainer adapter, offline: no API key, no model, a fake service."""

import asyncio
import copy
import contextlib
import hashlib
import io
import json
import logging
import math
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import tinker
import torch
from tinker_cookbook.rl import train
from tinker_cookbook.rl.data_processing import (
    assemble_training_data,
    compute_advantages,
)
from tinker_cookbook.rl.rollouts import do_group_rollout
from tests.trade_env.fake_tinker import FakeTinker, ScriptedCompleter

from app.trade_env import train_tinker as tt
from app.trade_env.config import make_config
from app.trade_env.replay import replay
from app.trade_env.reward import score
from app.trade_env.stats import cents, mean
from app.trade_env.tasks import build_tasks, load_records, write_tasks

FIXTURES = Path(__file__).parent / "fixtures" / "records.jsonl.gz"
CONFIG = make_config()
TASKS = build_tasks(load_records(FIXTURES), CONFIG)[0]
BUILDER = tt.TradeDatasetBuilder(groups_per_batch=6, group_size=5)
RENDERER = BUILDER.renderer()  # missing tokenizer is a test failure, never a skip
TOKENIZER = RENDERER.tokenizer
STOP = RENDERER.get_stop_sequences()[0]
SCALE = BUILDER.reward_scale
RL_KEYS = {"target_tokens", "logprobs", "advantages"}


def task(position="flat", split="train"):
    return next(t for t in TASKS if t["position"] == position and t["split"] == split)


def answer(action):
    probs = {"home": 0.5, "draw": 0.3, "away": 0.2}
    return json.dumps({"probs": probs, "action": action, "reason": "test"})


def tokens(text, finished=True):
    ids = TOKENIZER.encode(text, add_special_tokens=False)
    return ids + [STOP] if finished else ids


def prompt_text(one):
    return TOKENIZER.decode(RENDERER.build_generation_prompt(one["messages"]).to_ints())


def lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def step(one, text, finished=True, config=CONFIG):
    env = tt.TradeEnv(one, RENDERER, config, SCALE)
    return asyncio.run(env.step(tokens(text, finished)))


class SamePromptSameAnswer(ScriptedCompleter):
    """The scripted policy made a function of the prompt, and a count of its calls."""

    calls = 0

    def answer(self, prompt):
        self.calls += 1
        self.rng.seed(prompt)
        return super().answer(prompt)


class BareAction(ScriptedCompleter):
    """Drops the JSON: the first legal action as the whole answer."""

    def answer(self, prompt):
        return prompt.split("Legal actions: ")[-1].split(",")[0].strip(), True


class StillWhenFlat(ScriptedCompleter):
    """Every flat group agrees on do_nothing, so its reward is constant; the groups
    that hold a position get a mix of legal answers and junk."""

    def answer(self, prompt):
        if "do_nothing" in prompt.split("Legal actions: ")[-1]:
            return answer("do_nothing"), True
        legal = ["keep", "close", "nonsense"]
        return answer(legal[self.rng.randrange(3)]), True


class ResumableFake(FakeTinker):
    """The dry-run fake plus the two calls the cookbook makes when it resumes."""

    resumed = None

    async def resume(self, path, **more):
        self.resumed = path
        return self

    create_training_client_from_state_with_optimizer_async = resume

    def create_rest_client(self):
        raise ValueError("no REST client offline")


@contextlib.contextmanager
def quiet():
    """Swallow the trainer's console output and drop the log handlers it installs."""
    handlers = logging.root.handlers[:]
    logging.disable(logging.CRITICAL)
    try:
        with contextlib.redirect_stdout(io.StringIO()) as out:
            with contextlib.redirect_stderr(io.StringIO()):
                yield out
    finally:
        logging.disable(logging.NOTSET)
        for handler in logging.root.handlers[:]:
            if handler not in handlers:
                logging.root.removeHandler(handler)
                handler.close()


class EnvTests(unittest.TestCase):
    def test_reward_is_the_core_score_times_the_scale(self):
        for position, good, illegal in (
            ("flat", "buy_home", "close"),
            ("home", "switch_draw", "buy_away"),
        ):
            one = task(position)
            for text in (answer(good), answer(illegal), "no JSON here"):
                with self.subTest(position=position, text=text):
                    expected = score(one, text, CONFIG)
                    result = step(one, text)
                    self.assertEqual(result.reward, expected["reward"] * SCALE)
                    self.assertTrue(result.episode_done)
                    self.assertEqual(result.metrics["pnl"], expected["pnl"])
                    self.assertEqual(result.metrics["valid"], float(expected["valid"]))
                    self.assertEqual(result.metrics["truncated"], 0.0)
                    self.assertEqual(result.logs["action"], expected["action"])
                    self.assertEqual(result.logs["completion"], text)

    def test_the_three_kinds_of_answer_are_what_they_claim(self):
        one = task("flat")
        self.assertTrue(score(one, answer("buy_home"), CONFIG)["valid"])
        self.assertNotEqual(score(one, answer("buy_home"), CONFIG)["reward"], 0.0)
        self.assertEqual(score(one, answer("close"), CONFIG)["error"], "illegal_action")
        self.assertEqual(score(one, "no JSON here", CONFIG)["error"], "no_json")

    def test_a_cut_off_completion_is_invalid_even_with_a_whole_answer_in_it(self):
        for position in ("flat", "home"):
            one = task(position)
            result = step(one, answer(one["legal_actions"][0]), finished=False)
            held = score(one, None, CONFIG)
            self.assertFalse(held["valid"])
            self.assertEqual(result.reward, held["reward"] * SCALE)
            self.assertEqual(
                held["reward"],
                score(one, "no JSON here", CONFIG)["reward"],
            )
            self.assertEqual(result.metrics["valid"], 0.0)
            self.assertEqual(result.metrics["truncated"], 1.0)
            self.assertEqual(result.metrics["traded"], 0.0)

    def test_an_empty_completion_does_not_crash(self):
        env = tt.TradeEnv(task(), RENDERER, CONFIG, SCALE)
        result = asyncio.run(env.step([]))
        self.assertEqual(result.reward, -CONFIG["invalid_penalty"] * SCALE)

    def test_a_second_stop_token_is_invalid_not_a_crash(self):
        one = task("home")
        env = tt.TradeEnv(one, RENDERER, CONFIG, SCALE)
        for action in ([STOP, STOP, STOP], tokens(answer("close")) + [STOP]):
            with self.assertRaises(ValueError):
                RENDERER.parse_response(action)
            result = asyncio.run(env.step(action))
            self.assertEqual(result.reward, score(one, None, CONFIG)["reward"] * SCALE)
            self.assertEqual(result.metrics["valid"], 0.0)
            self.assertEqual(result.metrics["truncated"], 1.0)
            self.assertEqual(result.metrics["traded"], 0.0)
        self.assertIn('"action": "close"', result.logs["completion"])

    def test_every_step_reports_the_same_numeric_metrics(self):
        one = task("draw")
        results = [
            step(one, answer("keep")),
            step(one, answer("close")),
            step(one, answer("buy_home")),
            step(one, "no JSON here"),
            step(one, answer("keep"), finished=False),
        ]
        keys = {
            "pnl",
            "valid",
            "traded",
            "noop",
            "cost",
            "missed",
            "log_score_edge",
            "truncated",
        }
        for result in results:
            self.assertEqual(set(result.metrics), keys)
            for value in result.metrics.values():
                self.assertIsInstance(value, float)
                self.assertTrue(math.isfinite(value))
        kept, closed, illegal, unparsable, cut = [r.metrics for r in results]
        self.assertEqual((kept["noop"], kept["traded"]), (1.0, 0.0))
        self.assertEqual((closed["noop"], closed["traded"]), (0.0, 1.0))
        self.assertGreater(closed["cost"], 0.0)
        self.assertEqual(unparsable["log_score_edge"], 0.0)
        for broken in (illegal, unparsable, cut):
            self.assertEqual((broken["noop"], broken["traded"]), (0.0, 0.0))

    def test_score_time_overrides_reach_the_env(self):
        one = task("flat")
        config = tt.TradeDatasetBuilder(invalid_penalty=0.5).env_config()
        self.assertEqual(step(one, "nope", config=config).reward, -0.5 * SCALE)
        config = tt.TradeDatasetBuilder(w_log=1.0, w_missed=2.0).env_config()
        result = step(one, answer("buy_home"), config=config)
        shaped = result.metrics["pnl"] + result.metrics["log_score_edge"]
        shaped -= 2.0 * result.metrics["missed"]
        self.assertAlmostEqual(result.reward, shaped * SCALE)
        self.assertNotAlmostEqual(result.reward, result.metrics["pnl"] * SCALE)


class GroupTests(unittest.TestCase):
    def test_a_groups_envs_share_one_observation(self):
        one = task("away")
        group = tt.TradeGroupBuilder(one, RENDERER, CONFIG, 6, SCALE)
        envs = asyncio.run(group.make_envs())
        self.assertEqual(len(envs), 6)
        self.assertEqual(len({id(env) for env in envs}), 6)
        seen = [asyncio.run(env.initial_observation()) for env in envs]
        expected = RENDERER.build_generation_prompt(one["messages"]).to_ints()
        for prompt, stop in seen:
            self.assertEqual(prompt.to_ints(), expected)
            self.assertEqual(stop, [STOP])
        self.assertTrue(prompt_text(one).endswith("</think>\n\n"))
        self.assertNotIn("action_rewards", prompt_text(one))

    def test_hidden_labels_never_enter_the_model_input(self):
        original = task()
        changed = copy.deepcopy(original)
        changed["scoring"] = {"secret": "FUTURE_RESULT_SENTINEL"}
        changed["match_id"] = "HIDDEN_MATCH_SENTINEL"
        changed["id"] = "HIDDEN_TASK_SENTINEL"
        envs = [tt.TradeEnv(t, RENDERER, CONFIG, SCALE) for t in (original, changed)]
        observations = [asyncio.run(e.initial_observation())[0].to_ints() for e in envs]
        self.assertEqual(*observations)

    def test_logging_tags(self):
        def tags(one):
            return tt.TradeGroupBuilder(one, RENDERER, CONFIG, 1, SCALE).logging_tags()

        self.assertEqual(tags(task("flat", "train")), ["train", "flat"])
        self.assertEqual(tags(task("draw", "val")), ["val", "holding"])


class TrainingDataTests(unittest.TestCase):
    """Real cookbook rollouts and data assembly, driven by the scripted completer."""

    @classmethod
    def setUpClass(cls):
        dataset = tt.TradeDataset(
            TASKS, lambda t: tt.TradeGroupBuilder(t, RENDERER, CONFIG, 5, SCALE), 6
        )
        policy = ScriptedCompleter(TOKENIZER)

        async def collect():
            return [await do_group_rollout(g, policy) for g in dataset.get_batch(0)]

        cls.groups = asyncio.run(collect())
        cls.advantages = compute_advantages(cls.groups)

    def rewards(self, group):
        return [trajectory.transitions[0].reward for trajectory in group.trajectories_G]

    def test_rollouts_carry_the_scaled_env_reward(self):
        self.assertEqual(len(self.groups), 6)
        seen = set()
        for group in self.groups:
            self.assertEqual(len(group.trajectories_G), 5)
            self.assertEqual(group.get_total_rewards(), self.rewards(group))
            for trajectory in group.trajectories_G:
                (transition,) = trajectory.transitions
                metrics = transition.metrics
                raw = metrics["pnl"] - CONFIG["invalid_penalty"] * (
                    1 - metrics["valid"]
                )
                self.assertAlmostEqual(transition.reward, raw * SCALE)
                seen.add((metrics["valid"], metrics["truncated"]))
        self.assertEqual(seen, {(1.0, 0.0), (0.0, 0.0), (0.0, 1.0)})

    def test_advantages_are_reward_minus_the_group_mean(self):
        mixed = 0
        for group, advantages in zip(self.groups, self.advantages):
            rewards = self.rewards(group)
            centre = sum(rewards) / len(rewards)
            mixed += len(set(rewards)) > 1
            self.assertEqual(len(advantages), len(rewards))
            for advantage, reward in zip(advantages.tolist(), rewards):
                self.assertAlmostEqual(advantage, reward - centre, places=5)
            self.assertAlmostEqual(sum(advantages.tolist()), 0.0, places=5)
        self.assertGreater(mixed, 0)

    def test_constant_groups_have_zero_advantage(self):
        group = copy.deepcopy(self.groups[0])
        for trajectory in group.trajectories_G:
            trajectory.transitions[0].reward = 0.25
        group.final_rewards_G[:] = [0.0] * len(group.trajectories_G)
        self.assertEqual(compute_advantages([group])[0].tolist(), [0.0] * 5)

    def test_local_policy_gradient_increases_expected_trading_reward(self):
        # An exact four-action, one-token policy isolates the update's sign.
        # This checks our rewards + cookbook advantages + documented IS loss;
        # it does not pretend to test the remote Tinker optimizer.
        one = task()
        rewards = torch.tensor(
            [score(one, answer(a), CONFIG)["reward"] for a in one["legal_actions"]]
        )
        group = tt.rl.TrajectoryGroup(
            trajectories_G=[
                tt.rl.Trajectory(transitions=[], final_ob=None) for _ in rewards
            ],
            final_rewards_G=rewards.tolist(),
            metrics_G=[{} for _ in rewards],
        )
        advantages = compute_advantages([group])[0]
        logits = torch.zeros(4, requires_grad=True)
        old_logprobs = logits.log_softmax(0).detach()
        before = (logits.softmax(0) * rewards).sum().item()
        optimizer = torch.optim.Adam([logits], lr=0.01)
        loss = -(torch.exp(logits.log_softmax(0) - old_logprobs) * advantages).sum()
        loss.backward()
        self.assertTrue(torch.isfinite(logits.grad).all())
        self.assertLess(logits.grad[rewards.argmax()].item(), 0)
        optimizer.step()
        self.assertGreater((logits.softmax(0) * rewards).sum().item(), before)

    def test_datums_carry_only_rl_inputs_and_no_advantage_on_the_prompt(self):
        data, places = assemble_training_data(self.groups, self.advantages)
        self.assertEqual(len(data), 30)
        for datum, place in zip(data, places):
            group = self.groups[place["group_idx"]]
            (transition,) = group.trajectories_G[place["traj_idx"]].transitions
            advantage = self.advantages[place["group_idx"]][place["traj_idx"]].item()
            sent = train._remove_mask(datum).loss_fn_inputs
            self.assertEqual(set(sent), RL_KEYS)
            prompt, action = transition.ob.to_ints(), transition.ac.tokens
            n = len(prompt) - 1
            self.assertEqual(datum.model_input.to_ints(), (prompt + action)[:-1])
            self.assertEqual(sent["target_tokens"].tolist(), (prompt + action)[1:])
            advantages = sent["advantages"].tolist()
            self.assertEqual(advantages[:n], [0.0] * n)
            self.assertEqual(len(advantages[n:]), len(action))
            for value in advantages[n:]:
                self.assertAlmostEqual(value, advantage, places=5)
            logprobs = sent["logprobs"].tolist()
            self.assertEqual(logprobs[:n], [0.0] * n)
            for value, sampled in zip(logprobs[n:], transition.ac.logprobs):
                self.assertAlmostEqual(value, sampled, places=5)


class TrajectoryTests(unittest.TestCase):
    def setUp(self):
        self.match = next(
            iter(tt.by_match([t for t in TASKS if t["split"] == "train"]).values())
        )

    def test_position_carries_and_return_equals_cash_flow(self):
        env = tt.MatchEnv(self.match, RENDERER, CONFIG, 1.0)
        actions = ["buy_home", "switch_draw", "close", "buy_away", "keep"]
        results, cash, held = [], 0.0, None
        for i, action in enumerate(actions):
            one = env.task
            fill = one["scoring"]["fill_prices"]

            def fee(p):
                return 0.05 * p * (1 - p)

            if action == "close" or action.startswith("switch_"):
                p = fill[held]
                cash += max(0.0, min(p, max(0.001, p - 0.01)) - fee(p))
                held = None
            if action.startswith(("buy_", "switch_")):
                held = action.split("_", 1)[1]
                p = fill[held]
                cash -= min(1.0, max(p, min(0.999, p + 0.01)) + fee(p))
            result = asyncio.run(env.step(tokens(answer(action))))
            results.append(result)
            self.assertEqual(result.episode_done, i == 4)
            if i < 4:
                self.assertEqual(env.task["position"], held or "flat")
                self.assertIn(
                    "Position:", TOKENIZER.decode(result.next_observation.to_ints())
                )
        cash += float(held == one["scoring"]["result"])
        self.assertAlmostEqual(sum(r.reward for r in results), cash)
        self.assertEqual(results[-1].next_observation.length, 0)
        with self.assertRaisesRegex(ValueError, "complete"):
            asyncio.run(env.step(tokens(answer("keep"))))

    def test_invalid_or_truncated_answer_continues_holding(self):
        env = tt.MatchEnv(self.match, RENDERER, CONFIG, 1.0)
        asyncio.run(env.step(tokens(answer("buy_home"))))
        for ids in (tokens("buy_away"), tokens(answer("close"), finished=False)):
            result = asyncio.run(env.step(ids))
            self.assertFalse(result.episode_done)
            self.assertEqual(env.task["position"], "home")
            self.assertEqual(result.metrics["valid"], 0.0)
            self.assertAlmostEqual(
                result.reward, result.metrics["pnl"] - CONFIG["invalid_penalty"]
            )

    def test_whole_return_advantage_reaches_every_generated_turn(self):
        group = tt.MatchGroupBuilder(self.match, RENDERER, CONFIG, 8, 1.0)
        rolled = asyncio.run(do_group_rollout(group, ScriptedCompleter(TOKENIZER)))
        advantages = compute_advantages([rolled])
        self.assertEqual(rolled.final_rewards_G, [0.0] * 8)
        self.assertEqual([len(t.transitions) for t in rolled.trajectories_G], [5] * 8)
        self.assertGreater(advantages[0].abs().sum().item(), 0)
        data, places = assemble_training_data([rolled], advantages)
        self.assertEqual(len(data), 40)
        for i, trajectory in enumerate(rolled.trajectories_G):
            expected = sum(t.reward for t in trajectory.transitions)
            self.assertAlmostEqual(rolled.get_total_rewards()[i], expected)
            self.assertAlmostEqual(
                rolled.metrics_G[i]["pnl_per_match"],
                sum(t.metrics["pnl"] for t in trajectory.transitions),
            )
        for datum, place in zip(data, places):
            mask = datum.loss_fn_inputs["mask"].to_torch().bool()
            advantage = datum.loss_fn_inputs["advantages"].to_torch()
            self.assertTrue(torch.all(advantage[~mask] == 0))
            self.assertTrue(
                torch.allclose(
                    advantage[mask],
                    advantages[0][place["traj_idx"]].expand_as(advantage[mask]),
                )
            )
        # Changing only the last reward changes the FIRST turn's advantage too.
        altered = copy.deepcopy(rolled)
        altered.trajectories_G[0].transitions[-1].reward += 1
        changed = compute_advantages([altered])
        self.assertAlmostEqual(
            (changed[0][0] - advantages[0][0]).item(), 7 / 8, places=5
        )

    def test_trajectories_do_not_share_positions(self):
        group = tt.MatchGroupBuilder(self.match, RENDERER, CONFIG, 2, 1.0)
        left, right = asyncio.run(group.make_envs())
        asyncio.run(left.step(tokens(answer("buy_home"))))
        self.assertEqual(right.task["position"], "flat")
        self.assertEqual(right.index, 0)


class DatasetTests(unittest.TestCase):
    def test_each_epoch_contains_each_match_once(self):
        builder = tt.TradeDatasetBuilder(groups_per_batch=1, group_size=3, epochs=3)
        dataset, held = builder.datasets(TASKS, RENDERER)
        self.assertEqual(len(dataset), 3)  # fixture has one training match
        for i in range(3):
            (group,) = dataset.get_batch(i)
            self.assertEqual(len(group.tasks), 20)
            self.assertEqual(group.group_size, 3)
            self.assertEqual({t["split"] for t in group.tasks}, {"train"})
        self.assertEqual(len(held.tasks), 40)

    def test_match_order_is_seeded_and_reshuffled(self):
        matches = [
            [dict(t, match_id=str(i)) for t in TASKS if t["split"] == "train"]
            for i in range(12)
        ]

        def order(seed, epoch):
            dataset = tt.TradeDataset(matches, lambda m: m, 5, epochs=2, seed=seed)
            return [
                m[0]["match_id"]
                for b in range(epoch * 3, epoch * 3 + 3)
                for m in dataset.get_batch(b)
            ]

        self.assertEqual(order(0, 0), order(0, 0))
        self.assertNotEqual(order(0, 0), order(0, 1))
        self.assertNotEqual(order(0, 0), order(1, 0))
        self.assertEqual(sorted(order(0, 1)), sorted(str(i) for i in range(12)))

    def test_eval_remains_a_single_decision_diagnostic(self):
        builder = tt.TradeDatasetBuilder(eval_max_tasks=7)
        _, held = builder.datasets(TASKS, RENDERER)
        groups = held.get_batch(0)
        self.assertEqual(len(groups), 7)
        self.assertEqual({g.task["split"] for g in groups}, {"val"})
        self.assertEqual({g.group_size for g in groups}, {1})
        again = builder.datasets(TASKS, RENDERER)[1]
        self.assertEqual(held.tasks, again.tasks)

    def test_a_split_without_tasks(self):
        with self.assertRaises(ValueError):
            tt.TradeDatasetBuilder(train_split="nope").datasets(TASKS, RENDERER)
        self.assertIsNone(
            tt.TradeDatasetBuilder(eval_split="nope").datasets(TASKS, RENDERER)[1]
        )


class ReplayEvaluatorTests(unittest.TestCase):
    def test_pnl_matches_the_core_replay_under_the_same_policy(self):
        val = [t for t in TASKS if t["split"] == "val"]
        matches = tt.replay_matches(TASKS, "val", 0, 10)
        self.assertEqual(sorted(len(match) for match in matches), [20, 20])
        policy = SamePromptSameAnswer(TOKENIZER)

        def scripted(one):
            text, finished = policy.answer(prompt_text(one))
            return text if finished else None

        expected = replay(val, lambda match: scripted, CONFIG)
        policy.calls = 0
        evaluator = tt.MatchReplayEvaluator(matches, RENDERER, CONFIG, 256, "val")
        metrics = asyncio.run(evaluator.eval_token_completer(policy))
        steps = sum(len(result["steps"]) for result in expected)
        invalid = sum(result["invalid"] for result in expected)
        self.assertEqual(policy.calls, steps)
        self.assertEqual(steps, 10)
        self.assertEqual(
            metrics,
            {
                "replay/val/pnl_per_match_c": mean([cents(r["pnl"]) for r in expected]),
                "replay/val/cost_per_match_c": mean(
                    [cents(r["cost"]) for r in expected]
                ),
                "replay/val/trades_per_match": mean(
                    [float(r["trades"]) for r in expected]
                ),
                "replay/val/flat_baseline_pnl_c": 0.0,
                "replay/val/valid_rate": 1 - invalid / steps,
            },
        )
        self.assertGreater(sum(result["trades"] for result in expected), 0)
        self.assertGreater(invalid, 0)

    def test_a_bare_action_is_invalid_in_replay_as_it_is_in_the_env(self):
        matches = tt.replay_matches(TASKS, "val", 0, 10)
        evaluator = tt.MatchReplayEvaluator(matches, RENDERER, CONFIG, 256, "val")
        metrics = asyncio.run(evaluator.eval_token_completer(BareAction(TOKENIZER)))
        still = replay(sum(matches, []), lambda match: lambda one: None, CONFIG)
        self.assertEqual(metrics["replay/val/valid_rate"], 0.0)
        self.assertEqual(metrics["replay/val/trades_per_match"], 0.0)
        self.assertEqual(
            metrics["replay/val/pnl_per_match_c"],
            mean([cents(r["pnl"]) for r in still]),
        )
        one = next(t for t in matches[0] if t["position"] == "flat")
        result = step(one, "buy_home")
        self.assertEqual(
            (result.metrics["valid"], result.metrics["traded"]), (0.0, 0.0)
        )

    def test_replay_matches_takes_a_seeded_sample_of_whole_matches(self):
        one = tt.replay_matches(TASKS, "val", 0, 1)
        self.assertEqual(len(one), 1)
        self.assertEqual(len({t["match_id"] for t in one[0]}), 1)
        self.assertEqual(len(one[0]), 20)
        self.assertEqual(one, tt.replay_matches(TASKS, "val", 0, 1))
        self.assertEqual(tt.replay_matches(TASKS, "val", 0, 0), [])


class LauncherTests(unittest.TestCase):
    def setUp(self):
        credentials = mock.patch.dict(
            os.environ, {"TINKER_API_KEY": "test-only-not-a-real-key"}
        )
        credentials.start()
        self.addCleanup(credentials.stop)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.runs = self.root / "runs"
        self.tasks = [t for t in TASKS if t["split"] != "train"]
        for i in range(20):
            self.tasks += [
                dict(t, id=f"{i}:{t['id']}", match_id=f"train-{i}")
                for t in TASKS
                if t["split"] == "train"
            ]
        self.tasks.sort(key=lambda t: t["split"] != "train")
        write_tasks(self.root / "tasks.jsonl.gz", self.tasks)
        self.args = [
            f"tasks_path={self.root / 'tasks.jsonl.gz'}",
            f"runs_dir={self.runs}",
            "groups_per_batch=4",
            "group_size=4",
            "eval_max_tasks=8",
            "replay_matches=2",
        ]

    def launch(self, *args, policy=None):
        """confirm=true against a fake service: (the fake, what was printed)."""
        fake = ResumableFake(policy or ScriptedCompleter(TOKENIZER))
        with mock.patch.object(tinker, "ServiceClient", return_value=fake):
            with quiet() as out:
                tt.main(self.args + ["confirm=true", *args])
        return fake, out.getvalue()

    def refused(self, *args):
        """The launcher's one-line refusal, with the trainer and service off limits."""

        def forbidden(*args, **kwargs):
            raise AssertionError("the launcher reached Tinker")

        with mock.patch.object(tinker, "ServiceClient", forbidden):
            with mock.patch.object(train, "main", forbidden):
                with quiet() as out, self.assertRaises(SystemExit) as stopped:
                    tt.main(self.args + list(args))
        self.assertIsInstance(stopped.exception.code, str)
        return stopped.exception.code, out.getvalue()

    def run_dirs(self):
        return sorted(path.name for path in self.runs.iterdir())

    def test_refuses_to_start_without_confirm(self):
        message, printed = self.refused()
        self.assertIn("confirm=true", message)
        self.assertIn("ESTIMATE ONLY", printed)
        self.assertIn("dollars", printed)
        self.assertIn("New run: " + str(self.runs), printed)
        self.assertFalse(self.runs.exists())

    def test_confirm_hands_the_cookbook_trainer_the_grpo_settings(self):
        started = mock.AsyncMock()
        with mock.patch.object(train, "main", started), quiet():
            tt.main(self.args + ["confirm=true"])
            tt.main(self.args + ["confirm=true"])
        (first,), (second,) = [call.args for call in started.await_args_list]
        self.assertEqual(first.loss_fn, "importance_sampling")
        self.assertFalse(first.remove_constant_reward_groups)
        self.assertEqual(first.model_name, "Qwen/Qwen3-8B")
        self.assertEqual(first.renderer_name, "qwen3_disable_thinking")
        self.assertEqual((first.lora_rank, first.learning_rate), (32, 1e-5))
        self.assertEqual((first.max_tokens, first.max_steps), (256, None))
        self.assertEqual((first.eval_every, first.save_every), (10, 20))
        self.assertEqual(first.kl_penalty_coef, 0.0)
        self.assertEqual(first.num_substeps, 1)
        self.assertEqual(first.temperature, 1.0)
        self.assertIsNone(first.async_config)
        self.assertEqual(first.dataset_builder.group_size, 4)
        self.assertEqual(Path(first.log_path).parent, self.runs)
        self.assertEqual(first.log_path, second.log_path)
        with mock.patch.object(train, "main", started), quiet():
            tt.main(self.args + ["confirm=true", "learning_rate=3e-5", "name=mine"])
            tt.main(self.args + ["confirm=true", "learning_rate=3e-5"])
        named, other = [call.args[0] for call in started.await_args_list[2:]]
        self.assertEqual(Path(named.log_path).name, "mine")
        self.assertNotEqual(other.log_path, first.log_path)

    def test_held_out_tasks_are_evaluated_under_their_splits_name(self):
        cli = tt.chz.entrypoint(tt.CLI, argv=self.args)
        config = tt.train_config(cli)
        trained, second = asyncio.run(config.dataset_builder())
        self.assertIsNone(second)  # the cookbook would log a second dataset as test/
        self.assertEqual(len(trained), 5)
        held, replays = [build() for build in config.evaluator_builders]
        self.assertEqual((held.name, held.max_tokens), ("val", 256))
        self.assertEqual(len(held.env_group_builders_P), 8)
        self.assertEqual((replays.split, len(replays.matches)), ("val", 2))
        for off in ("eval_every=0", "eval_max_tasks=0", "replay_matches=0"):
            config = tt.train_config(tt.chz.entrypoint(tt.CLI, argv=self.args + [off]))
            self.assertEqual(len(config.evaluator_builders), 0 if "every" in off else 1)

    def test_defaults(self):
        cli = tt.CLI()
        self.assertEqual((cli.group_size, cli.groups_per_batch, cli.epochs), (8, 8, 1))
        self.assertEqual((cli.reward_scale, cli.eval_max_tasks), (1.0, 200))
        self.assertEqual((cli.train_split, cli.eval_split), ("train", "val"))
        self.assertEqual(cli.tasks_path, "data/processed/trade_env/tasks.jsonl.gz")
        self.assertEqual(cli.runs_dir, "runs/trade_env")
        self.assertFalse(cli.confirm or cli.dry_run)
        self.assertIn(cli.model_name, tt.PRICES)

    def test_estimate_counts_the_run(self):
        cli = tt.chz.entrypoint(tt.CLI, argv=self.args + ["epochs=2", "eval_every=3"])
        est = tt.estimate(cli)
        self.assertEqual((est["train_matches"], est["steps"]), (20, 10))
        self.assertEqual(est["train_rollouts"], 2 * 20 * 4 * 5)
        self.assertEqual(est["evals"], 4 + 1)  # before steps 0, 3, 6, 9 and at the end
        self.assertEqual(est["eval_rollouts"], 5 * (8 + 2 * 5))
        sampled = est["train_rollouts"] + est["eval_rollouts"]
        self.assertEqual(est["completion_tokens"], sampled * tt.ANSWER_TOKENS)
        self.assertEqual(est["completion_tokens_max"], sampled * 256)
        self.assertIn("tokenizer", est["prompt_tokens_each"])
        self.assertGreater(est["prompt_tokens"], sampled * 500)
        self.assertGreater(est["train_tokens"], est["train_rollouts"] * 500)
        prefill, sample, trained = tt.PRICES["Qwen/Qwen3-8B"]
        dollars = (
            est["prompt_tokens"] * prefill
            + est["completion_tokens"] * sample
            + est["train_tokens"] * trained
        ) / 1e6
        self.assertAlmostEqual(est["dollars"], dollars, places=2)
        self.assertGreater(est["dollars_max"], est["dollars"])
        capped = tt.estimate(tt.chz.replace(cli, max_steps=3))
        self.assertEqual(
            (capped["steps"], capped["train_rollouts"]), (3, 3 * 4 * 4 * 5)
        )
        self.assertEqual(capped["evals"], 1 + 1)
        rest = tt.estimate(cli, start=7)  # steps 7, 8, 9: one eval, and the final one
        self.assertEqual((rest["steps"], rest["train_rollouts"]), ("3 (7 to 10)", 240))
        self.assertEqual((rest["evals"], rest["eval_rollouts"]), (2, 2 * 18))
        none = tt.estimate(tt.chz.replace(cli, eval_every=0))
        self.assertEqual((none["evals"], none["eval_rollouts"]), (0, 0))
        digest = hashlib.sha256((self.root / "tasks.jsonl.gz").read_bytes()).hexdigest()
        self.assertIn(digest[:12], est["tasks_file"])
        self.assertIn(self.tasks[0]["scoring"]["config_hash"], est["tasks_file"])

    def test_estimate_works_without_a_tokenizer_or_a_price(self):
        cli = tt.chz.entrypoint(tt.CLI, argv=self.args + ["model_name=Qwen/Nope"])
        with mock.patch.object(tt, "get_tokenizer", side_effect=OSError("offline")):
            est = tt.estimate(cli)
        self.assertIn("characters / 3.5", est["prompt_tokens_each"])
        self.assertGreater(est["prompt_tokens"], 0)
        self.assertEqual((est["dollars"], est["dollars_max"]), ("unknown", "unknown"))
        self.assertEqual(est["usd_per_million_tokens"], "unknown")

    def test_a_model_without_a_price_does_not_start(self):
        known = tt.get_tokenizer  # lend it a tokenizer, so only the price is missing
        with mock.patch.object(
            tt, "get_tokenizer", lambda name: known("Qwen/Qwen3-8B")
        ):
            message, printed = self.refused("confirm=true", "model_name=Qwen/Nope")
        self.assertIn("No price for Qwen/Nope", message)
        self.assertRegex(printed, r"dollars +unknown\n")
        self.assertFalse(self.runs.exists())

    def test_a_model_without_a_tokenizer_does_not_start(self):
        started = mock.AsyncMock()
        with mock.patch.object(train, "main", started), quiet() as out:
            with mock.patch.object(tt, "get_tokenizer", side_effect=OSError("offline")):
                with self.assertRaisesRegex(OSError, "offline"):
                    tt.main(self.args + ["confirm=true"])
        started.assert_not_awaited()
        self.assertIn("characters / 3.5", out.getvalue())
        self.assertFalse(self.runs.exists())

    def test_an_unknown_renderer_is_not_mistaken_for_a_missing_tokenizer(self):
        started = mock.AsyncMock()
        with mock.patch.object(train, "main", started), quiet():
            with self.assertRaisesRegex(ValueError, "Unknown renderer: bogus"):
                tt.main(self.args + ["confirm=true", "renderer_name=bogus"])
        started.assert_not_awaited()
        self.assertFalse(self.runs.exists())

    def test_bad_settings_stop_before_the_estimate(self):
        for setting, said in (
            ("groups_per_batch=0", "groups_per_batch must be at least 1"),
            ("groups_per_batch=-8", "groups_per_batch must be at least 1"),
            ("group_size=1", "group_size must be at least 2"),
            ("group_size=0", "group_size must be at least 2"),
            ("epochs=-1", "epochs must be at least 1"),
            ("max_tokens=0", "max_tokens must be at least 1"),
            ("max_steps=-5", "max_steps must be at least 1"),
            ("max_steps=0", "max_steps must be at least 1"),
            ("eval_every=-1", "eval_every must be at least 0"),
            ("save_every=-1", "save_every must be at least 0"),
            ("replay_matches=-1", "replay_matches must be at least 0"),
            ("eval_max_tasks=-1", "eval_max_tasks must be at least 0"),
            ("eval_split=nope", "No nope tasks"),
            ("train_split=nope", "No nope tasks"),
            (f"tasks_path={self.root / 'missing.jsonl.gz'}", "No tasks file"),
        ):
            for mode in ("confirm=true", "dry_run=true"):
                with self.subTest(setting=setting, mode=mode):
                    message, printed = self.refused(setting, mode)
                    self.assertIn(said, message)
                    self.assertEqual(printed, "")
        self.assertFalse(self.runs.exists())

    def test_trajectory_training_rejects_non_pnl_shaping(self):
        for option in ("w_log=0.2", "w_missed=1"):
            message, printed = self.refused(option, "confirm=true")
            self.assertIn("optimize P&L directly", message)
            self.assertEqual(printed, "")

    def test_broken_reward_chain_fails_before_client(self):
        changed = copy.deepcopy(self.tasks)
        changed[0]["scoring"]["chunk_end"] = "settlement"
        write_tasks(self.root / "tasks.jsonl.gz", changed)
        message, printed = self.refused("confirm=true")
        self.assertIn("Broken reward chain", message)
        self.assertEqual(printed, "")

    def test_bad_rl_loss_fails_before_client(self):
        message, printed = self.refused("loss_fn=cross_entropy", "confirm=true")
        self.assertIn("loss_fn must be", message)
        self.assertEqual(printed, "")

    def test_match_cannot_cross_split_boundaries(self):
        changed = [dict(t) for t in self.tasks]
        changed[0]["split"] = "val"
        write_tasks(self.root / "tasks.jsonl.gz", changed)
        message, printed = self.refused("confirm=true")
        self.assertIn("multiple splits", message)
        self.assertEqual(printed, "")

    def test_incomplete_replay_and_duplicate_tasks_fail_early(self):
        for tasks, expected in (
            (self.tasks[1:], "Incomplete position"),
            (self.tasks + self.tasks[:1], "Duplicate task"),
        ):
            write_tasks(self.root / "tasks.jsonl.gz", tasks)
            message, printed = self.refused("confirm=true")
            self.assertIn(expected, message)
            self.assertEqual(printed, "")

    def test_estimate_respects_small_completion_cap_and_disabled_evaluators(self):
        cli = tt.chz.entrypoint(
            tt.CLI,
            argv=self.args + ["max_tokens=16", "eval_max_tasks=0", "replay_matches=0"],
        )
        figures = tt.estimate(cli)
        self.assertEqual(figures["completion_tokens"], figures["completion_tokens_max"])
        self.assertEqual(figures["evals"], 0)

    def test_missing_credentials_fail_before_client(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            message, _ = self.refused("confirm=true")
        self.assertIn("TINKER_API_KEY", message)

    def test_task_hash_must_match_expected_config(self):
        changed = [
            dict(t, scoring={**t["scoring"], "config_hash": "wrong"})
            for t in self.tasks
        ]
        write_tasks(self.root / "tasks.jsonl.gz", changed)
        message, printed = self.refused("confirm=true")
        self.assertIn("another config", message)
        self.assertEqual(printed, "")

    def test_nondefault_build_config_is_explicit(self):
        config = make_config({"half_spread": 0.03})
        tasks, _ = build_tasks(load_records(FIXTURES), config)
        write_tasks(self.root / "tasks.jsonl.gz", tasks)
        self.assertIn("another config", self.refused()[0])
        path = self.root / "tasks.meta.json"
        path.write_text(json.dumps({"config": config}))
        message, _ = self.refused(f"config_path={path}")
        self.assertIn("confirm=true", message)

    def test_finite_numeric_settings_are_checked(self):
        for value in (
            "learning_rate=nan",
            "learning_rate=-1",
            "reward_scale=0",
            "w_log=inf",
            "invalid_penalty=-1",
            "lora_rank=0",
            "eval_split=train",
        ):
            message, printed = self.refused(value)
            self.assertTrue(message)
            self.assertEqual(printed, "")

    def test_training_without_evaluation_needs_no_eval_split(self):
        message, printed = self.refused("eval_split=nope", "eval_every=0")
        self.assertIn("confirm=true", message)
        self.assertRegex(printed, r"eval_rollouts +0\n")

    def test_dry_run_trains_two_steps_against_the_fake_service(self):
        real = tinker.ServiceClient
        with quiet() as out:
            tt.main(self.args + ["dry_run=true", "name=wiring"])
        self.assertIs(tinker.ServiceClient, real)
        self.assertIn("ESTIMATE ONLY", out.getvalue())
        run = self.runs / "dry-wiring"
        rows = lines(run / "metrics.jsonl")
        self.assertEqual([row["step"] for row in rows], [0, 1, 2])
        for row in rows[:2]:
            for key in ("pnl", "valid", "noop", "truncated", "reward/total"):
                self.assertIn("env/all/" + key, row)
            self.assertEqual(row["env/all/total_episodes"], 16)
            self.assertEqual(row["optim/lr"], 1e-5)
        # the untrained model before step 0, and the final checkpoint after step 1
        for row in (rows[0], rows[2]):
            self.assertEqual(row["val/env/all/total_episodes"], 8)
            self.assertIn("val/env/all/pnl", row)
            self.assertIn("replay/val/pnl_per_match_c", row)
            self.assertIn("replay/val/valid_rate", row)
        self.assertNotIn("env/all/pnl", rows[2])
        for row in rows:
            self.assertEqual([key for key in row if key.startswith("test/")], [])
        saved = lines(run / "checkpoints.jsonl")
        self.assertEqual([(row["name"], row["batch"]) for row in saved], [("final", 2)])
        logged = lines(run / "iteration_000000" / "train_rollout_summaries.jsonl")
        self.assertEqual(len(logged), 16)
        self.assertEqual(
            set(logged[0]["steps"][0]["logs"]), {"task_id", "action", "completion"}
        )
        fake = FakeTinker.last
        self.assertEqual(len(fake.batches), 2)
        self.assertEqual(len(fake.optim_steps), 2)
        self.assertEqual(fake.optim_steps[0].learning_rate, 1e-5)
        for batch in fake.batches:
            self.assertEqual(batch["loss_fn"], "importance_sampling")
            self.assertEqual(len(batch["data"]), 80)
            for datum in batch["data"]:
                self.assertEqual(set(datum.loss_fn_inputs), RL_KEYS)

    def test_train_metrics_cover_whole_trajectories(self):
        self.launch(
            "name=kept",
            "groups_per_batch=20",
            "max_steps=1",
            policy=StillWhenFlat(TOKENIZER),
        )
        row = lines(self.runs / "kept" / "metrics.jsonl")[0]
        self.assertEqual(row["env/all/total_episodes"], 80)
        self.assertEqual(row["env/all/total_turns"], 400)
        self.assertEqual(row["env/all/pnl_per_match"], 0.0)
        self.assertEqual(row["env/all/reward/total"], 0.0)
        self.assertEqual(row["env/all/by_group/frac_mixed"], 0.0)

    def test_a_rerun_resumes_and_says_so(self):
        fake, printed = self.launch("max_steps=2", "save_every=1", "eval_every=0")
        self.assertIsNone(fake.resumed)
        self.assertIn("New run: ", printed)
        self.assertNotIn("Resuming from step", printed)
        (name,) = self.run_dirs()
        metrics = self.runs / name / "metrics.jsonl"
        self.assertEqual([row["step"] for row in lines(metrics)], [0, 1])
        fake, printed = self.launch("max_steps=4", "save_every=1", "eval_every=0")
        self.assertEqual(fake.resumed, "tinker://dry-run/weights/final")
        self.assertIn(f"Resuming from step 2: {self.runs / name}", printed)
        self.assertRegex(printed, r"steps +2 \(2 to 4\)\n")
        self.assertRegex(printed, r"train_rollouts +160\n")
        self.assertEqual(self.run_dirs(), [name])
        self.assertEqual([row["step"] for row in lines(metrics)], [0, 1, 2, 3])
        self.assertEqual(len(fake.batches), 2)

    def test_a_finished_run_is_not_evaluated_twice(self):
        for _ in range(2):
            fake, _ = self.launch("max_steps=2", "name=once")
        rows = lines(self.runs / "once" / "metrics.jsonl")
        self.assertEqual([row["step"] for row in rows], [0, 1, 2])
        self.assertIn("replay/val/valid_rate", rows[2])
        self.assertEqual(fake.batches, [])

    def test_final_evaluation_is_exported_and_repeated_after_more_training(self):
        self.launch("max_steps=1", "name=extended")
        self.launch("max_steps=2", "name=extended")
        path = self.runs / "extended"
        rows = lines(path / "metrics.jsonl")
        self.assertEqual([r["step"] for r in rows if "final_evaluation" in r], [1, 2])
        self.assertTrue(
            list((path / "iteration_000002").glob("eval_*rollout_summaries.jsonl"))
        )

    def test_same_step_without_final_marker_does_not_skip_final_eval(self):
        self.launch("max_steps=1", "name=incomplete")
        path = self.runs / "incomplete" / "metrics.jsonl"
        rows = lines(path)
        rows[-1] = {"step": 1, "unrelated_metric": 42}
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))
        self.launch("max_steps=1", "name=incomplete")
        self.assertIn("final_evaluation", lines(path)[-1])

    def test_rebuilt_tasks_start_a_new_run(self):
        self.launch("max_steps=2")
        (first,) = self.run_dirs()
        write_tasks(self.root / "tasks.jsonl.gz", self.tasks[4:])
        fake, printed = self.launch("max_steps=2")
        self.assertIsNone(fake.resumed)
        self.assertIn("New run: ", printed)
        self.assertEqual(len(self.run_dirs()), 2)
        self.assertIn(first, self.run_dirs())

    def test_a_named_run_is_not_resumed_under_other_settings(self):
        self.launch("name=mine", "max_steps=2")
        before = (self.runs / "mine" / "metrics.jsonl").read_text()
        message, printed = self.refused(
            "name=mine", "confirm=true", "seed=7", "reward_scale=2.0"
        )
        self.assertIn(str(self.runs / "mine"), message)
        self.assertIn("another reward_scale, seed.", message)
        self.assertEqual(printed, "")
        write_tasks(self.root / "tasks.jsonl.gz", self.tasks[4:])
        message, _ = self.refused("name=mine", "confirm=true")
        self.assertIn("another tasks_sha256.", message)
        self.assertEqual((self.runs / "mine" / "metrics.jsonl").read_text(), before)
        write_tasks(self.root / "tasks.jsonl.gz", self.tasks)
        fake, printed = self.launch("name=mine", "max_steps=3")
        self.assertIn("Resuming from step", printed)
        self.assertEqual(len(fake.batches), 1)

    def test_dry_run_refuses_symlink_to_real_run(self):
        real = self.root / "real"
        real.mkdir()
        sentinel = real / "keep.txt"
        sentinel.write_text("real run")
        self.runs.mkdir()
        (self.runs / "dry-link").symlink_to(real, target_is_directory=True)
        message, _ = self.refused("dry_run=true", "name=link")
        self.assertIn("unsafe dry-run directory", message)
        self.assertEqual(sentinel.read_text(), "real run")

    def test_a_dry_run_can_only_wipe_its_own_directory(self):
        cli = tt.chz.entrypoint(tt.CLI, argv=self.args + ["name=mine"])
        dry = tt.chz.replace(cli, dry_run=True)
        self.assertEqual(tt.run_name(cli), "mine")
        self.assertEqual(tt.run_name(dry), "dry-mine")
        self.assertTrue(tt.run_name(tt.chz.replace(dry, name=None)).startswith("dry-"))
        self.launch("name=real", "max_steps=1", "eval_every=0")
        kept = sorted(path.name for path in (self.runs / "real").iterdir())
        self.assertIn("checkpoints.jsonl", kept)
        for name in ("x/..", "..", ".", "", "../real", "a/b", "dry-real", "dry-"):
            for mode in ("dry_run=true", "confirm=true"):
                with self.subTest(name=name, mode=mode):
                    message, printed = self.refused("name=" + name, mode)
                    self.assertIn("plain directory name", message)
                    self.assertEqual(printed, "")
        with quiet():
            tt.main(self.args + ["dry_run=true", "name=real"])
        self.assertEqual(self.run_dirs(), ["dry-real", "real"])
        self.assertEqual(sorted(p.name for p in (self.runs / "real").iterdir()), kept)


if __name__ == "__main__":
    unittest.main()
