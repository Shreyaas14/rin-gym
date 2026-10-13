import contextlib
import copy
import io
import json
import math
from pathlib import Path
import random
import re
import tempfile
import unittest

from app.trade_env.__main__ import main, meta_path
from app.trade_env.baselines import POLICIES, run_baselines, run_policy, sensitivity
from app.trade_env.clock import decision_points
from app.trade_env.config import (
    OUTCOMES,
    POSITIONS,
    config_hash,
    make_config,
    parse_overrides,
)
from app.trade_env.costs import buy_cost, fee, sell_proceeds
from app.trade_env.parse import parse
from app.trade_env.prompt import render
from app.trade_env.replay import by_match, replay_match
from app.trade_env.reward import legal_actions, score
from app.trade_env.splits import is_demo, split_for
from app.trade_env.state import build_state, counts, shown_prices
from app.trade_env.stats import bootstrap_ci, single_step, step_summary
from app.trade_env.tasks import build_tasks, load_records, match_tasks, write_tasks

FIXTURES = Path(__file__).parent / "fixtures" / "records.jsonl.gz"
OWN_GOAL = Path(__file__).parent / "fixtures" / "own-goal.jsonl.gz"
GROUP, GROUP_2, FINAL, EPL = (
    "400021440",
    "400021504",
    "400021543",
    "zhs8gg1hvcuqvhkk2itb54pg",
)
RECORDS = {record["match_id"]: record for record in load_records(FIXTURES)}
FLAT = ["buy_home", "buy_draw", "buy_away", "do_nothing"]
SCORING_KEYS = [
    "scoring",
    "action_rewards",
    "next_position",
    "market_probs",
    "shown_prices",
    "fill_prices",
    "end_marks",
    "final_chunk",
    "config_hash",
    "action_costs",
    "chunk_end",
    "observed_score",
    "extra_time",
]


def record(match_id=GROUP):
    return copy.deepcopy(RECORDS[match_id])


def text(task):
    return "\n".join(message["content"] for message in task["messages"])


def answer(action, probs=(0.5, 0.3, 0.2)):
    return json.dumps(
        {"probs": dict(zip(OUTCOMES, probs)), "action": action, "reason": "test"}
    )


def decision_times(rec):
    p = rec["periods"]
    return {
        "h1+15": p["h1_start"] + 900,
        "h1+30": p["h1_start"] + 1800,
        "ht": p["h1_end"],
        "h2+15": p["h2_start"] + 900,
        "h2+30": p["h2_start"] + 1800,
    }


def cash_flow(rec, trades):
    """P&L from cash alone, written out without the env: pay to buy and receive
    to sell at the first price 60 s after each decision, then settle at 1 or 0.
    The quote is the mid moved by the spread, held between 0.001 and 0.999 and
    never through the mid; cash per share stays between 0 and 1."""

    def fill(outcome, t):
        return next(p for at, p in rec["prices"][outcome] if at >= t + 60)

    times, cash, held = decision_times(rec), 0.0, None
    for decision, action in trades:
        t = times[decision]
        if action == "close" or action.startswith("switch_"):
            p = fill(held, t)
            cash += max(0.0, min(p, max(0.001, p - 0.01)) - 0.05 * p * (1 - p))
            held = None
        if action.startswith(("buy_", "switch_")):
            held = action.split("_")[1]
            p = fill(held, t)
            cash -= min(1.0, max(p, min(0.999, p + 0.01)) + 0.05 * p * (1 - p))
    if held == rec["regulation"]["result"]:
        cash += 1.0
    return cash


def random_replay(tasks, seed, config, invalid_share=0.0):
    rng = random.Random(seed)

    def policy(task):
        if rng.random() < invalid_share:
            return "I am not sure."
        return rng.choice(task["legal_actions"])

    return replay_match(tasks, policy, config)


def random_means(rows):
    return [row["mean_c"] for row in rows if row["policy"] == "random_legal"]


def played(result):
    return [(s["decision"], s["action"]) for s in result["steps"]]


def quote(rec, t, values, before=120, after=0):
    """Set the samples from `before` seconds ahead of t to `after` seconds past it."""
    for outcome, value in zip(OUTCOMES, values):
        for sample in rec["prices"][outcome]:
            if value is not None and t - before < sample[0] <= t + after:
                sample[1] = value
    return rec


def task_of(rec, name, config=None):
    tasks, _ = match_tasks(rec, config or make_config())
    return next(t for t in tasks if t["id"] == f"{rec['match_id']}:{name}")


def price_rows(task):
    """Label and cells of each row of the price table, in the order printed."""
    lines = task["messages"][1]["content"].splitlines()
    start = lines.index("Prices ($ per share)") + 2
    end = next(i for i, line in enumerate(lines) if line.startswith("Buying now"))
    return [(line[:17].strip(), line[17:].split()) for line in lines[start:end]]


class CostTests(unittest.TestCase):
    def test_fee_spread_and_clamps(self):
        c = make_config()
        self.assertAlmostEqual(fee(0.5, c), 0.0125)
        self.assertAlmostEqual(buy_cost(0.5, c), 0.5225)
        self.assertAlmostEqual(sell_proceeds(0.5, c), 0.4775)
        self.assertAlmostEqual(buy_cost(0.2, c), 0.2 + 0.01 + 0.05 * 0.2 * 0.8)
        self.assertAlmostEqual(buy_cost(0.995, c), 0.999 + fee(0.995, c))
        self.assertAlmostEqual(sell_proceeds(0.005, c), 0.001 - fee(0.005, c))
        # past 0.999 and 0.001 the quote is the mid itself, not a better price
        self.assertAlmostEqual(buy_cost(0.9995, c), 0.9995 + fee(0.9995, c))
        self.assertAlmostEqual(sell_proceeds(0.0005, c), 0.0005 - fee(0.0005, c))
        for p in (0.0005, 0.03, 0.5, 0.97, 0.9995):
            self.assertGreater(buy_cost(p, c), sell_proceeds(p, c))
            self.assertGreater(sell_proceeds(p, c), 0)

    def test_a_trade_never_pays_and_cash_stays_between_0_and_1(self):
        prices = [0.0, 0.0005, 0.001, 0.005, 0.03, 0.05, 0.5, 0.95, 0.97, 0.995]
        prices += [0.999, 0.9995, 1.0]
        for spread in (0.0, 0.01, 0.03, 0.05):
            c = make_config({"half_spread": spread})
            for p in prices:
                self.assertGreaterEqual(buy_cost(p, c), p, (spread, p))
                self.assertLessEqual(sell_proceeds(p, c), p, (spread, p))
                self.assertLessEqual(buy_cost(p, c), 1.0, (spread, p))
                self.assertGreaterEqual(sell_proceeds(p, c), 0.0, (spread, p))

    def test_no_action_has_a_negative_cost_at_tail_prices(self):
        rec = record(GROUP)
        t = decision_times(rec)["h2+30"]
        quote(rec, t, (0.9995, 0.0005, 0.0005), after=600)
        tasks, drops = match_tasks(rec, make_config())
        self.assertEqual(drops, [])
        last = [task for task in tasks if task["decision"] == "h2+30"]
        self.assertEqual(
            last[0]["scoring"]["fill_prices"],
            {"home": 0.9995, "draw": 0.0005, "away": 0.0005},
        )
        for task in last:
            scoring = task["scoring"]
            for action, cost in scoring["action_costs"].items():
                self.assertGreaterEqual(cost, 0.0, (task["id"], action))
            if task["position"] != "flat":
                # selling at an unchanged price cannot beat holding still
                self.assertLess(scoring["action_rewards"]["close"], 0.0)

    def test_costs_follow_config(self):
        c = make_config({"half_spread": 0.03, "fee_rate": 0.0})
        self.assertAlmostEqual(buy_cost(0.4, c), 0.43)
        self.assertAlmostEqual(sell_proceeds(0.4, c), 0.37)


class ConfigTests(unittest.TestCase):
    def test_unknown_key_and_bad_choice_rejected(self):
        with self.assertRaises(ValueError):
            make_config({"half_spred": 0.01})
        with self.assertRaises(ValueError):
            make_config({"mark": "bid"})

    def test_override_must_have_the_type_of_its_default(self):
        bad = [
            ("show_kickoff_price", "False"),
            ("show_kickoff_price", 0),
            ("half_spread", ".03"),
            ("half_spread", -0.01),
            ("half_spread", True),
            ("fill_delay_s", "abc"),
            ("max_sum_dev", float("nan")),
            ("fee_rate", float("inf")),
            ("mark", 3),
            ("decisions", "ht"),
            ("decisions", [1, 2]),
            ("competitions", ["17"]),
            ("competitions", {"17": 2026}),
        ]
        for key, value in bad:
            with self.assertRaises(ValueError, msg=(key, value)) as raised:
                make_config({key: value})
            self.assertIn(key + " must be", str(raised.exception))
        good = make_config(
            {"show_kickoff_price": False, "fill_delay_s": 0, "half_spread": 0.03}
        )
        self.assertIs(good["show_kickoff_price"], False)
        self.assertEqual(make_config({"fill_delay_s": 90.5})["fill_delay_s"], 90.5)

    def test_overrides_from_the_command_line(self):
        parsed = parse_overrides(
            [
                "show_kickoff_price=False",
                "w_log=0.5",
                "mark=liquidation",
                'decisions=["ht"]',
                "half_spread=.03",
                "world_cup_competition=17",
            ]
        )
        self.assertEqual(
            parsed,
            {
                "show_kickoff_price": False,
                "w_log": 0.5,
                "mark": "liquidation",
                "decisions": ["ht"],
                "half_spread": ".03",
                "world_cup_competition": "17",
            },
        )
        self.assertIs(
            parse_overrides(["show_kickoff_price=TRUE"])["show_kickoff_price"], True
        )
        with self.assertRaises(ValueError):
            make_config(parsed)
        with self.assertRaises(ValueError):
            parse_overrides(["half_spread"])

    def test_hash_tracks_the_rules_baked_into_tasks(self):
        base = config_hash(make_config())
        self.assertEqual(base, config_hash(make_config()))
        for key, value in (
            ("fill_delay_s", 0),
            ("half_spread", 0.03),
            ("mark", "liquidation"),
            ("decisions", ["ht"]),
            ("split_shot_competitions", []),
        ):
            self.assertNotEqual(base, config_hash(make_config({key: value})))
        for key, value in (("w_log", 0.5), ("invalid_penalty", 0.1), ("seed", 1)):
            self.assertEqual(base, config_hash(make_config({key: value})))


