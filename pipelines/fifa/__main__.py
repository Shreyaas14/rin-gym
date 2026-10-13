"""python -m pipelines.fifa: ingest, verify, or export shared Parquet data."""

import argparse
import json
from pathlib import Path
from .client import Client, COHORTS
from .dataset import ingest, verify
from .replay import export_replay, preview_existing_renderer


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    get = sub.add_parser("ingest", help="Publish a validated local Parquet snapshot")
    get.add_argument("--cohort", action="append", choices=COHORTS)
    get.add_argument(
        "--scope", action="append", default=[], metavar="COMPETITION:SEASON"
    )
    get.add_argument("--cache", type=Path, default=Path("data/raw/fifa"))
    get.add_argument("--output", type=Path, default=Path("data/processed/fifa"))
    get.add_argument("--import-cache", type=Path, action="append", default=[])
    get.add_argument("--workers", type=int, default=4)
    get.add_argument("--refresh", action="store_true")
    get.add_argument("--offline", action="store_true")
    get.add_argument("--allow-partial", action="store_true")
    check = sub.add_parser("verify")
    check.add_argument("--dataset", type=Path, default=Path("data/processed/fifa"))
    check.add_argument(
        "--cache", type=Path, help="Also verify source hashes and every original event"
    )
    export = sub.add_parser("export-replay")
    export.add_argument("--dataset", type=Path, default=Path("data/processed/fifa"))
    export.add_argument("--match", required=True)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--sanitize-locations", action="store_true")
    preview = sub.add_parser(
        "replay-preview", help="Smoke-test the existing fixed World Cup renderer"
    )
    preview.add_argument("--dataset", type=Path, default=Path("data/processed/fifa"))
    preview.add_argument("--renderer", type=Path, required=True)
    preview.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "verify":
        print(json.dumps(verify(args.dataset, args.cache), indent=2))
    elif args.command == "export-replay":
        print(
            export_replay(
                args.dataset, args.match, args.output, args.sanitize_locations
            )
        )
    elif args.command == "replay-preview":
        preview_existing_renderer(args.dataset, args.renderer, args.output)
    else:
        scopes = []
        for value in args.scope:
            pair = value.split(":")
            if len(pair) != 2 or not all(pair):
                parser.error("--scope requires COMPETITION:SEASON")
            scopes.append(tuple(pair))
        client = Client(args.cache, refresh=args.refresh, offline=args.offline)
        for directory in args.import_cache:
            print("Imported cached responses:", client.import_cache(directory))
        cohorts = args.cohort if args.cohort is not None else ([] if scopes else None)
        path, manifest = ingest(
            client, args.output, cohorts, args.workers, args.allow_partial, scopes
        )
        print(
            json.dumps(
                {
                    "snapshot": str(path.resolve()),
                    "coverage": manifest["coverage"],
                    "files": manifest["files"],
                    "quality_flags": manifest["match_quality_flags"],
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
