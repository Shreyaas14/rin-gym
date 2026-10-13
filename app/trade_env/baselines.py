"""Policies that need no model, run through full-match replay."""

import random

from .config import OUTCOMES, POSITIONS, config_hash, make_config
from .replay import replay
from .reward import noop
from .stats import SPLITS, bootstrap_ci, cents, mean, sd
from .tasks import build_tasks

SENSITIVITY = {
    "half_spread": [0.01, 0.03, 0.05],
    "fill_delay_s": [0, 60, 120],
    "event_delay_s": [0, 60, 120],
}


def do_nothing(tasks, rng):
    return lambda task: noop(task["position"])


def random_legal(tasks, rng):
    return lambda task: rng.choice(task["legal_actions"])


def buy_and_hold(pick):
    def make(tasks, rng):
        def policy(task):
            return "buy_" + pick(task) if task["position"] == "flat" else "keep"

        return policy

    return make


def favourite(task):
    prices = task["scoring"]["shown_prices"]
    return max(OUTCOMES, key=lambda o: prices[o])


def leader_else_draw(task):
    score = task["scoring"]["observed_score"]
    if score["home"] == score["away"]:
        return "draw"
    return "home" if score["home"] > score["away"] else "away"


def best_plan(tasks):
    """Best action per task of one match, by backward induction over positions."""
    plan, value = {}, {position: 0.0 for position in POSITIONS}
    for t in sorted({task["t"] for task in tasks}, reverse=True):
        earlier = {}
        for task in tasks:
            if task["t"] != t:
                continue
            scoring = task["scoring"]
            total = {
                a: scoring["action_rewards"][a] + value[scoring["next_position"][a]]
                for a in task["legal_actions"]
            }
            plan[task["id"]] = max(total, key=total.get)
            earlier[task["position"]] = total[plan[task["id"]]]
        value = earlier
    return plan


def hindsight_best(tasks, rng):
    plan = best_plan(tasks)
    return lambda task: plan[task["id"]]


POLICIES = {
    "do_nothing": do_nothing,
    "random_legal": random_legal,
    "buy_favourite_and_hold": buy_and_hold(favourite),
    "buy_leader_else_draw_and_hold": buy_and_hold(leader_else_draw),
    "always_buy_home_and_hold": buy_and_hold(lambda task: "home"),
    "hindsight_best": hindsight_best,
}


def run_policy(tasks, name, config):
    def make(match):
        rng = random.Random(f"{config['seed']}:{name}:{match[0]['match_id']}")
        return POLICIES[name](match, rng)

    return replay(tasks, make, config)


def summarize(results, label, config):
    pnl = [cents(r["pnl"]) for r in results]
    low, high = bootstrap_ci(pnl, config["bootstrap_reps"], f"{config['seed']}:{label}")
    return {
        "matches": len(results),
        "mean_c": mean(pnl),
        "sd_c": sd(pnl),
        "ci_low_c": low,
        "ci_high_c": high,
        "trades": mean([float(r["trades"]) for r in results]),
        "cost_c": mean([cents(r["cost"]) for r in results]),
    }


def run_baselines(tasks, config, policies=None):
    """One row per group (split, plus demo matches) and policy."""
    rows = []
    for name in policies or POLICIES:
        results = run_policy(tasks, name, config)
        groups = [(s, [r for r in results if r["split"] == s]) for s in SPLITS]
        groups.append(("demo", [r for r in results if r["demo"]]))
        for group, members in groups:
            if members:
                label = f"{group}:{name}"
                row = summarize(members, label, config)
                rows.append({"group": group, "policy": name, **row})
    order = {group: i for i, group in enumerate(SPLITS + ("demo",))}
    return sorted(rows, key=lambda row: order[row["group"]])


def sensitivity(records, overrides, policies=None):
    """Baseline rows for each cost and delay setting, changed one at a time."""
    rows, seen = [], set()
    for key, values in SENSITIVITY.items():
        for value in values:
            config = make_config({**overrides, key: value})
            if config_hash(config) in seen:
                continue
            seen.add(config_hash(config))
            tasks, _ = build_tasks(records, config)
            setting = {k: config[k] for k in SENSITIVITY}
            for row in run_baselines(tasks, config, policies):
                rows.append({**setting, **row})
    return rows