class ClockTests(unittest.TestCase):
    def test_default_points(self):
        periods = {"h1_start": 1000.5, "h1_end": 4000, "h2_start": 5000, "h2_end": 8100}
        points = decision_points(periods, make_config())
        self.assertEqual(
            [(p["name"], p["t"]) for p in points],
            [
                ("h1+15", 1900.5),
                ("h1+30", 2800.5),
                ("ht", 4000),
                ("h2+15", 5900),
                ("h2+30", 6800),
            ],
        )
        self.assertEqual(points[0]["phase"], "first half, 15:00 played")
        self.assertEqual(points[2]["phase"], "halftime")
        self.assertEqual([p["idx"] for p in points], [0, 1, 2, 3, 4])

    def test_long_halftime_moves_only_second_half_points(self):
        rec = record(GROUP)
        before = decision_points(rec["periods"], make_config())
        delayed = long_halftime(rec, 7000)
        after = decision_points(delayed["periods"], make_config())
        self.assertEqual([p["t"] for p in after[:3]], [p["t"] for p in before[:3]])
        self.assertEqual(after[2]["t"], delayed["periods"]["h1_end"])
        self.assertEqual(after[3]["t"], delayed["periods"]["h2_start"] + 900)
        self.assertEqual(after[3]["t"] - before[3]["t"], 7000)
        tasks, drops = match_tasks(delayed, make_config())
        self.assertEqual((len(tasks), drops), (20, []))
        late = next(t for t in tasks if t["id"] == GROUP + ":h2+15:flat")
        self.assertIn("Clock: second half, 15:00 played", text(late))
        self.assertEqual(late["t"], delayed["periods"]["h2_start"] + 900)
        half = next(t for t in tasks if t["id"] == GROUP + ":ht:flat")
        self.assertEqual(half["scoring"]["chunk_end"], "h2+15")
        for seed in range(10):
            result = random_replay(tasks, seed, make_config())
            self.assertAlmostEqual(
                result["pnl"], cash_flow(delayed, played(result)), places=9
            )

    def test_point_at_or_after_period_end_is_dropped(self):
        periods = {"h1_start": 0, "h1_end": 1800, "h2_start": 2700, "h2_end": 5500}
        names = [p["name"] for p in decision_points(periods, make_config())]
        self.assertEqual(names, ["h1+15", "ht", "h2+15", "h2+30"])
        rec = record(GROUP)
        rec["periods"]["h1_end"] = rec["periods"]["h1_start"] + 1800
        _, drops = match_tasks(rec, make_config())
        self.assertIn(("h1+30", "after_period_end"), drops)

    def test_optional_pre_match_point(self):
        periods = {"h1_start": 1000, "h1_end": 4000, "h2_start": 5000, "h2_end": 8100}
        config = make_config({"decisions": ["pre", "h1+15", "ht"]})
        points = decision_points(periods, config)
        self.assertEqual([p["t"] for p in points], [940, 1900, 4000])
        self.assertIsNone(points[0]["kickoff_t"])
        self.assertEqual(points[1]["kickoff_t"], 1000)
        tasks, _ = match_tasks(record(GROUP), config)
        pre = next(
            t for t in tasks if t["decision"] == "pre" and t["position"] == "flat"
        )
        self.assertIn("Clock: before kickoff", text(pre))
        self.assertNotIn("kickoff ", pre["messages"][1]["content"])
        self.assertEqual([label for label, _ in price_rows(pre)], ["now"])
        # later prompts list the rows oldest first: pre-match is before kickoff
        half = next(
            t for t in tasks if t["decision"] == "ht" and t["position"] == "flat"
        )
        self.assertEqual(
            [label for label, _ in price_rows(half)],
            ["pre-match", "kickoff", "first half 15:00", "now"],
        )

    def test_bad_names_and_order_rejected(self):
        periods = {"h1_start": 0, "h1_end": 3000, "h2_start": 4000, "h2_end": 7000}
        with self.assertRaises(ValueError):
            decision_points(periods, make_config({"decisions": ["h3+5"]}))
        with self.assertRaises(ValueError):
            decision_points(periods, make_config({"decisions": ["ht", "h1+15"]}))


def long_halftime(rec, seconds):
    """Push the second half back, as a weather delay does; quotes keep coming
    for five minutes after the first half ends and resume with the restart."""
    out = copy.deepcopy(rec)
    resume = out["periods"]["h1_end"] + 300
    start = out["periods"]["h2_start"]
    for key in ("h2_start", "h2_end"):
        out["periods"][key] += seconds
    for event in out["events"]:
        if event["t"] >= start:
            event["t"] += seconds
    for series in out["prices"].values():
        for sample in series:
            if sample[0] > resume:
                sample[0] += seconds
    return out


class LeakageTests(unittest.TestCase):
    def scrubbed(self, rec, t, config, lie):
        """The record with everything not yet knowable at t removed or falsified."""
        out = copy.deepcopy(rec)
        cutoff = t - config["event_delay_s"]
        events = [e for e in out["events"] if e["t"] <= cutoff]
        prices = {o: [s for s in out["prices"][o] if s[0] <= t] for o in OUTCOMES}
        if lie:
            for event in out["events"]:
                if event["t"] > cutoff:
                    events.append(
                        {
                            **event,
                            "kind": "goal",
                            "team": "away",
                            "minute": "LEAK",
                            "home_goals": 7,
                            "away_goals": 9,
                            "description": "LEAK",
                        }
                    )
            for outcome in OUTCOMES:
                prices[outcome] += [
                    [s[0], 0.987] for s in out["prices"][outcome] if s[0] > t
                ]
        out["events"], out["prices"] = events, prices
        out["regulation"] = {"home_goals": 7, "away_goals": 9, "result": "LEAK"}
        out["extra_time"] = "LEAK"
        out["periods"] = {
            key: value if value <= t else "LEAK"
            for key, value in out["periods"].items()
        }
        return out

    def test_future_events_prices_and_results_change_no_byte(self):
        config = make_config()
        checked = 0
        for rec in RECORDS.values():
            tasks, _ = match_tasks(rec, config)
            built = {task["id"]: task for task in tasks}
            points = decision_points(rec["periods"], config)
            for k, point in enumerate(points):
                for lie in (False, True):
                    other = self.scrubbed(rec, point["t"], config, lie)
                    shown, reason = shown_prices(other["prices"], point["t"], config)
                    self.assertIsNone(reason)
                    for position in POSITIONS:
                        state = build_state(other, points, k, position, shown, config)
                        task = built[f"{rec['match_id']}:{point['name']}:{position}"]
                        self.assertEqual(
                            json.dumps(render(state, config)).encode(),
                            json.dumps(task["messages"]).encode(),
                        )
                        self.assertNotIn("LEAK", text(task))
                        checked += 1
        self.assertEqual(checked, 4 * 5 * 2 * 4)

    def test_same_prompt_when_the_rest_of_the_match_differs(self):
        config = make_config()
        rec = record(GROUP)
        original = {task["id"]: task for task in match_tasks(rec, config)[0]}
        for point in decision_points(rec["periods"], config):
            other = copy.deepcopy(rec)
            # later prices move, but still sum to about 1 so no point is dropped
            for outcome, shift in (("home", -0.02), ("draw", 0.02)):
                for sample in other["prices"][outcome]:
                    if sample[0] > point["t"]:
                        sample[1] = round(min(1.0, max(0.0, sample[1] + shift)), 4)
            for event in other["events"]:
                if event["t"] > point["t"] - config["event_delay_s"]:
                    event["home_goals"] += 3
                    event["description"] = "LEAK"
            other["regulation"] = {"home_goals": 4, "away_goals": 1, "result": "home"}
            other["extra_time"] = True
            made, drops = match_tasks(other, config)
            self.assertEqual(drops, [])
            changed = {task["id"]: task for task in made}
            for position in POSITIONS:
                key = f"{GROUP}:{point['name']}:{position}"
                self.assertEqual(changed[key]["messages"], original[key]["messages"])
                self.assertNotEqual(changed[key]["scoring"], original[key]["scoring"])

    def test_events_inside_the_delay_are_not_shown(self):
        rec = record(EPL)
        t = decision_times(rec)["h1+15"]
        for offset, shown in ((-61, True), (-59, False), (30, False)):
            marked = copy.deepcopy(rec)
            marked["events"].append(
                {
                    **marked["events"][5],
                    "t": t + offset,
                    "seq": 9000,
                    "kind": "foul",
                    "description": "MARKER",
                }
            )
            tasks, _ = match_tasks(marked, make_config())
            task = next(x for x in tasks if x["id"] == EPL + ":h1+15:flat")
            self.assertEqual("MARKER" in text(task), shown)
        live = make_config({"event_delay_s": 0})
        marked["events"][-1]["t"] = t - 1
        task = next(
            x for x in match_tasks(marked, live)[0] if x["id"] == EPL + ":h1+15:flat"
        )
        self.assertIn("MARKER", text(task))

    def test_no_scoring_or_result_field_in_messages(self):
        tasks, _ = build_tasks(list(RECORDS.values()), make_config())
        for task in tasks:
            body = text(task)
            for key in SCORING_KEYS:
                self.assertNotIn(key, body)
            self.assertNotIn(task["scoring"]["config_hash"], body)
            self.assertEqual(
                sorted(task),
                sorted(
                    [
                        "id",
                        "match_id",
                        "competition",
                        "split",
                        "demo",
                        "decision",
                        "decision_idx",
                        "t",
                        "position",
                        "messages",
                        "legal_actions",
                        "scoring",
                    ]
                ),
            )
            self.assertEqual([m["role"] for m in task["messages"]], ["system", "user"])

    def test_extra_time_final_is_scored_on_regulation(self):
        rec = record(FINAL)
        self.assertTrue(rec["extra_time"])
        tasks, _ = match_tasks(rec, make_config())
        last = next(t for t in tasks if t["id"] == FINAL + ":h2+30:draw")
        self.assertTrue(last["scoring"]["final_chunk"])
        self.assertEqual(last["scoring"]["result"], "draw")
        self.assertEqual(
            last["scoring"]["end_marks"], {"home": 0.0, "draw": 1.0, "away": 0.0}
        )
        shown = last["scoring"]["shown_prices"]["draw"]
        self.assertAlmostEqual(last["scoring"]["action_rewards"]["keep"], 1 - shown)


