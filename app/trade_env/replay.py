"""Full-match replay: walk the decision points in order, carrying the position."""

import math

from .reward import score, score_action


def by_match(tasks):
    out = {}
    for task in tasks:
        out.setdefault(task["match_id"], []).append(task)
    return out


class Replay:
    """One position-carrying replay; policies may supply each answer asynchronously."""

    def __init__(self, tasks, config):
        self.at = {(t["t"], t["position"]): t for t in tasks}
        self.times = sorted({t["t"] for t in tasks})
        self.first, self.config = tasks[0], config
        self.position, self.steps = "flat", []

    def next_task(self):
        i = len(self.steps)
        return self.at[(self.times[i], self.position)] if i < len(self.times) else None

    def step(self, answer):
        task = self.next_task()
        if task is None:
            raise ValueError("Replay is complete")
        out = (
            score_action(task, answer, self.config)
            if answer in task["legal_actions"]
            else score(task, answer, self.config)
        )
        self.steps.append({"task_id": task["id"], "decision": task["decision"], **out})
        self.position = out["next_position"]

    def result(self):
        if self.next_task() is not None:
            raise ValueError("Replay is incomplete")
        return {
            **{k: self.first[k] for k in ("match_id", "split", "demo")},
            **{
                k: math.fsum(s[k] for s in self.steps)
                for k in ("pnl", "reward", "cost")
            },
            "trades": sum(s["trades"] for s in self.steps),
            "invalid": sum(not s["valid"] for s in self.steps),
            "final_position": self.position,
            "steps": self.steps,
        }


def replay_match(tasks, policy, config):
    replay = Replay(tasks, config)
    while (task := replay.next_task()) is not None:
        replay.step(policy(task))
    return replay.result()


def replay(tasks, make_policy, config):
    """Replay every match; make_policy(match_tasks) returns that match's policy."""
    return [
        replay_match(match, make_policy(match), config)
        for _, match in sorted(by_match(tasks).items())
    ]
