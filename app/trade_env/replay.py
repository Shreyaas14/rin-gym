"""Full-match replay: walk the decision points in order, carrying the position."""

import math

from .reward import score, score_action


def by_match(tasks):
    tasks = list(tasks)
    if len({task.get("market_venue", "polymarket") for task in tasks}) > 1:
        raise ValueError("Replay each market venue separately")
    out = {}
    for task in tasks:
        out.setdefault(task["match_id"], []).append(task)
    return out


def replay_match(tasks, policy, config):
    """Play one match's tasks with policy(task) -> a legal action or a completion.

    The step P&Ls add up to the match P&L: buys and sells at fill prices with
    costs, settlement at the end. Reward also carries penalties and shaping.
    """
    at = {(task["t"], task["position"]): task for task in tasks}
    position, steps = "flat", []
    for t in sorted({task["t"] for task in tasks}):
        task = at[(t, position)]
        answer = policy(task)
        if answer in task["legal_actions"]:
            step = score_action(task, answer, config)
        else:
            step = score(task, answer, config)
        steps.append({"task_id": task["id"], "decision": task["decision"], **step})
        position = step["next_position"]
    first = tasks[0]
    return {
        "match_id": first["match_id"],
        "split": first["split"],
        "demo": first["demo"],
        "pnl": math.fsum(s["pnl"] for s in steps),
        "reward": math.fsum(s["reward"] for s in steps),
        "trades": sum(s["trades"] for s in steps),
        "cost": math.fsum(s["cost"] for s in steps),
        "invalid": sum(not s["valid"] for s in steps),
        "final_position": position,
        "steps": steps,
    }


def replay(tasks, make_policy, config):
    """Replay every match; make_policy(match_tasks) returns that match's policy."""
    return [
        replay_match(match, make_policy(match), config)
        for _, match in sorted(by_match(tasks).items())
    ]