class StateTests(unittest.TestCase):
    def own_goal_record(self):
        """A home player scores in his own net; the last row seen before the
        first decision is a save logged at the same instant with the old score."""
        rec = record(EPL)
        at = decision_times(rec)["h1+15"] - 60.5
        for event in rec["events"]:
            if event["t"] > at:
                event["away_goals"] += 1
        rec["regulation"]["away_goals"] += 1
        base = {
            **rec["events"][5],
            "t": at,
            "minute": "14'",
            "team": "home",
            "team_id": rec["home_team_id"],
            "home_goals": 0,
        }
        rec["events"] += [
            {
                **base,
                "seq": 9000,
                "kind": "own_goal",
                "type": "Own goal",
                "away_goals": 1,
                "description": "Virgil VAN DIJK (Liverpool) scores an own goal.",
            },
            # a later row at the same instant still carrying the old score
            {
                **base,
                "seq": 9001,
                "kind": "goal_prevention",
                "type": "Goal Prevention",
                "away_goals": 0,
                "description": "The goalkeeper of Liverpool pulls off a save.",
            },
        ]
        return rec

    def test_own_goal_credits_the_side_whose_score_rose(self):
        rec = self.own_goal_record()
        config = make_config()
        points = decision_points(rec["periods"], config)
        shown, _ = shown_prices(rec["prices"], points[0]["t"], config)
        state = build_state(rec, points, 0, "flat", shown, config)
        self.assertEqual(state["events"][-1]["kind"], "goal_prevention")
        self.assertEqual(state["score"], {"home": 0, "away": 1})
        self.assertEqual(state["totals"]["away"]["goals"], 1)
        self.assertEqual(state["totals"]["home"]["goals"], 0)
        row = next(r for r in state["events"] if r["kind"] == "own_goal")
        self.assertEqual(row["team"], "Liverpool")
        self.assertEqual(row["note"], "goal for Bournemouth, score 0-1")
        user = render(state, config)[1]["content"]
        self.assertIn("Score: Liverpool 0-1 Bournemouth", user)
        self.assertIn("-> goal for Bournemouth, score 0-1", user)
        self.assertEqual(sum(r["note"] is not None for r in state["events"]), 1)

    def test_real_own_goal_followed_by_a_stale_row(self):
        # Morocco v Haiti: Bounou's own goal makes it 0-1 after 10 minutes, and
        # the next row, a save at the same instant, still says 0-0
        rec = load_records(OWN_GOAL)[0]
        rows = [e for e in rec["events"] if e["minute"] == "10'"]
        self.assertEqual(
            [(e["kind"], e["team"], e["home_goals"], e["away_goals"]) for e in rows],
            [
                ("shot_attempt", "away", 0, 0),
                ("own_goal", "home", 0, 1),
                ("goal_prevention", "home", 0, 0),
            ],
        )
        tasks, drops = match_tasks(rec, make_config())
        self.assertEqual((len(tasks), drops), (20, []))
        first = next(t for t in tasks if t["id"] == "400021452:h1+15:flat")
        self.assertEqual(first["scoring"]["observed_score"], {"home": 0, "away": 1})
        user = first["messages"][1]["content"]
        self.assertIn("Score: Morocco 0-1 Haiti", user)
        self.assertIn("Haiti: goals 1 (1)", user)
        self.assertIn("Morocco: goals 0 (0)", user)
        self.assertIn(
            "10' own_goal [Morocco] Yassine BOUNOU (Morocco) scores an own goal."
            " -> goal for Haiti, score 0-1",
            user,
        )
        self.assertEqual(user.count("-> goal for"), 1)
        later = next(t for t in tasks if t["id"] == "400021452:ht:flat")
        self.assertIn("Score: Morocco 2-2 Haiti", later["messages"][1]["content"])
        self.assertIn("goal for Morocco, score 2-2", later["messages"][1]["content"])

    def test_stale_score_row_does_not_lower_the_score(self):
        rec = self.own_goal_record()
        tasks, _ = match_tasks(rec, make_config())
        for task in tasks:
            self.assertEqual(task["scoring"]["observed_score"]["away"] >= 1, True)

    def test_counts_use_kind_and_window(self):
        rec = record(GROUP)
        config = make_config()
        points = decision_points(rec["periods"], config)
        shown, _ = shown_prices(rec["prices"], points[3]["t"], config)
        state = build_state(rec, points, 3, "flat", shown, config)
        cutoff = points[3]["t"] - 60
        since = points[2]["t"] - 60
        fouls = [
            e
            for e in rec["events"]
            if e["kind"] == "foul" and e["team"] == "away" and e["t"] <= cutoff
        ]
        self.assertEqual(state["totals"]["away"]["fouls"], len(fouls))
        self.assertEqual(
            state["window"]["away"]["fouls"], sum(e["t"] > since for e in fouls)
        )
        self.assertEqual(state["score"], {"home": 1, "away": 0})
        self.assertNotIn("coin_toss", [r["kind"] for r in state["events"]])

    def test_price_history_is_as_of_each_earlier_point(self):
        tasks, _ = match_tasks(record(GROUP), make_config())
        by_id = {task["id"]: task for task in tasks}
        user = by_id[GROUP + ":h2+15:flat"]["messages"][1]["content"]
        for name, label in (("h1+15", "first half 15:00"), ("ht", "halftime")):
            earlier = by_id[f"{GROUP}:{name}:flat"]["scoring"]["shown_prices"]
            row = next(line for line in user.splitlines() if line.startswith(label))
            self.assertEqual(row.split()[-3:], [f"{earlier[o]:.4f}" for o in OUTCOMES])
        first = by_id[GROUP + ":h1+15:flat"]["messages"][1]["content"]
        self.assertIn("kickoff", first)
        self.assertNotIn("halftime", first)
        self.assertEqual(
            [label for label, _ in price_rows(by_id[GROUP + ":h2+30:flat"])],
            [
                "kickoff",
                "first half 15:00",
                "first half 30:00",
                "halftime",
                "second half 15:00",
                "now",
            ],
        )

    def test_incoherent_earlier_quote_is_not_shown(self):
        # the home leg is quoted off an empty book around the 15-minute mark
        rec = record(GROUP)
        t = decision_times(rec)["h1+15"]
        before = dict(price_rows(task_of(rec, "h1+30:flat")))
        self.assertNotIn("n/a", before["first half 15:00"])
        quote(rec, t, (0.2, None, None))
        tasks, drops = match_tasks(rec, make_config())
        self.assertEqual(drops, [("h1+15", "price_sum")])
        for task in tasks:
            rows = dict(price_rows(task))
            self.assertEqual(rows["first half 15:00"], ["n/a"] * 3)
            self.assertEqual(rows["kickoff"], before["kickoff"])
            self.assertNotIn("0.2000", task["messages"][1]["content"])

    def test_real_kickoff_blip_is_not_shown(self):
        # Liverpool v Bournemouth: 0.505 / 0.155 / 0.085 a minute before kickoff,
        # between two home quotes of 0.745
        rec = record(EPL)
        blip, reason = shown_prices(
            rec["prices"], rec["periods"]["h1_start"], make_config()
        )
        self.assertEqual((blip, reason), (None, "price_sum"))
        tasks, drops = match_tasks(rec, make_config())
        self.assertEqual(drops, [])
        for task in tasks:
            rows = price_rows(task)
            self.assertEqual(rows[0], ("kickoff", ["n/a"] * 3))
            for label, cells in rows[1:]:
                self.assertLessEqual(abs(sum(map(float, cells)) - 1), 0.05, label)

    def test_shots_count_every_attempt_in_both_feeds(self):
        config = make_config()

        def rows(rec, kind, team, cutoff):
            return sum(
                e["kind"] == kind and e["team"] == team and e["t"] <= cutoff
                for e in rec["events"]
            )

        # Premier League: a shot row is a miss; saves, blocks and goals stand alone
        late = task_of(record(EPL), "h2+30:flat")
        cutoff = late["t"] - 60
        user = late["messages"][1]["content"]
        self.assertIn("Liverpool: goals 2 (0), shots 14 (2),", user)
        self.assertIn("Bournemouth: goals 1 (1), shots 9 (3),", user)
        self.assertEqual(
            14,
            rows(RECORDS[EPL], "shot_attempt", "home", cutoff)
            + rows(RECORDS[EPL], "goal_prevention", "away", cutoff)
            + rows(RECORDS[EPL], "goal", "home", cutoff),
        )
        self.assertEqual(rows(RECORDS[EPL], "shot_attempt", "home", cutoff), 5)
        # World Cup: every attempt, saved or scored, already has a shot row
        late = task_of(record(GROUP_2), "h2+30:flat")
        cutoff = late["t"] - 60
        self.assertIn(
            "Colombia: goals 2 (1), shots 11 (4),", late["messages"][1]["content"]
        )
        self.assertEqual(rows(RECORDS[GROUP_2], "shot_attempt", "away", cutoff), 11)
        self.assertEqual(rows(RECORDS[GROUP_2], "goal_prevention", "home", cutoff), 3)
        tasks, _ = build_tasks(list(RECORDS.values()), config)
        stat = "{}: goals (\\d+) \\(\\d+\\), shots (\\d+) "
        for task in tasks:
            for name in (RECORDS[task["match_id"]][side] for side in ("home", "away")):
                found = re.search(stat.format(re.escape(name)), text(task))
                self.assertLessEqual(
                    int(found.group(1)), int(found.group(2)), task["id"]
                )

    def test_split_shot_rule_is_per_feed(self):
        events = [
            {"kind": "shot_attempt", "team": "home", "home_goals": 0, "away_goals": 0},
            {
                "kind": "goal_prevention",
                "team": "away",
                "home_goals": 0,
                "away_goals": 0,
            },
            {"kind": "goal", "team": "home", "home_goals": 1, "away_goals": 0},
            {
                "kind": "penalty_missed",
                "team": "away",
                "home_goals": 1,
                "away_goals": 0,
            },
            {"kind": "own_goal", "team": "home", "home_goals": 1, "away_goals": 1},
            {"kind": "goal_prevention", "team": None, "home_goals": 1, "away_goals": 1},
        ]
        whole = counts(events)
        self.assertEqual((whole["home"]["shots"], whole["away"]["shots"]), (1, 0))
        split = counts(events, split=True)
        self.assertEqual((split["home"]["shots"], split["away"]["shots"]), (3, 1))
        self.assertEqual(list(split["home"])[:2], ["goals", "shots"])
        plain = make_config({"split_shot_competitions": []})
        user = task_of(record(EPL), "h2+30:flat", plain)["messages"][1]["content"]
        self.assertIn("Liverpool: goals 2 (0), shots 5 (1),", user)


