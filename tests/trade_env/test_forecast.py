"""Offline forecast reward, timing, masking and prompt-boundary checks."""

import copy
import json
from pathlib import Path
import unittest

from app.trade_env.config import make_config, config_hash
from app.trade_env.forecast import targets, predictions, validate_targets, brier
from app.trade_env.tasks import load_records, build_tasks
from app.trade_env.reward import score


class ForecastTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = make_config({"forecast_rewards": True})
        cls.records = load_records(Path(__file__).parent / "fixtures/records.jsonl.gz")
        cls.tasks, _ = build_tasks(cls.records, cls.config)

    def task(self):
        return copy.deepcopy(
            next(
                t
                for t in self.tasks
                if t["position"] == "flat"
                and t["scoring"]["forecast"]["prices"] is not None
                and t["scoring"]["forecast"]["next_goal"] is not None
            )
        )

    def answer(self, task):
        labels = task["scoring"]["forecast"]
        return {
            "probs": {
                o: float(o == task["scoring"]["result"])
                for o in ("home", "draw", "away")
            },
            "next_goal": {
                o: float(o == labels["next_goal"]) for o in ("home", "away", "none")
            },
            "future_prices": labels["prices"],
            "action": "do_nothing",
            "reason": "test",
        }

    def test_rules_toggle_changes_only_system_prompt_and_fingerprint(self):
        from app.trade_env.parse import objects
        from app.trade_env.prompt import system_prompt

        config = make_config({"forecast_rewards": True, "football_rules": True})
        rules_tasks, _ = build_tasks(self.records, config)
        self.assertNotEqual(config_hash(config), config_hash(self.config))
        self.assertEqual(len(rules_tasks), len(self.tasks))
        for before, after in zip(self.tasks, rules_tasks):
            self.assertEqual(before["id"], after["id"])
            self.assertEqual(before["messages"][1], after["messages"][1])
            self.assertEqual(
                {k: v for k, v in before["scoring"].items() if k != "config_hash"},
                {k: v for k, v in after["scoring"].items() if k != "config_hash"},
            )
        self.assertNotIn("Football rules reference", system_prompt(self.config))
        prompt = system_prompt(config)
        self.assertIn("Football rules reference", prompt)
        self.assertIn("Two yellows to different players", prompt)
        self.assertEqual(len(objects(prompt)), 1)

    def test_prompt_has_one_complete_parseable_example(self):
        from app.trade_env.parse import objects
        from app.trade_env.prompt import system_prompt

        prompt = system_prompt(self.config)
        examples = objects(prompt)
        self.assertEqual(len(examples), 1)
        example = examples[0][2]
        self.assertEqual(
            set(example), {"probs", "next_goal", "future_prices", "action", "reason"}
        )
        t = self.task()
        example["action"] = "do_nothing"
        out = score(t, json.dumps(example), self.config)
        self.assertTrue(out["valid"])
        self.assertIn("Do not output a second JSON object", prompt)

    def test_observed_split_object_failure_remains_invalid(self):
        t = self.task()
        answer = self.answer(t)
        trade = {k: answer[k] for k in ("probs", "action", "reason")}
        forecasts = {k: answer[k] for k in ("next_goal", "future_prices")}
        for first, second in ((trade, forecasts), (forecasts, trade)):
            out = score(t, json.dumps(first) + "\n" + json.dumps(second), self.config)
            self.assertFalse(out["valid"])
            self.assertEqual(out["action"], "do_nothing")

    def test_labels_validate_and_old_hash_is_preserved(self):
        validate_targets(self.tasks)
        self.assertEqual(config_hash(make_config()), "411efa26fd8f")
        self.assertNotEqual(config_hash(self.config), config_hash(make_config()))

    def test_truthful_forecasts_win_without_changing_pnl(self):
        t = self.task()
        a = self.answer(t)
        good = score(t, json.dumps(a), self.config)
        a["probs"] = {"home": 1 / 3, "draw": 1 / 3, "away": 1 / 3}
        a["next_goal"] = {"home": 1 / 3, "away": 1 / 3, "none": 1 / 3}
        a["future_prices"] = t["scoring"]["shown_prices"]
        bad = score(t, json.dumps(a), self.config)
        self.assertGreater(good["reward"], bad["reward"])
        self.assertEqual(good["pnl"], 0)
        self.assertEqual(bad["pnl"], 0)
        self.assertEqual(good["forecast"]["result_brier"], 0)
        self.assertEqual(good["forecast"]["goal_brier"], 0)
        self.assertEqual(good["forecast"]["price_mse"], 0)
        self.assertAlmostEqual(bad["forecast"]["price_reward"], 0)

    def test_missing_forecasts_are_invalid_and_hold(self):
        t = self.task()
        a = self.answer(t)
        del a["next_goal"]
        a["action"] = "buy_home"
        out = score(t, json.dumps(a), self.config)
        self.assertFalse(out["valid"])
        self.assertEqual(out["action"], "do_nothing")
        self.assertLess(out["reward"], 0)

    def test_malformed_forecasts_rejected(self):
        t = self.task()
        for key, value in [
            ("next_goal", {"home": 0, "away": 0, "none": 0}),
            ("future_prices", {"home": float("nan"), "draw": 0.2, "away": 0.3}),
            ("future_prices", {"home": True, "draw": 0.2, "away": 0.3}),
        ]:
            a = self.answer(t)
            a[key] = value
            self.assertIsNone(predictions(json.dumps(a), 0.05))

    def test_masks_and_match_normalization(self):
        t = self.task()
        a = self.answer(t)
        out = score(t, json.dumps(a), self.config)
        t["scoring"]["forecast_points"] *= 2
        half = score(t, json.dumps(a), self.config)
        self.assertAlmostEqual(half["reward"], out["reward"] / 2)
        t["scoring"]["forecast"] = {"next_goal": None, "prices": None}
        masked = score(t, json.dumps(a), self.config)["forecast"]
        self.assertEqual(masked["goal_reward"], 0)
        self.assertEqual(masked["price_reward"], 0)
        self.assertEqual(masked["goal_available"], 0)
        self.assertEqual(masked["price_available"], 0)

    def test_goal_boundary_own_goal_and_censoring(self):
        record = {
            "periods": {"h2_end": 1000},
            "events": [
                {"t": 10, "home_goals": 1, "away_goals": 0},
                {"t": 11, "home_goals": 1, "away_goals": 1, "team": "home"},
            ],
            "prices": {o: [[10, 0.3], [310, 0.4]] for o in ("home", "draw", "away")},
        }
        label = targets(record, 10, {}, self.config)
        self.assertEqual(label["next_goal"], "away")
        self.assertEqual(label["prices"], dict.fromkeys(("home", "draw", "away"), 0.4))
        self.assertEqual(
            targets(record, 800, {}, self.config), {"next_goal": None, "prices": None}
        )
        record["events"][1]["home_goals"] = 2
        self.assertIsNone(targets(record, 10, {}, self.config)["next_goal"])

    def test_future_label_mutations_do_not_change_prompt_or_task_selection(self):
        record = copy.deepcopy(self.records[0])
        before, _ = build_tasks([record], self.config)
        t = min(x["t"] for x in before)
        for e in record["events"]:
            if e["t"] > t:
                e.update(home_goals=50, away_goals=40)
        after, _ = build_tasks([record], self.config)
        self.assertEqual([x["id"] for x in before], [x["id"] for x in after])
        self.assertEqual(
            [x["messages"] for x in before if x["t"] == t],
            [x["messages"] for x in after if x["t"] == t],
        )

    def test_position_variants_must_share_labels(self):
        tasks = copy.deepcopy(self.tasks)
        tasks[0]["scoring"]["forecast"]["next_goal"] = (
            "none" if tasks[0]["scoring"]["forecast"]["next_goal"] != "none" else "home"
        )
        with self.assertRaisesRegex(ValueError, "position-dependent"):
            validate_targets(tasks)

    def test_brier_is_proper_in_expectation(self):
        truth = {"home": 0.6, "away": 0.1, "none": 0.3}
        other = {"home": 0.8, "away": 0.1, "none": 0.1}
        self.assertLess(
            sum(p * brier(truth, k) for k, p in truth.items()),
            sum(p * brier(other, k) for k, p in truth.items()),
        )


