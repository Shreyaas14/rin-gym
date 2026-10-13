"""Every rule that can change lives in one dict of defaults."""

import copy
import hashlib
import json
import math
import re

OUTCOMES = ("home", "draw", "away")
POSITIONS = ("flat",) + OUTCOMES

DEFAULTS = {
    # clock
    "decisions": ["h1+15", "h1+30", "ht", "h2+15", "h2+30"],
    "pre_lead_s": 60,
    # timing
    "event_delay_s": 60,
    "price_max_age_s": 90,
    "max_sum_dev": 0.05,
    "fill_delay_s": 60,
    "fill_max_wait_s": 120,
    # costs
    "fee_rate": 0.05,
    "half_spread": 0.01,
    # reward
    "mark": "mid",
    "start_mark": "decision",
    "invalid_penalty": 0.04,
    "w_log": 0.0,
    "w_missed": 0.0,
    "prob_sum_tol": 0.05,
    # Optional forecast shaping; old task files remain usable in P&L-only mode.
    "forecast_rewards": False,
    "forecast_horizon_s": 300,
    "w_result": 0.1,
    "w_goal": 0.1,
    "w_price": 0.1,
    # prompt
    "show_kickoff_price": True,
    "football_rules": False,
    # feeds whose shot rows are only the misses: a save, a block and a goal are
    # rows of their own there, where other feeds log a shot row for every attempt
    "split_shot_competitions": ["2000000000"],
    "competitions": {
        "17": "FIFA World Cup 2026",
        "2000000000": "Premier League 2025/26",
    },
    # splits
    "train_competitions": ["2000000000"],
    "world_cup_competition": "17",
    "group_stage_name": "First Stage",
    "knockout_from_utc": "2026-06-28T12:00:00Z",
    "demo_match_ids": ["400021540", "400021528", "400021538"],
    # evaluation
    "seed": 20261012,
    "bootstrap_reps": 2000,
}
CHOICES = {"mark": ("mid", "liquidation"), "start_mark": ("decision", "fill")}
# applied when an answer is scored or a table is drawn, so not baked into tasks
SCORE_TIME = (
    "w_result",
    "w_goal",
    "w_price",
    "invalid_penalty",
    "w_log",
    "w_missed",
    "prob_sum_tol",
    "seed",
    "bootstrap_reps",
)


def expected(value, default):
    """None when an override has the type of its default, else what was wanted."""
    if type(default) is bool:
        return None if type(value) is bool else "true or false"
    if type(default) in (int, float):
        ok = type(value) in (int, float) and 0 <= value < math.inf
        return None if ok else "a non-negative number"
    if type(default) is str:
        return None if type(value) is str else "text"
    if type(default) is list:
        ok = type(value) is list and all(type(item) is str for item in value)
        return None if ok else "a list of strings"
    ok = type(value) is dict and all(type(item) is str for item in value.values())
    return None if ok else "an object of strings"


def make_config(overrides=None):
    config = copy.deepcopy(DEFAULTS)
    for key, value in (overrides or {}).items():
        if key not in DEFAULTS:
            raise ValueError("Unknown config key: " + key)
        wanted = (
            None
            if key == "fee_rate" and value == "market"
            else expected(value, DEFAULTS[key])
        )
        if wanted:
            raise ValueError(f"{key} must be {wanted}, got {value!r}")
        config[key] = copy.deepcopy(value)
    for key, allowed in CHOICES.items():
        if config[key] not in allowed:
            raise ValueError(key + " must be one of " + ", ".join(allowed))
    if (
        type(config["forecast_horizon_s"]) is not int
        or config["forecast_horizon_s"] <= 0
    ):
        raise ValueError("forecast_horizon_s must be a positive integer")
    decisions = config["decisions"]
    order = []
    for name in decisions:
        found = re.fullmatch(r"h([12])\+(\d+)", name)
        if name in ("pre", "ht"):
            order.append((-1, 0) if name == "pre" else (1, 0))
        elif found:
            order.append((0 if found[1] == "1" else 2, int(found[2])))
        else:
            raise ValueError("decisions: unknown decision name " + name)
    if not order or any(a >= b for a, b in zip(order, order[1:])):
        raise ValueError("decisions must be non-empty, unique and in time order")
    return config


def parse_overrides(pairs):
    """["half_spread=0.03", "mark=liquidation"] -> {"half_spread": 0.03, ...}"""
    out = {}
    for pair in pairs or []:
        key, sep, text = pair.partition("=")
        if not sep:
            raise ValueError("Expected KEY=VALUE, got " + pair)
        if type(DEFAULTS.get(key)) is str:
            out[key] = text  # a text setting is taken as typed, digits and all
            continue
        try:
            out[key] = json.loads(text)
        except ValueError:
            # True and False as Python spells them; anything else is text
            out[key] = {"true": True, "false": False}.get(text.lower(), text)
    return out


def config_hash(config):
    """Identifies the rules a task file was built with."""
    built = {k: v for k, v in config.items() if k not in SCORE_TIME}
    if not config["football_rules"]:
        built.pop("football_rules")
    if not config["forecast_rewards"]:
        built.pop("forecast_rewards")
        built.pop("forecast_horizon_s")
    text = json.dumps(built, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()[:12]