class PromptTests(unittest.TestCase):
    def test_tail_prices_are_printed_as_quoted(self):
        rec = record(GROUP)
        t = decision_times(rec)["h2+30"]
        quote(rec, t, (0.9995, 0.0005, 0.0005), after=600)
        user = task_of(rec, "h2+30:away")["messages"][1]["content"]
        self.assertEqual(
            price_rows(task_of(rec, "h2+30:away"))[-1],
            ("now", ["0.9995", "0.0005", "0.0005"]),
        )
        self.assertNotIn("1.000", user)
        costs = re.search(
            r"Buying now costs about: home ([\d.]+), draw ([\d.]+), away ([\d.]+)", user
        )
        for cost, price in zip(costs.groups(), ("0.9995", "0.0005", "0.0005")):
            self.assertGreaterEqual(float(cost), float(price))
        back = re.search(r"Selling away now returns about ([\d.]+)", user)
        self.assertLessEqual(float(back.group(1)), 0.0005)
        # a finer quote than the grid still reads to four places
        quote(rec, t, (0.22125, 0.3, 0.47875))
        self.assertEqual(
            price_rows(task_of(rec, "h2+30:flat"))[-1],
            ("now", ["0.2213", "0.3000", "0.4788"]),
        )

    def test_fill_rule_is_worded_as_it_is_applied(self):
        wording = {
            0: "fills at the next price quoted, usually within a minute",
            60: "at least 60 seconds after you decide, usually 60 to 120 seconds later",
            120: "at least 120 seconds after you decide, usually 120 to 180 seconds later",
        }
        for delay, phrase in wording.items():
            config = make_config({"fill_delay_s": delay})
            for rec in RECORDS.values():
                tasks, _ = match_tasks(rec, config)
                system = tasks[0]["messages"][0]["content"]
                self.assertIn(phrase, system)
                self.assertNotIn("at once", system)
                self.assertNotIn("current price", system)
                # and the sentence is true of the quotes: about one a minute
                for at in decision_times(rec).values():
                    for series in rec["prices"].values():
                        lag = next(s[0] for s in series if s[0] >= at + delay) - at
                        self.assertGreaterEqual(lag, delay)
                        self.assertLess(lag, delay + 90)


class PriceRuleTests(unittest.TestCase):
    def drop(self, rec, config=None):
        tasks, drops = match_tasks(rec, config or make_config())
        return tasks, drops

    def test_stale_shown_price_drops_the_point(self):
        rec = record(GROUP)
        t = decision_times(rec)["h1+30"]
        rec["prices"]["draw"] = [
            s for s in rec["prices"]["draw"] if not t - 200 < s[0] <= t
        ]
        tasks, drops = self.drop(rec)
        self.assertEqual(drops, [("h1+30", "stale_price")])
        self.assertEqual(len(tasks), 16)
        self.assertNotIn("h1+30", {task["decision"] for task in tasks})
        # the chunk before the gap now runs to the next usable point
        before = next(x for x in tasks if x["id"] == GROUP + ":h1+15:flat")
        self.assertEqual(before["scoring"]["chunk_end"], "ht")
        for seed in range(20):
            result = random_replay(tasks, seed, make_config())
            self.assertAlmostEqual(
                result["pnl"], cash_flow(rec, played(result)), places=9
            )

    def test_price_exactly_at_max_age_is_kept(self):
        rec = record(GROUP)
        t = math.floor(decision_times(rec)["h1+30"])
        for outcome in OUTCOMES:
            rec["prices"][outcome] = [
                s for s in rec["prices"][outcome] if not t - 89 <= s[0] <= t + 30
            ] + [[t - 89, 0.33]]
            rec["prices"][outcome].sort()
        self.assertEqual(self.drop(rec)[1], [])
        for outcome in OUTCOMES:
            rec["prices"][outcome] = [
                s for s in rec["prices"][outcome] if s[0] != t - 89
            ] + [[t - 91, 0.33]]
            rec["prices"][outcome].sort()
        self.assertEqual(self.drop(rec)[1], [("h1+30", "stale_price")])

    def test_prices_that_do_not_sum_near_one_drop_the_point(self):
        for factor, dropped in ((1.3, True), (0.8, True), (1.03, False)):
            rec = record(GROUP)
            t = decision_times(rec)["ht"]
            for sample in rec["prices"]["home"]:
                if t - 120 < sample[0] <= t:
                    sample[1] = sample[1] * factor
            _, drops = self.drop(rec)
            self.assertEqual(drops, [("ht", "price_sum")] if dropped else [])

    def test_quote_exactly_on_the_sum_limit_is_kept(self):
        # 0.495 + 0.26 + 0.295 is 1.05, but 1.0500000000000003 - 1 in floats
        rec = record(GROUP)
        t = decision_times(rec)["ht"]
        self.assertGreater(abs(sum((0.495, 0.26, 0.295)) - 1), 0.05)
        quote(rec, t, (0.495, 0.26, 0.295))
        tasks, drops = self.drop(rec)
        self.assertEqual(drops, [])
        self.assertEqual(len(tasks), 20)
        quote(rec, t, (0.495, 0.26, 0.296))
        self.assertEqual(self.drop(rec)[1], [("ht", "price_sum")])
        quote(rec, t, (0.405, 0.26, 0.285))
        self.assertEqual(self.drop(rec)[1], [])
        quote(rec, t, (0.405, 0.26, 0.284))
        self.assertEqual(self.drop(rec)[1], [("ht", "price_sum")])

    def test_market_probs_divide_by_the_exact_sum(self):
        # adding left to right gives 0.97; the exact sum rounds to 0.9700000000000001
        prices = (0.005, 0.055, 0.91)
        self.assertNotEqual((0.005 + 0.055) + 0.91, math.fsum(prices))
        rec = record(GROUP)
        quote(rec, decision_times(rec)["ht"], prices)
        scoring = task_of(rec, "ht:flat")["scoring"]
        self.assertEqual(scoring["shown_prices"], dict(zip(OUTCOMES, prices)))
        self.assertEqual(
            scoring["market_probs"],
            {o: p / math.fsum(prices) for o, p in zip(OUTCOMES, prices)},
        )

    def test_incoherent_fill_drops_the_point(self):
        # the away leg jumps to 0.49 for three minutes with no goal, as on the
        # real Manchester United v Burnley feed; nobody could trade at that price
        rec = record(GROUP)
        t = decision_times(rec)["h1+30"]
        clean = task_of(rec, "h1+30:away")["scoring"]
        self.assertLess(clean["shown_prices"]["away"], 0.2)
        for sample in rec["prices"]["away"]:
            if t + 30 < sample[0] <= t + 240:
                sample[1] = 0.49
        tasks, drops = self.drop(rec)
        self.assertEqual(drops, [("h1+30", "fill_sum")])
        self.assertEqual(len(tasks), 16)
        self.assertNotIn("h1+30", {task["decision"] for task in tasks})
        _, summary = build_tasks([rec], make_config())
        self.assertEqual(summary["decision_points_dropped"], {"fill_sum": 1})
        self.assertEqual(summary["tasks_dropped"], {"fill_sum": 4})
        # the chunk before it runs on to halftime and the match still adds up
        before = next(x for x in tasks if x["id"] == GROUP + ":h1+15:flat")
        self.assertEqual(before["scoring"]["chunk_end"], "ht")
        for seed in range(20):
            result = random_replay(tasks, seed, make_config())
            self.assertAlmostEqual(
                result["pnl"], cash_flow(rec, played(result)), places=9
            )
        # the earlier quote itself was fine, so later prompts still show it
        half = next(x for x in tasks if x["id"] == GROUP + ":ht:flat")
        self.assertNotIn("n/a", dict(price_rows(half))["first half 30:00"])

    def test_every_fill_sums_to_about_one(self):
        for config in (
            make_config(),
            make_config({"decisions": ["pre", "h1+15", "ht"]}),
        ):
            tasks, _ = build_tasks(list(RECORDS.values()), config)
            for task in tasks:
                for key in ("shown_prices", "fill_prices"):
                    total = math.fsum(task["scoring"][key].values())
                    self.assertLessEqual(abs(total - 1), 0.05 + 1e-9, (task["id"], key))

    def test_missing_fill_drops_the_point(self):
        rec = record(GROUP)
        t = decision_times(rec)["h2+15"]
        rec["prices"]["away"] = [
            s for s in rec["prices"]["away"] if not t + 60 <= s[0] <= t + 185
        ]
        self.assertEqual(self.drop(rec)[1], [("h2+15", "no_fill")])

    def test_fill_is_first_sample_after_the_delay(self):
        rec = record(GROUP)
        times = decision_times(rec)
        for delay in (0, 60, 120):
            config = make_config({"fill_delay_s": delay})
            tasks, _ = match_tasks(rec, config)
            task = next(x for x in tasks if x["id"] == GROUP + ":h1+30:flat")
            for outcome in OUTCOMES:
                want = next(
                    p
                    for at, p in rec["prices"][outcome]
                    if at >= times["h1+30"] + delay
                )
                self.assertEqual(task["scoring"]["fill_prices"][outcome], want)
                shown = [p for at, p in rec["prices"][outcome] if at <= times["h1+30"]]
                self.assertEqual(task["scoring"]["shown_prices"][outcome], shown[-1])

    def test_chunk_end_mark_is_next_points_shown_price(self):
        tasks, _ = match_tasks(record(GROUP), make_config())
        flat = sorted(
            (t for t in tasks if t["position"] == "flat"), key=lambda t: t["t"]
        )
        for now, later in zip(flat, flat[1:]):
            self.assertEqual(
                now["scoring"]["end_marks"], later["scoring"]["shown_prices"]
            )
            self.assertFalse(now["scoring"]["final_chunk"])
        self.assertTrue(flat[-1]["scoring"]["final_chunk"])
        self.assertEqual(flat[-1]["scoring"]["chunk_end"], "settlement")


