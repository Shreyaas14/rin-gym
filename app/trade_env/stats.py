"""Summary statistics: whole-match bootstrap and the single-step reward table."""

import math
import random

from .config import POSITIONS
from .reward import noop

SPLITS = ("train", "val", "test")


def mean(values):
    # fsum: the same digits on every Python version
    return math.fsum(values) / len(values) if values else 0.0


def sd(values):
    if len(values) < 2:
        return 0.0
    centre = mean(values)
    return math.sqrt(math.fsum((v - centre) ** 2 for v in values) / (len(values) - 1))


def bootstrap_ci(values, reps, seed):
    """95% percentile interval of the mean, resampling whole matches."""
    if not values:
        return 0.0, 0.0
    rng = random.Random(seed)
    means = sorted(mean(rng.choices(values, k=len(values))) for _ in range(reps))
    return means[int(0.025 * (reps - 1))], means[int(math.ceil(0.975 * (reps - 1)))]


def cents(value):
    return 100 * value


def single_step(tasks):
    """Per position and action: tasks, mean reward, how often it is the best."""
    rows = []
    for position in POSITIONS:
        group = [
            t["scoring"]["action_rewards"] for t in tasks if t["position"] == position
        ]
        if not group:
            continue
        for action in group[0]:
            values = [rewards[action] for rewards in group]
            best = sum(rewards[action] == max(rewards.values()) for rewards in group)
            rows.append(
                {
                    "position": position,
                    "action": action,
                    "tasks": len(values),
                    "mean_c": cents(mean(values)),
                    "sd_c": cents(sd(values)),
                    "best_share": best / len(values),
                }
            )
    return rows


def step_summary(tasks):
    """Share of tasks where a trade beats holding still, and mean missed reward."""
    beats, missed_noop, missed_random = [], [], []
    for task in tasks:
        rewards = task["scoring"]["action_rewards"]
        still = rewards[noop(task["position"])]
        best = max(rewards.values())
        beats.append(best > still)
        missed_noop.append(best - still)
        missed_random.append(best - mean(list(rewards.values())))
    return {
        "tasks": len(tasks),
        "trade_beats_noop_share": mean(beats),
        "mean_missed_noop_c": cents(mean(missed_noop)),
        "mean_missed_random_c": cents(mean(missed_random)),
    }


def prompt_sizes(tasks):
    """Characters of system + user prompt per decision point."""
    sizes = {}
    for task in tasks:
        chars = sum(len(m["content"]) for m in task["messages"])
        sizes.setdefault(task["decision"], []).append(chars)
    return {
        decision: {
            "tasks": len(values),
            "min": min(values),
            "mean": round(mean(values)),
            "max": max(values),
        }
        for decision, values in sizes.items()
    }


def table(rows, columns):
    """Fixed-width text table; floats get two decimals."""
    cells = [
        [f"{row[c]:.2f}" if isinstance(row[c], float) else str(row[c]) for c in columns]
        for row in rows
    ]
    widths = [max(len(c), *(len(r[i]) for r in cells)) for i, c in enumerate(columns)]
    lines = ["  ".join(c.ljust(w) for c, w in zip(columns, widths))]
    for row in cells:
        lines.append(
            "  ".join(
                cell.ljust(w) if i == 0 else cell.rjust(w)
                for i, (cell, w) in enumerate(zip(row, widths))
            )
        )
    return "\n".join(lines)
