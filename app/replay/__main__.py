"""Import FIFA/odds Parquet into DuckDB and render a selected match offline."""

import argparse
import json
import duckdb
from pathlib import Path

from .store import import_database, list_matches, load_match


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    ingest = commands.add_parser(
        "import", help="Materialize Parquet snapshots in DuckDB"
    )
    ingest.add_argument(
        "--dataset",
        type=Path,
        required=True,
        help="FIFA source snapshot directory or current.json parent",
    )
    ingest.add_argument(
        "--markets",
        type=Path,
        help="All-family markets/prices Parquet snapshot directory",
    )
    ingest.add_argument("--database", type=Path, required=True)
    ingest.add_argument("--replace", action="store_true")
    export = commands.add_parser(
        "export-records",
        help="Export mapped H/D/A histories to trade_env match records",
    )
    export.add_argument("--database", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)
    audit = commands.add_parser(
        "verify", help="Check replay states for every imported match"
    )
    audit.add_argument("--database", type=Path, required=True)
    listing = commands.add_parser("list-matches")
    listing.add_argument("--database", type=Path, required=True)
    listing.add_argument("--search")
    listing.add_argument("--limit", type=int, default=30)
    render = commands.add_parser("render")
    render.add_argument("--database", type=Path, required=True)
    render.add_argument("--match", required=True)
    render.add_argument("--output", type=Path, required=True)
    render.add_argument("--preview", action="store_true")
    render.add_argument("--fps", type=int, default=24)
    render.add_argument(
        "--max-seconds", type=float, help="Optional truncated video for smoke testing"
    )
    args = parser.parse_args(argv)
    try:
        if args.command == "import":
            result = import_database(
                args.dataset, args.database, args.markets, args.replace
            )
        elif args.command == "export-records":
            from .export import export_records

            result = export_records(args.database, args.output)
        elif args.command == "verify":
            from .audit import verify_database

            result = verify_database(args.database)
        elif args.command == "list-matches":
            if args.limit < 1:
                raise ValueError("--limit must be positive")
            result = list_matches(args.database, args.search, args.limit)
        else:
            from .model import Replay
            from .render import render as render_match

            result = render_match(
                Replay(load_match(args.database, args.match)),
                args.output,
                args.preview,
                args.fps,
                args.max_seconds,
            )
        print(json.dumps(result, indent=2, default=str))
    except (ValueError, FileNotFoundError, RuntimeError, duckdb.Error) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