class RewardTests(unittest.TestCase):
    def setUp(self):
        self.config = make_config()
        self.tasks = {
            task["id"]: task for task in match_tasks(record(GROUP), self.config)[0]
        }

    def test_legal_actions(self):
        self.assertEqual(legal_actions("flat"), FLAT)
        self.assertEqual(
            legal_actions("draw"), ["keep", "close", "switch_home", "switch_away"]
        )
        for task in self.tasks.values():
            self.assertEqual(task["legal_actions"], legal_actions(task["position"]))
            self.assertEqual(
                set(task["scoring"]["action_rewards"]), set(task["legal_actions"])
            )

    def test_action_rewards_match_the_formulas(self):
        c = self.config
        for name in ("h1+15", "ht", "h2+30"):
            flat = self.tasks[f"{GROUP}:{name}:flat"]["scoring"]
            s, f, e = flat["shown_prices"], flat["fill_prices"], flat["end_marks"]
            self.assertEqual(flat["action_rewards"]["do_nothing"], 0.0)
            for o in OUTCOMES:
                self.assertAlmostEqual(
                    flat["action_rewards"]["buy_" + o], e[o] - buy_cost(f[o], c)
                )
            held = self.tasks[f"{GROUP}:{name}:home"]["scoring"]["action_rewards"]
            sold = sell_proceeds(f["home"], c) - s["home"]
            self.assertAlmostEqual(held["keep"], e["home"] - s["home"])
            self.assertAlmostEqual(held["close"], sold)
            self.assertAlmostEqual(
                held["switch_draw"], sold + e["draw"] - buy_cost(f["draw"], c)
            )
            # switching is closing, then buying from flat
            self.assertAlmostEqual(
                held["switch_away"], held["close"] + flat["action_rewards"]["buy_away"]
            )

    def test_settlement_pays_one_or_zero_without_exit_cost(self):
        last = self.tasks[GROUP + ":h2+30:draw"]["scoring"]
        self.assertEqual(last["result"], "draw")
        self.assertEqual(last["end_marks"], {"home": 0.0, "draw": 1.0, "away": 0.0})
        self.assertAlmostEqual(
            last["action_rewards"]["keep"], 1.0 - last["shown_prices"]["draw"]
        )
        lost = self.tasks[GROUP + ":h2+30:home"]["scoring"]
        self.assertAlmostEqual(
            lost["action_rewards"]["keep"], -lost["shown_prices"]["home"]
        )

    def test_valid_answer_is_a_lookup(self):
        task = self.tasks[GROUP + ":h2+15:flat"]
        out = score(task, "<think>hm</think>" + answer("buy_draw"), self.config)
        self.assertTrue(out["valid"])
        self.assertEqual(out["action"], "buy_draw")
        self.assertEqual(out["next_position"], "draw")
        self.assertEqual(out["reward"], task["scoring"]["action_rewards"]["buy_draw"])
        self.assertEqual(out["pnl"], out["reward"])
        self.assertEqual(out["trades"], 1)
        self.assertGreater(out["cost"], 0.01)

    def test_invalid_answer_holds_still_and_pays_the_penalty(self):
        cases = ["no json here", answer("keep"), answer("buy_home", (0.9, 0.9, 0.9))]
        errors = ["no_json", "illegal_action", "bad_probs"]
        for completion, error in zip(cases, errors):
            out = score(self.tasks[GROUP + ":h2+15:flat"], completion, self.config)
            self.assertEqual((out["valid"], out["error"]), (False, error))
            self.assertEqual(out["action"], "do_nothing")
            self.assertEqual(out["next_position"], "flat")
            self.assertAlmostEqual(out["reward"], -0.04)
            self.assertEqual(out["pnl"], 0.0)
        held = self.tasks[GROUP + ":h2+15:home"]
        out = score(held, answer("buy_home"), self.config)
        keep = held["scoring"]["action_rewards"]["keep"]
        self.assertEqual((out["action"], out["next_position"]), ("keep", "home"))
        self.assertAlmostEqual(out["reward"], keep - 0.04)
        self.assertAlmostEqual(out["pnl"], keep)

    def test_malformed_answers_score_as_invalid_instead_of_raising(self):
        held = self.tasks[GROUP + ":h2+15:home"]
        keep = held["scoring"]["action_rewards"]["keep"]
        huge = '{"probs":{"home":1' + "0" * 400 + ',"draw":0,"away":0},"action":"keep"}'
        deep = '{"a":' * 10200 + "1" + "}" * 10200
        with self.assertRaises(RecursionError):
            json.loads(deep)
        cases = [
            (huge, "bad_probs"),
            (deep, "bad_probs"),
            ('{"probs": ' * 1100, "no_json"),
        ]
        for completion, error in cases:
            out = score(held, completion, self.config)
            self.assertEqual((out["valid"], out["error"]), (False, error))
            self.assertEqual((out["action"], out["next_position"]), ("keep", "home"))
            self.assertAlmostEqual(out["reward"], keep - 0.04)

    def test_shaping_terms_are_logged_and_weighted(self):
        task = self.tasks[GROUP + ":h2+15:flat"]
        scoring = task["scoring"]
        plain = score(task, answer("buy_home", (0.6, 0.3, 0.1)), self.config)
        edge = math.log(0.3) - math.log(scoring["market_probs"]["draw"])
        best = max(scoring["action_rewards"].values())
        self.assertAlmostEqual(plain["log_score_edge"], edge)
        self.assertAlmostEqual(plain["missed"], best - plain["pnl"])
        self.assertGreaterEqual(plain["missed"], 0)
        self.assertEqual(plain["reward"], plain["pnl"])
        shaped = score(
            task,
            answer("buy_home", (0.6, 0.3, 0.1)),
            make_config({"w_log": 0.5, "w_missed": 0.25}),
        )
        self.assertAlmostEqual(
            shaped["reward"], plain["pnl"] + 0.5 * edge - 0.25 * plain["missed"]
        )
        zero = score(task, answer("do_nothing", (1.0, 0.0, 0.0)), self.config)
        self.assertAlmostEqual(
            zero["log_score_edge"],
            math.log(1e-4) - math.log(scoring["market_probs"]["draw"]),
        )
        self.assertAlmostEqual(sum(scoring["market_probs"].values()), 1.0)

    def test_liquidation_mark_values_open_positions_at_the_bid(self):
        config = make_config({"mark": "liquidation"})
        marked = {t["id"]: t for t in match_tasks(record(GROUP), config)[0]}
        for key, task in self.tasks.items():
            mid, liq = task["scoring"], marked[key]["scoring"]
            for action, reward in mid["action_rewards"].items():
                opens = mid["next_position"][action] != "flat"
                if mid["final_chunk"] or not opens:
                    self.assertAlmostEqual(liq["action_rewards"][action], reward)
                else:
                    outcome = mid["next_position"][action]
                    haircut = mid["end_marks"][outcome] - sell_proceeds(
                        mid["end_marks"][outcome], config
                    )
                    self.assertAlmostEqual(
                        liq["action_rewards"][action], reward - haircut
                    )

    def test_fill_start_mark_makes_close_exactly_the_exit_cost(self):
        config = make_config({"start_mark": "fill"})
        for task in match_tasks(record(GROUP), config)[0]:
            if task["position"] != "flat":
                scoring = task["scoring"]
                self.assertAlmostEqual(
                    scoring["action_rewards"]["close"],
                    -scoring["action_costs"]["close"],
                )

    def test_action_gaps_do_not_depend_on_the_start_mark(self):
        other = {
            t["id"]: t
            for t in match_tasks(record(GROUP), make_config({"start_mark": "fill"}))[0]
        }
        for key, task in self.tasks.items():
            a, b = (
                task["scoring"]["action_rewards"],
                other[key]["scoring"]["action_rewards"],
            )
            first = task["legal_actions"][0]
            for action in a:
                self.assertAlmostEqual(a[action] - a[first], b[action] - b[first])


