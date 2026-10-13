"""python -m app.trade_env: build records and tasks, inspect them, run baselines."""

import argparse
import json
from pathlib import Path
import sys

from .baselines import POLICIES, SENSITIVITY, run_baselines, sensitivity
from .config import config_hash, make_config, parse_overrides
from .stats import SPLITS, prompt_sizes, single_step, step_summary, table
from .tasks import build_tasks, load_records, read_tasks, write_bytes, write_tasks

ROOT = Path("data/processed/trade_env")
BASELINE_COLUMNS = [
    "group",
    "policy",
    "matches",
    "mean_c",
    "sd_c",
    "ci_low_c",
    "ci_high_c",
    "trades",
    "cost_c",
]


def add_config(parser):
    parser.add_argument(
        "--set", action="append", default=[], metavar="KEY=VALUE", dest="overrides"
    )


def meta_path(output):
    """tasks.jsonl.gz -> tasks.meta.json, keeping any other dots in the name."""
    name = output.name
    for suffix in (".jsonl.gz", ".jsonl"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return output.with_name(name + ".meta.json")


def load_tasks(args, config):
    """Tasks from --records under the given config, else the built tasks file."""
    if args.records:
        return build_tasks(load_records(args.records), config)[0]
    tasks = read_tasks(args.tasks)
    built = {task["scoring"]["config_hash"] for task in tasks}
    if built != {config_hash(config)}:
        raise SystemExit("Tasks were built under another config; pass --records")
    return tasks


def show(args):
    for task in read_tasks(args.tasks):
        if task["id"] == args.task_id:
            for message in task["messages"]:
                print(f"--- {message['role']} ---")
                print(message["content"])
            if args.reveal:
                print("--- scoring (hidden from the model) ---")
                print(json.dumps(task["scoring"], indent=2))
            return
    raise SystemExit("No task " + args.task_id)


def print_stats(tasks):
    print(f"tasks: {len(tasks)}  matches: {len({t['match_id'] for t in tasks})}")
    sizes = prompt_sizes(tasks)
    rows = [{"decision": d, **sizes[d]} for d in sizes]
    print("\nprompt size in characters (system + user)")
    print(table(rows, ["decision", "tasks", "min", "mean", "max"]))
    for group in ("all",) + SPLITS:
        part = [t for t in tasks if group == "all" or t["split"] == group]
        if not part:
            continue
        summary = step_summary(part)
        print(
            f"\nsingle step, {group}: {summary['tasks']} tasks, a trade beats holding "
            f"still in {100 * summary['trade_beats_noop_share']:.1f}% of them, mean "
            f"missed {summary['mean_missed_noop_c']:.2f}c holding still and "
            f"{summary['mean_missed_random_c']:.2f}c choosing at random"
        )
        columns = ["position", "action", "tasks", "mean_c", "sd_c", "best_share"]
        print(table(single_step(part), columns))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["build-records"]:
        from .build_records import main as build_records

        return build_records(argv[1:])
    parser = argparse.ArgumentParser(
        prog="python -m app.trade_env", description=__doc__
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build-records", help="Fetch and join match records (data side)")
    build = sub.add_parser("build-tasks", help="Turn records into tasks.jsonl.gz")
    build.add_argument("--records", type=Path, default=ROOT / "records.jsonl.gz")
    build.add_argument("--output", type=Path, default=ROOT / "tasks.jsonl.gz")
    add_config(build)
    one = sub.add_parser("show", help="Print one task's prompt")
    one.add_argument("task_id", help="<match_id>:<decision>:<position>")
    one.add_argument("--tasks", type=Path, default=ROOT / "tasks.jsonl.gz")
    one.add_argument("--reveal", action="store_true", help="Also print the scoring")
    base = sub.add_parser("baselines", help="Replay the no-model policies")
    base.add_argument("--tasks", type=Path, default=ROOT / "tasks.jsonl.gz")
    base.add_argument("--records", type=Path, help="Rebuild tasks from records")
    base.add_argument("--policy", action="append", choices=POLICIES)
    base.add_argument(
        "--sensitivity",
        action="store_true",
        help="Vary " + ", ".join(SENSITIVITY) + " one at a time (needs --records)",
    )
    base.add_argument("--json", action="store_true")
    add_config(base)
    stat = sub.add_parser("stats", help="Prompt sizes and the single-step table")
    stat.add_argument("--tasks", type=Path, default=ROOT / "tasks.jsonl.gz")
    stat.add_argument("--records", type=Path, help="Rebuild tasks from records")
    add_config(stat)
    args = parser.parse_args(argv)
    if args.command == "show":
        return show(args)
    try:
        overrides = parse_overrides(args.overrides)
        config = make_config(overrides)
    except ValueError as error:
        parser.error(str(error))
    if args.command == "build-tasks":
        tasks, summary = build_tasks(load_records(args.records), config)
        write_tasks(args.output, tasks)
        meta = {"config": config, **summary}
        write_bytes(
            meta_path(args.output), (json.dumps(meta, indent=2) + "\n").encode()
        )
        print(json.dumps(summary, indent=2))
        print("Wrote", args.output)
    elif args.command == "stats":
        print_stats(load_tasks(args, config))
    elif args.sensitivity:
        if not args.records:
            parser.error("--sensitivity needs --records")
        rows = sensitivity(load_records(args.records), overrides, args.policy)
        columns = list(SENSITIVITY) + BASELINE_COLUMNS
        print(json.dumps(rows, indent=2) if args.json else table(rows, columns))
    else:
        rows = run_baselines(load_tasks(args, config), config, args.policy)
        print(
            json.dumps(rows, indent=2) if args.json else table(rows, BASELINE_COLUMNS)
        )


if __name__ == "__main__":
    main()
