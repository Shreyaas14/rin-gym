"""Legal actions, the reward of every action over one chunk, and scoring an answer."""

import math

from .config import OUTCOMES
from .costs import buy_cost, sell_proceeds
from .parse import parse

PROB_FLOOR = 1e-4


def legal_actions(position):
    if position == "flat":
        return ["buy_" + o for o in OUTCOMES] + ["do_nothing"]
    return ["keep", "close"] + ["switch_" + o for o in OUTCOMES if o != position]


def noop(position):
    return "do_nothing" if position == "flat" else "keep"


def next_position(position, action):
    if action in ("do_nothing", "keep"):
        return position
    if action == "close":
        return "flat"
    return action.split("_", 1)[1]


def action_table(position, start, fill, end, final, config):
    """Reward and cost paid for every legal action from one position.

    start: mids at the decision (where the last chunk was marked), fill: mids at the
    fill sample, end: mids at the chunk end, or the 1/0 settlement when final.
    """

    def value(outcome):
        if final or config["mark"] == "mid":
            return end[outcome]
        return sell_proceeds(end[outcome], config)

    def buy(outcome):
        return value(outcome) - buy_cost(fill[outcome], config)

    def spread_paid(outcome, side):
        if side == "buy":
            return buy_cost(fill[outcome], config) - fill[outcome]
        return fill[outcome] - sell_proceeds(fill[outcome], config)

    rewards, costs = {}, {}
    if position == "flat":
        for outcome in OUTCOMES:
            rewards["buy_" + outcome] = buy(outcome)
            costs["buy_" + outcome] = spread_paid(outcome, "buy")
        rewards["do_nothing"], costs["do_nothing"] = 0.0, 0.0
        return rewards, costs
    held = start[position] if config["start_mark"] == "decision" else fill[position]
    sold = sell_proceeds(fill[position], config) - held
    rewards["keep"], costs["keep"] = value(position) - held, 0.0
    rewards["close"], costs["close"] = sold, spread_paid(position, "sell")
    for outcome in OUTCOMES:
        if outcome != position:
            rewards["switch_" + outcome] = sold + buy(outcome)
            costs["switch_" + outcome] = costs["close"] + spread_paid(outcome, "buy")
    return rewards, costs


def trades(action):
    return {"buy": 1, "close": 1, "switch": 2}.get(action.split("_")[0], 0)


def clip(p):
    return min(1.0, max(PROB_FLOOR, p))


def log_score_edge(probs, market_probs, result):
    return math.log(clip(probs[result])) - math.log(clip(market_probs[result]))


def score_action(task, action, config, valid=True, probs=None, error=None):
    scoring = task["scoring"]
    pnl = scoring["action_rewards"][action]
    missed = max(scoring["action_rewards"].values()) - pnl
    edge = None
    if probs:
        edge = log_score_edge(probs, scoring["market_probs"], scoring["result"])
    reward = pnl + config["w_log"] * (edge or 0.0) - config["w_missed"] * missed
    if not valid:
        reward -= config["invalid_penalty"]
    return {
        "reward": reward,
        "pnl": pnl,
        "valid": valid,
        "error": error,
        "action": action,
        "next_position": scoring["next_position"][action],
        "probs": probs,
        "log_score_edge": edge,
        "missed": missed,
        "cost": scoring["action_costs"][action],
        "trades": trades(action),
    }


def score(task, completion, config):
    """Score one completion. An invalid answer holds still and pays the penalty."""
    parsed = parse(completion, task["legal_actions"], config["prob_sum_tol"])
    action = parsed["action"] if parsed["ok"] else noop(task["position"])
    return score_action(
        task, action, config, parsed["ok"], parsed["probs"], parsed["error"]
    )