class ParseTests(unittest.TestCase):
    def test_plain_answer(self):
        out = parse(answer("buy_home"), FLAT)
        self.assertTrue(out["ok"])
        self.assertEqual(out["action"], "buy_home")
        self.assertEqual(out["probs"], {"home": 0.5, "draw": 0.3, "away": 0.2})
        self.assertEqual(out["reason"], "test")

    def test_think_block_is_ignored(self):
        thought = "<think>maybe " + answer("buy_away") + " or not</think>\n"
        self.assertEqual(
            parse(thought + answer("buy_draw"), FLAT)["action"], "buy_draw"
        )
        only = parse("<think>" + answer("buy_away") + "</think> I pass.", FLAT)
        self.assertEqual((only["ok"], only["error"]), (False, "no_json"))
        open_tag = parse("<think>" + answer("buy_away"), FLAT)
        self.assertEqual(open_tag["error"], "no_json")
        closing_only = parse("reasoning {x}</think>" + answer("buy_home"), FLAT)
        self.assertEqual(closing_only["action"], "buy_home")

    def test_think_tag_inside_the_answer_is_only_text(self):
        for reason in (
            "no need to <think> further",
            "done </think> now",
            "<think>hold</think>",
            "</THINK> { <think>",
        ):
            body = json.dumps(
                {
                    "probs": {"home": 0.5, "draw": 0.3, "away": 0.2},
                    "action": "buy_home",
                    "reason": reason,
                }
            )
            for completion in (
                body,
                "<think>or " + answer("buy_away") + "</think>" + body,
            ):
                out = parse(completion, FLAT)
                self.assertEqual((out["ok"], out["action"]), (True, "buy_home"), reason)
                self.assertEqual(out["reason"], reason)

    def test_think_tags_in_any_case(self):
        draft = "<THINK>" + answer("buy_away") + "</THINK>"
        self.assertEqual(parse(draft + " I pass.", FLAT)["error"], "no_json")
        self.assertEqual(parse(draft + answer("buy_draw"), FLAT)["action"], "buy_draw")
        self.assertEqual(
            parse("<Think>" + answer("buy_away"), FLAT)["error"], "no_json"
        )

    def test_reasoning_left_open_is_never_the_answer(self):
        # an answer, then second thoughts that never finish: the answer stands
        out = parse(answer("buy_home") + "<think>or " + answer("buy_away"), FLAT)
        self.assertEqual((out["ok"], out["action"]), (True, "buy_home"))
        two = "<think>a</think>" + answer("buy_home") + "<think>b</think> so "
        self.assertEqual(parse(two + answer("buy_draw"), FLAT)["action"], "buy_draw")
        # an open tag in prose cannot be told from reasoning cut short, so what
        # follows it is treated as a draft
        prose = "I will not use <think> tags. " + answer("buy_home")
        self.assertEqual(parse(prose, FLAT)["error"], "no_json")

    def test_trailing_text_and_code_fence(self):
        out = parse("```json\n" + answer("do_nothing") + "\n```\nGood luck!", FLAT)
        self.assertEqual((out["ok"], out["action"]), (True, "do_nothing"))
        out = parse("Odds {home > away}. " + answer("buy_home") + " Thanks {", FLAT)
        self.assertEqual((out["ok"], out["action"]), (True, "buy_home"))

    def test_last_object_wins_and_is_the_outer_one(self):
        both = answer("buy_home") + " On reflection: " + answer("buy_away")
        self.assertEqual(parse(both, FLAT)["action"], "buy_away")
        nested = json.dumps(
            {
                "probs": {"home": 0.2, "draw": 0.3, "away": 0.5},
                "action": "buy_away",
                "reason": 'braces } { and "quotes"',
            }
        )
        out = parse(nested, FLAT)
        self.assertEqual((out["ok"], out["action"]), (True, "buy_away"))

    def test_illegal_action(self):
        for action in ("keep", "buy_both", "BUY_HOME", None, 3):
            body = json.dumps(
                {"probs": {"home": 0.5, "draw": 0.3, "away": 0.2}, "action": action}
            )
            out = parse(body, FLAT)
            self.assertEqual((out["ok"], out["error"]), (False, "illegal_action"))
            self.assertIsNotNone(out["probs"])
        held = ["keep", "close", "switch_draw", "switch_away"]
        self.assertFalse(parse(answer("switch_home"), held)["ok"])
        self.assertTrue(parse(answer(" close "), held)["ok"])

    def test_bad_probs(self):
        bad = [
            {"home": 0.5, "draw": 0.5, "away": 0.5},
            {"home": 0.5, "draw": 0.3},
            {"home": 1.2, "draw": -0.1, "away": -0.1},
            {"home": "0.5", "draw": 0.3, "away": 0.2},
            {"home": True, "draw": 0, "away": 0},
            {"home": 0.5, "draw": 0.3, "away": 0.2, "other": 0.0},
            [0.5, 0.3, 0.2],
            None,
        ]
        for probs in bad:
            out = parse(json.dumps({"probs": probs, "action": "buy_home"}), FLAT)
            self.assertEqual((out["ok"], out["error"]), (False, "bad_probs"), probs)
        for odd in ("NaN", "Infinity", "-Infinity", "1e999", "1" + "0" * 400, "-0.5"):
            body = '{"probs": {"home": %s, "draw": 0.5, "away": 0.5}, "action": "buy_home"}'
            self.assertEqual(parse(body % odd, FLAT)["error"], "bad_probs", odd)

    def test_zero_probabilities_are_invalid_even_with_large_tolerance(self):
        self.assertEqual(
            parse(answer("buy_home", (0, 0, 0)), FLAT, tol=1)["error"], "bad_probs"
        )

    def test_probs_within_tolerance_are_renormalised(self):
        out = parse(answer("buy_home", (0.52, 0.31, 0.21)), FLAT)
        self.assertTrue(out["ok"])
        self.assertAlmostEqual(sum(out["probs"].values()), 1.0)
        self.assertAlmostEqual(out["probs"]["home"], 0.52 / 1.04)
        self.assertFalse(parse(answer("buy_home", (0.52, 0.33, 0.21)), FLAT)["ok"])
        self.assertTrue(parse(answer("buy_home", (0.5, 0.3, 0.16)), FLAT)["ok"])
        self.assertFalse(parse(answer("buy_home", (0.5, 0.3, 0.14)), FLAT)["ok"])
        # exactly on the limit counts as within it
        self.assertGreater(abs(sum((0.52, 0.32, 0.21)) - 1), 0.05)
        self.assertTrue(parse(answer("buy_home", (0.52, 0.32, 0.21)), FLAT)["ok"])
        self.assertTrue(parse(answer("buy_home", (0.5, 0.3, 0.15)), FLAT)["ok"])

    def test_nothing_usable(self):
        for completion in ("", "buy_home", "{not json}", "[1, 2]", None, 7):
            out = parse(completion, FLAT)
            self.assertEqual((out["ok"], out["error"]), (False, "no_json"))


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.config = make_config()
        tasks, _ = build_tasks(list(RECORDS.values()), self.config)
        self.matches = by_match(tasks)

    def test_chunk_rewards_telescope_to_cash_flow(self):
        traded = 0
        for match_id, tasks in self.matches.items():
            for seed in range(50):
                result = random_replay(tasks, seed, self.config)
                self.assertEqual(len(result["steps"]), 5)
                steps = math.fsum(step["pnl"] for step in result["steps"])
                self.assertAlmostEqual(
                    steps, cash_flow(RECORDS[match_id], played(result)), places=9
                )
                self.assertEqual(result["pnl"], steps)
                self.assertEqual(result["reward"], result["pnl"])
                traded += result["trades"]
        self.assertGreater(traded, 400)

    def test_telescopes_under_other_costs_and_delays(self):
        # cash_flow hard-codes the default costs, so only the timing may vary here
        config = make_config({"event_delay_s": 0, "price_max_age_s": 75})
        tasks, _ = match_tasks(record(EPL), config)
        for seed in range(20):
            result = random_replay(tasks, seed, config)
            self.assertAlmostEqual(
                result["pnl"], cash_flow(RECORDS[EPL], played(result)), places=9
            )

    def test_invalid_answers_keep_the_position_and_cost_the_penalty(self):
        tasks = self.matches[GROUP]
        for seed in range(30):
            result = random_replay(tasks, seed, self.config, invalid_share=0.4)
            trades = [
                (s["decision"], s["action"]) for s in result["steps"] if s["valid"]
            ]
            self.assertAlmostEqual(
                result["pnl"], cash_flow(RECORDS[GROUP], trades), places=9
            )
            self.assertAlmostEqual(
                result["reward"], result["pnl"] - 0.04 * result["invalid"]
            )
            for before, after in zip(result["steps"], result["steps"][1:]):
                self.assertTrue(after["task_id"].endswith(before["next_position"]))

    def test_policy_may_return_a_completion(self):
        tasks = self.matches[GROUP]
        result = replay_match(
            tasks,
            lambda task: answer("buy_draw" if task["position"] == "flat" else "keep"),
            self.config,
        )
        self.assertEqual(result["final_position"], "draw")
        self.assertEqual((result["trades"], result["invalid"]), (1, 0))
        self.assertAlmostEqual(
            result["pnl"], cash_flow(RECORDS[GROUP], [("h1+15", "buy_draw")]), places=9
        )
        self.assertIsNotNone(result["steps"][0]["log_score_edge"])

    def test_liquidation_mark_does_not_telescope(self):
        config = make_config({"mark": "liquidation"})
        tasks, _ = match_tasks(record(GROUP), config)
        hold = replay_match(
            tasks, lambda t: "buy_draw" if t["position"] == "flat" else "keep", config
        )
        self.assertLess(hold["pnl"], cash_flow(RECORDS[GROUP], [("h1+15", "buy_draw")]))


