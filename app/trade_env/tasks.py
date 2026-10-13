"""Records to tasks: one task per match, decision point and held position."""

from collections import Counter
import gzip
import json
import math
import os
from pathlib import Path

from .clock import decision_points
from .config import OUTCOMES, POSITIONS, config_hash
from .prompt import render
from .reward import action_table, legal_actions, next_position
from .splits import is_demo, split_for
from .state import build_state, fill_prices, shown_prices

SCHEMA = "match_record/v1"


def _load_records(path):
    path = Path(path)
    if path.is_dir():
        return [json.loads(p.read_text()) for p in sorted(path.glob("*.json"))]
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        if path.suffix == ".json":
            data = json.load(handle)
            return data if isinstance(data, list) else [data]
        return [json.loads(line) for line in handle if line.strip()]


def load_records(path):
    """Records in a stable order. Uses the shared loader when the data side has one."""
    path = Path(path)
    try:
        from .records import load_records as shared
    except ImportError:
        shared = None
    records = _load_records(path) if shared is None or path.is_dir() else shared(path)
    for record in records:
        if record.get("schema") != SCHEMA:
            raise ValueError("Not a match_record/v1: " + str(record.get("match_id")))
    return sorted(records, key=lambda r: (r["scheduled_utc"], r["match_id"]))


def quotes(record, points, config):
    """Shown and fill prices per decision point, or the reason it cannot be used."""
    out = []
    for i, point in enumerate(points):
        later = points[i + 1 :]
        limit = later[0]["t"] if later else record["periods"]["h2_end"]
        shown, reason = shown_prices(record["prices"], point["t"], config)
        fill = None
        if shown:
            fill, reason = fill_prices(record["prices"], point["t"], limit, config)
        out.append({"shown": shown, "fill": fill, "reason": reason})
    return out


def make_task(record, point, position, messages, split, scoring, config):
    task = {
        "id": ("kalshi:" if record.get("market_venue") == "kalshi" else "")
        + f"{record['match_id']}:{point['name']}:{position}",
        "match_id": record["match_id"],
        "competition": record.get("competition_id"),
        "split": split,
        "demo": is_demo(record, config),
        "decision": point["name"],
        "decision_idx": point["idx"],
        "t": point["t"],
        "position": position,
        "messages": messages,
        "legal_actions": legal_actions(position),
        "scoring": scoring,
    }
    if "market_venue" in record:
        task["market_venue"] = record["market_venue"]
    return task


def match_tasks(record, config):
    """(tasks, drops) for one match; drops are (decision, reason) pairs.

    A chunk runs from a usable decision point to the next usable one, so the
    chunk-end mark is that point's shown price and a replay never has a gap.
    """
    points = decision_points(record["periods"], config)
    kept = {p["name"] for p in points}
    drops = [(d, "after_period_end") for d in config["decisions"] if d not in kept]
    split = split_for(record, config)
    if split is None:
        return [], drops + [(p["name"], "unknown_competition") for p in points]
    quoted = quotes(record, points, config)
    drops += [(p["name"], q["reason"]) for p, q in zip(points, quoted) if q["reason"]]
    usable = [i for i, q in enumerate(quoted) if not q["reason"]]
    result = record["regulation"]["result"]
    settlement = {o: float(o == result) for o in OUTCOMES}
    tasks = []
    for n, i in enumerate(usable):
        shown, fill = quoted[i]["shown"], quoted[i]["fill"]
        final = n + 1 == len(usable)
        end = settlement if final else quoted[usable[n + 1]]["shown"]
        total = math.fsum(shown.values())
        for position in POSITIONS:
            state = build_state(record, points, i, position, shown, config)
            rewards, costs = action_table(position, shown, fill, end, final, config)
            scoring = {
                "action_rewards": rewards,
                "next_position": {a: next_position(position, a) for a in rewards},
                "result": result,
                "market_probs": {o: shown[o] / total for o in OUTCOMES},
                "shown_prices": shown,
                "fill_prices": fill,
                "end_marks": end,
                "final_chunk": final,
                "config_hash": config_hash(config),
                "action_costs": costs,
                "chunk_end": "settlement" if final else points[usable[n + 1]]["name"],
                "observed_score": state["score"],
            }
            messages = render(state, config)
            tasks.append(
                make_task(record, points[i], position, messages, split, scoring, config)
            )
    return tasks, drops


def build_tasks(records, config):
    """All tasks plus a summary of what was kept and what was dropped, and why."""
    records = list(records)
    if len({record.get("market_venue", "polymarket") for record in records}) > 1:
        raise ValueError(
            "Build each market venue separately; mixed venue records are ambiguous"
        )
    tasks, dropped = [], Counter()
    matches = Counter()
    for record in records:
        made, drops = match_tasks(record, config)
        tasks += made
        dropped.update(reason for _, reason in drops)
        if made:
            matches[made[0]["split"]] += 1
    per_point = len(POSITIONS)
    summary = {
        "config_hash": config_hash(config),
        "records": len(records),
        "matches_with_tasks": sum(matches.values()),
        "matches_by_split": dict(sorted(matches.items())),
        "tasks": len(tasks),
        "tasks_by_split": dict(sorted(Counter(t["split"] for t in tasks).items())),
        "tasks_by_decision": dict(Counter(t["decision"] for t in tasks)),
        "decision_points_dropped": dict(sorted(dropped.items())),
        "tasks_dropped": {k: v * per_point for k, v in sorted(dropped.items())},
        "demo_matches": sorted({t["match_id"] for t in tasks if t["demo"]}),
    }
    if records and "market_venue" in records[0]:
        summary["market_venue"] = records[0]["market_venue"]
    return tasks, summary


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def write_bytes(path, body):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    temporary.write_bytes(body)
    os.replace(temporary, path)


def write_tasks(path, tasks):
    """One task per line; the same tasks always give the same bytes."""
    body = "".join(dumps(task) + "\n" for task in tasks).encode()
    if Path(path).suffix == ".gz":
        body = gzip.compress(body, mtime=0)
    write_bytes(path, body)


def read_tasks(path):
    opener = gzip.open if Path(path).suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]
