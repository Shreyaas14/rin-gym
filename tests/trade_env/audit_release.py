"""Audit the local release; run with python -m tests.trade_env.audit_release.

Unlike unit tests this requires AK's full release and the rebuilt tasks file.
It does not call any external service or alter training data.
"""

import copy
import hashlib
import json
from pathlib import Path
import tempfile

from app.trade_env.clock import decision_points
from app.trade_env.config import make_config, POSITIONS
from app.trade_env.dataset import load_release
from app.trade_env.prompt import render
from app.trade_env.state import build_state, shown_prices
from app.trade_env.tasks import build_tasks, read_tasks, validate_tasks, write_tasks


def main():
    config = make_config()
    records = load_release()
    tasks, summary = build_tasks(records, config)
    validate_tasks(tasks)
    stored = Path("data/processed/trade_env/tasks.jsonl.gz")
    assert read_tasks(stored) == tasks, "Stored tasks differ from rebuilt tasks"
    with tempfile.TemporaryDirectory() as tmp:
        rebuilt = Path(tmp) / "tasks.jsonl.gz"
        write_tasks(rebuilt, tasks)
        assert rebuilt.read_bytes() == stored.read_bytes(), "Task bytes changed"
    checked = 0
    for record in records:
        points = decision_points(record["periods"], config)
        for k, point in enumerate(points):
            shown, reason = shown_prices(record["prices"], point["t"], config)
            if reason:
                continue
            other = copy.deepcopy(record)
            cutoff = point["t"] - config["event_delay_s"]
            for event in other["events"]:
                if event["t"] > cutoff:
                    event.update(
                        home_goals=99, away_goals=88, description="FUTURE_SENTINEL"
                    )
            other["regulation"] = {"result": "FUTURE_SENTINEL"}
            other["extra_time"] = "FUTURE_SENTINEL"
            for outcome, series in other["prices"].items():
                other["prices"][outcome] = [
                    [t, p if t <= point["t"] else 0.999] for t, p in series
                ]
            for name, t in other["periods"].items():
                if t > point["t"]:
                    other["periods"][name] = "FUTURE_SENTINEL"
            for position in POSITIONS:
                original = render(
                    build_state(record, points, k, position, shown, config), config
                )
                mutated = render(
                    build_state(other, points, k, position, shown, config), config
                )
                assert original == mutated, (
                    record["match_id"],
                    point["name"],
                    position,
                )
                checked += 1
    report = {
        "matches": len(records),
        "contracts_verified": len(records) * 3,
        "tasks": len(tasks),
        "prompt_future_mutations_checked": checked,
        "byte_identical_rebuild": True,
        "tasks_sha256": hashlib.sha256(stored.read_bytes()).hexdigest(),
        "matches_by_split": summary["matches_by_split"],
        "drops": summary["decision_points_dropped"],
        "limits": [
            "Event-time checks cannot establish historical publication time or remove later corrections.",
            "Task inclusion depends on future fill availability; this is retrospective sample selection.",
            "No remote optimizer or learned model was tested.",
        ],
    }
    output = Path("artifacts/trade-env-migration/rl-audit.json")
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