class BaselineTests(unittest.TestCase):
    def setUp(self):
        self.config = make_config({"bootstrap_reps": 200})
        self.tasks, _ = build_tasks(list(RECORDS.values()), self.config)

    def test_do_nothing_is_zero_and_hindsight_is_the_ceiling(self):
        results = {name: run_policy(self.tasks, name, self.config) for name in POLICIES}
        for result in results["do_nothing"]:
            self.assertEqual(
                (result["pnl"], result["trades"], result["cost"]), (0, 0, 0)
            )
        for i, best in enumerate(results["hindsight_best"]):
            self.assertGreaterEqual(best["pnl"], 0)
            for name in POLICIES:
                self.assertGreaterEqual(best["pnl"] + 1e-12, results[name][i]["pnl"])
            for seed in range(40):
                tasks = by_match(self.tasks)[best["match_id"]]
                other = random_replay(tasks, seed, self.config)
                self.assertGreaterEqual(best["pnl"] + 1e-12, other["pnl"])

    def test_buy_and_hold_policies(self):
        home = {
            r["match_id"]: r
            for r in run_policy(self.tasks, "always_buy_home_and_hold", self.config)
        }
        fav = {
            r["match_id"]: r
            for r in run_policy(self.tasks, "buy_favourite_and_hold", self.config)
        }
        lead = {
            r["match_id"]: r
            for r in run_policy(
                self.tasks, "buy_leader_else_draw_and_hold", self.config
            )
        }
        for match_id, rec in RECORDS.items():
            self.assertEqual(home[match_id]["trades"], 1)
            self.assertEqual(home[match_id]["final_position"], "home")
            self.assertAlmostEqual(
                home[match_id]["pnl"], cash_flow(rec, [("h1+15", "buy_home")]), places=9
            )
        # Colombia were 0.7+ favourites away to Uzbekistan and won
        self.assertEqual(fav[GROUP_2]["final_position"], "away")
        self.assertGreater(fav[GROUP_2]["pnl"], 0)
        # Czechia led 1-0 after 15 minutes; the match ended 1-1
        self.assertEqual(lead[GROUP]["final_position"], "home")
        self.assertLess(lead[GROUP]["pnl"], 0)
        # 0-0 after 15 minutes of the final, which was level after 90
        self.assertEqual(lead[FINAL]["final_position"], "draw")
        self.assertGreater(lead[FINAL]["pnl"], 0)

    def test_random_policy_and_table_are_seeded(self):
        first = run_baselines(self.tasks, self.config)
        again = run_baselines(self.tasks, self.config)
        self.assertEqual(first, again)
        other = run_baselines(
            self.tasks, make_config({"seed": 1, "bootstrap_reps": 200})
        )
        self.assertNotEqual(random_means(first), random_means(other))
        self.assertEqual(
            sorted({row["group"] for row in first}), ["test", "train", "val"]
        )
        self.assertEqual(len(first), 3 * len(POLICIES))
        for row in first:
            self.assertLessEqual(row["ci_low_c"], row["mean_c"] + 1e-9)
            self.assertGreaterEqual(row["ci_high_c"], row["mean_c"] - 1e-9)
        val = next(
            r for r in first if r["group"] == "val" and r["policy"] == "do_nothing"
        )
        self.assertEqual((val["matches"], val["mean_c"], val["sd_c"]), (2, 0.0, 0.0))

    def test_bootstrap_resamples_whole_matches(self):
        values = [-40.0, 10.0, 60.0, 5.0, -15.0, 30.0]
        low, high = bootstrap_ci(values, 500, "seed")
        self.assertEqual((low, high), bootstrap_ci(values, 500, "seed"))
        self.assertLess(low, sum(values) / 6)
        self.assertGreater(high, sum(values) / 6)
        self.assertGreaterEqual(low, min(values))
        self.assertLessEqual(high, max(values))
        self.assertEqual(bootstrap_ci([7.0], 100, "seed"), (7.0, 7.0))

    def test_sensitivity_varies_one_setting_at_a_time(self):
        rows = sensitivity(
            list(RECORDS.values()), {"bootstrap_reps": 50}, ["always_buy_home_and_hold"]
        )
        settings = {
            (r["half_spread"], r["fill_delay_s"], r["event_delay_s"]) for r in rows
        }
        self.assertEqual(len(settings), 7)
        self.assertIn((0.01, 60, 60), settings)
        self.assertIn((0.05, 60, 60), settings)
        cost = {
            r["half_spread"]: r["cost_c"]
            for r in rows
            if r["group"] == "val"
            and (r["fill_delay_s"], r["event_delay_s"]) == (60, 60)
        }
        self.assertLess(cost[0.01], cost[0.03])
        self.assertAlmostEqual(cost[0.05] - cost[0.01], 4.0)

    def test_single_step_table(self):
        rows = single_step(self.tasks)
        self.assertEqual(len(rows), 16)
        nothing = next(r for r in rows if r["action"] == "do_nothing")
        self.assertEqual((nothing["tasks"], nothing["mean_c"]), (20, 0.0))
        summary = step_summary(self.tasks)
        self.assertEqual(summary["tasks"], 80)
        self.assertGreater(summary["trade_beats_noop_share"], 0)
        self.assertLessEqual(summary["trade_beats_noop_share"], 1)
        self.assertGreaterEqual(summary["mean_missed_noop_c"], 0)


class SplitTests(unittest.TestCase):
    def test_default_rule(self):
        config = make_config()
        self.assertEqual(split_for(RECORDS[EPL], config), "train")
        staged = {**RECORDS[GROUP], "stage_name": "First Stage"}
        self.assertEqual(split_for(staged, config), "val")
        for stage in ("Round of 32", "Quarter-final", "Final"):
            self.assertEqual(split_for({**staged, "stage_name": stage}, config), "test")

    def test_missing_stage_falls_back_to_the_calendar(self):
        config = make_config()
        group, final = record(GROUP), record(FINAL)
        self.assertEqual(
            (group.pop("stage_name"), final.pop("stage_name")), ("First Stage", "Final")
        )
        self.assertEqual(split_for(group, config), "val")
        self.assertEqual(split_for(final, config), "test")
        last_group = {**group, "scheduled_utc": "2026-06-28T02:00:00Z"}
        first_knockout = {**group, "scheduled_utc": "2026-06-28T19:00:00Z"}
        self.assertEqual(split_for(last_group, config), "val")
        self.assertEqual(split_for(first_knockout, config), "test")

    def test_demo_matches_are_always_test(self):
        config = make_config()
        demo = {**RECORDS[GROUP], "match_id": "400021528", "stage_name": "First Stage"}
        self.assertEqual(split_for(demo, config), "test")
        self.assertTrue(is_demo(demo, config))
        tasks, _ = match_tasks({**record(GROUP), "match_id": "400021528"}, config)
        self.assertEqual({(t["split"], t["demo"]) for t in tasks}, {("test", True)})
        plain, _ = match_tasks(record(GROUP), config)
        self.assertEqual({(t["split"], t["demo"]) for t in plain}, {("val", False)})

    def test_unknown_competition_is_dropped_and_counted(self):
        other = {**record(GROUP), "competition_id": "999"}
        self.assertIsNone(split_for(other, make_config()))
        tasks, summary = build_tasks([other], make_config())
        self.assertEqual(tasks, [])
        self.assertEqual(summary["decision_points_dropped"], {"unknown_competition": 5})
        self.assertEqual(summary["tasks_dropped"], {"unknown_competition": 20})

    def test_split_rule_is_config_driven(self):
        config = make_config({"train_competitions": ["17"], "demo_match_ids": []})
        self.assertEqual(split_for(RECORDS[FINAL], config), "train")