class ForecastTrainerTests(unittest.TestCase):
    def test_trajectory_total_and_hidden_targets(self):
        import asyncio
        from app.trade_env.train_tinker import MatchEnv, TradeDatasetBuilder
        from app.trade_env.replay import by_match

        config = make_config({"forecast_rewards": True})
        records = load_records(Path(__file__).parent / "fixtures/records.jsonl.gz")
        tasks, _ = build_tasks(records, config)
        match = next(iter(by_match(tasks).values()))
        renderer = TradeDatasetBuilder().renderer()
        env = MatchEnv(match, renderer, config, 1.0)
        first = asyncio.run(env.initial_observation())[0].to_ints()
        mutated = copy.deepcopy(match)
        for t in mutated:
            t["scoring"]["forecast"] = {
                "next_goal": "FUTURE_SENTINEL",
                "prices": "FUTURE_SENTINEL",
            }
        hidden = MatchEnv(mutated, renderer, config, 1.0)
        self.assertEqual(first, asyncio.run(hidden.initial_observation())[0].to_ints())
        total = expected = pnl = 0.0
        for i in range(len(env.times)):
            answer = json.dumps(
                {
                    "probs": {"home": 0.3, "draw": 0.4, "away": 0.3},
                    "next_goal": {"home": 0.2, "away": 0.2, "none": 0.6},
                    "future_prices": {"home": 0.3, "draw": 0.4, "away": 0.3},
                    "action": "do_nothing",
                }
            )
            tokens = renderer.tokenizer.encode(answer, add_special_tokens=False) + [
                renderer.get_stop_sequences()[0]
            ]
            result = asyncio.run(env.step(tokens))
            total += result.reward
            pnl += result.metrics["pnl"]
            expected += sum(
                result.metrics["forecast/" + k]
                for k in ("result_reward", "goal_reward", "price_reward")
            )
            self.assertEqual(result.episode_done, i + 1 == len(env.times))
        self.assertEqual(pnl, 0)
        self.assertAlmostEqual(total, expected)


if __name__ == "__main__":
    unittest.main()