class BuildTests(unittest.TestCase):
    def test_task_shape_and_summary(self):
        tasks, summary = build_tasks(load_records(FIXTURES), make_config())
        self.assertEqual(len(tasks), 80)
        self.assertEqual(len({task["id"] for task in tasks}), 80)
        self.assertEqual(
            summary["tasks_by_split"], {"test": 20, "train": 20, "val": 40}
        )
        self.assertEqual(summary["decision_points_dropped"], {})
        task = next(t for t in tasks if t["id"] == EPL + ":h2+15:away")
        self.assertEqual(
            (task["decision"], task["decision_idx"], task["position"], task["split"]),
            ("h2+15", 3, "away", "train"),
        )
        self.assertEqual(task["competition"], "2000000000")
        self.assertEqual(task["t"], RECORDS[EPL]["periods"]["h2_start"] + 900)
        self.assertEqual(task["scoring"]["config_hash"], config_hash(make_config()))
        user = task["messages"][1]["content"]
        self.assertIn(
            "Premier League 2025/26, Regular Season: Liverpool (home) v Bournemouth (away)",
            user,
        )
        self.assertIn("Position: 1 share of away (Bournemouth)", user)
        self.assertIn("Legal actions: keep, close, switch_home, switch_draw", user)
        self.assertIn('"action": "<one legal action>"', task["messages"][0]["content"])
        for task in tasks:
            self.assertLess(len(text(task)), 8000)

    def test_stage_name_is_shown_when_present(self):
        staged = {**record(FINAL), "stage_name": "Final"}
        tasks, _ = match_tasks(staged, make_config())
        self.assertIn(
            "FIFA World Cup 2026, Final: Spain (home) v Argentina (away)",
            tasks[0]["messages"][1]["content"],
        )

    def test_same_config_gives_identical_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for name, records in (
                ("a", load_records(FIXTURES)),
                ("b", list(reversed(load_records(FIXTURES)))),
            ):
                reordered = Path(directory) / (name + ".jsonl")
                reordered.write_text("".join(json.dumps(r) + "\n" for r in records))
                tasks, _ = build_tasks(load_records(reordered), make_config())
                for suffix in (".jsonl", ".jsonl.gz"):
                    paths.append(Path(directory) / (name + "-tasks" + suffix))
                    write_tasks(paths[-1], tasks)
            self.assertEqual(paths[0].read_bytes(), paths[2].read_bytes())
            self.assertEqual(paths[1].read_bytes(), paths[3].read_bytes())
            self.assertEqual(paths[0].read_bytes().count(b"\n"), 80)
            changed, _ = build_tasks(
                load_records(FIXTURES), make_config({"half_spread": 0.03})
            )
            write_tasks(paths[2], changed)
            self.assertNotEqual(paths[0].read_bytes(), paths[2].read_bytes())

    def test_wrong_schema_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.jsonl"
            path.write_text(json.dumps({**record(GROUP), "schema": "other/v0"}) + "\n")
            with self.assertRaises(ValueError):
                load_records(path)


class CliTests(unittest.TestCase):
    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main([str(a) for a in argv])
        return out.getvalue()

    def test_build_show_baselines_stats(self):
        with tempfile.TemporaryDirectory() as directory:
            tasks = Path(directory) / "tasks.jsonl.gz"
            built = self.run_cli(
                "build-tasks", "--records", FIXTURES, "--output", tasks
            )
            self.assertIn('"tasks": 80', built)
            meta = json.loads((Path(directory) / "tasks.meta.json").read_text())
            self.assertEqual(meta["config"]["half_spread"], 0.01)
            self.assertEqual(meta["tasks"], 80)
            task_id = FINAL + ":ht:draw"
            shown = self.run_cli("show", task_id, "--tasks", tasks)
            self.assertIn("--- user ---", shown)
            self.assertIn("Position: 1 share of draw", shown)
            self.assertNotIn("action_rewards", shown)
            revealed = self.run_cli("show", task_id, "--tasks", tasks, "--reveal")
            self.assertIn("action_rewards", revealed)
            table = self.run_cli(
                "baselines", "--tasks", tasks, "--set", "bootstrap_reps=50"
            )
            self.assertIn("hindsight_best", table)
            self.assertIn("ci_low_c", table)
            rows = json.loads(
                self.run_cli(
                    "baselines",
                    "--records",
                    FIXTURES,
                    "--set",
                    "bootstrap_reps=50",
                    "--set",
                    "half_spread=0.03",
                    "--policy",
                    "do_nothing",
                    "--json",
                )
            )
            self.assertEqual([row["group"] for row in rows], ["train", "val", "test"])
            stats = self.run_cli("stats", "--tasks", tasks)
            self.assertIn("prompt size in characters", stats)
            self.assertIn("single step, val: 40 tasks", stats)
            with self.assertRaises(SystemExit):
                self.run_cli("show", "nope:ht:flat", "--tasks", tasks)
            with self.assertRaises(SystemExit):
                self.run_cli("stats", "--tasks", tasks, "--set", "fill_delay_s=0")

    def test_meta_file_is_named_after_the_whole_task_file(self):
        self.assertEqual(
            [
                meta_path(Path(name)).name
                for name in (
                    "tasks.jsonl.gz",
                    "tasks.spread03.jsonl.gz",
                    "a.b.jsonl",
                    "x",
                )
            ],
            [
                "tasks.meta.json",
                "tasks.spread03.meta.json",
                "a.b.meta.json",
                "x.meta.json",
            ],
        )
        with tempfile.TemporaryDirectory() as directory:
            wide = Path(directory) / "tasks.spread03.jsonl.gz"
            plain = Path(directory) / "tasks.jsonl.gz"
            self.run_cli(
                "build-tasks", "--records", FIXTURES, "--output", wide,
                "--set", "half_spread=0.03",
            )  # fmt: skip
            self.run_cli("build-tasks", "--records", FIXTURES, "--output", plain)
            spreads = {
                path.name: json.loads(path.read_text())["config"]["half_spread"]
                for path in Path(directory).glob("*.meta.json")
            }
            self.assertEqual(
                spreads, {"tasks.meta.json": 0.01, "tasks.spread03.meta.json": 0.03}
            )

    def test_set_values_are_typed(self):
        with tempfile.TemporaryDirectory() as directory:
            tasks = Path(directory) / "tasks.jsonl.gz"
            self.run_cli(
                "build-tasks", "--records", FIXTURES, "--output", tasks,
                "--set", "show_kickoff_price=False",
            )  # fmt: skip
            shown = self.run_cli("show", GROUP + ":h1+30:flat", "--tasks", tasks)
            self.assertNotIn("kickoff ", shown)
            self.assertIn("first half 15:00", shown)
            for bad in ("half_spread=.03", "fill_delay_s=abc", "foo=1", "mark=bid"):
                errors = io.StringIO()
                with self.assertRaises(SystemExit) as stopped:
                    with contextlib.redirect_stderr(errors):
                        self.run_cli(
                            "build-tasks", "--records", FIXTURES, "--output", tasks,
                            "--set", bad,
                        )  # fmt: skip
                self.assertEqual(stopped.exception.code, 2)
                self.assertIn(bad.split("=")[0], errors.getvalue())
                self.assertNotIn("Traceback", errors.getvalue())

    def test_bad_decisions_fail_before_reading_or_writing(self):
        for decision in ("[]", '["wat"]', '["h2+15","ht"]', '["ht","ht"]'):
            with tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "tasks.jsonl.gz"
                error = io.StringIO()
                with (
                    contextlib.redirect_stderr(error),
                    self.assertRaises(SystemExit) as stopped,
                ):
                    main(
                        [
                            "build-tasks",
                            "--records",
                            "/missing",
                            "--output",
                            str(output),
                            "--set",
                            "decisions=" + decision,
                        ]
                    )
                self.assertEqual(stopped.exception.code, 2)
                self.assertIn("decisions", error.getvalue())
                self.assertFalse(output.exists())

    def test_build_records_command_is_removed(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(["build-records"])


class TemplateAndMarketFeesTests(unittest.TestCase):
    def test_missing_template_variable_raises(self):
        from jinja2 import UndefinedError
        from app.trade_env.prompt import user_prompt, system_prompt

        with self.assertRaises(UndefinedError):
            user_prompt({}, make_config())
        with self.assertRaises(UndefinedError):
            system_prompt({})

    def test_market_fees_change_only_costs_and_are_shown(self):
        rec = record()
        rec["fee_rates"] = {"home": 0.0, "draw": 0.03, "away": 0.05}
        config = make_config({"fee_rate": "market"})
        task = match_tasks(rec, config)[0][0]
        self.assertIn("home 0, draw 0.03, away 0.05", task["messages"][0]["content"])
        self.assertEqual(task["scoring"]["fee_rates"], rec["fee_rates"])
        pricing = {**config, "market_fee_rates": rec["fee_rates"]}
        for side in OUTCOMES:
            p = task["scoring"]["fill_prices"][side]
            expected = task["scoring"]["end_marks"][side] - buy_cost(p, pricing, side)
            self.assertAlmostEqual(
                task["scoring"]["action_rewards"]["buy_" + side], expected
            )
        for held in match_tasks(rec, config)[0]:
            for action in held["legal_actions"]:
                scoring = held["scoring"]
                start, fill, end = (
                    scoring[k] for k in ("shown_prices", "fill_prices", "end_marks")
                )
                position = held["position"]
                if action == "do_nothing":
                    expected = 0
                elif action == "keep":
                    expected = end[position] - start[position]
                else:
                    expected = (
                        sell_proceeds(fill[position], pricing, position)
                        - start[position]
                        if position != "flat"
                        else 0
                    )
                    if action != "close":
                        side = action.split("_", 1)[1]
                        expected += end[side] - buy_cost(fill[side], pricing, side)
                self.assertAlmostEqual(scoring["action_rewards"][action], expected)
        del rec["fee_rates"]
        with self.assertRaisesRegex(ValueError, "fee rates"):
            match_tasks(rec, config)


if __name__ == "__main__":
    unittest.main()
