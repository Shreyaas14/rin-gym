"""Independently validate Kalshi Parquet identity, prices, lineage and raw hashes."""

import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from pipelines.fifa.dataset import resolve_snapshot
from .ingest import MARKETS, PRICE_SCHEMA, windows


def verify(dataset, fifa, cache=None):
    root = resolve_snapshot(dataset)
    source = resolve_snapshot(fifa)
    manifest = json.loads((root / "manifest.json").read_text())
    original = json.loads((source / "manifest.json").read_text())
    if manifest.get("venue") != "kalshi":
        raise ValueError("Not a Kalshi dataset")
    for name in ("matches", "events"):
        with (source / (name + ".parquet")).open("rb") as f:
            digest = hashlib.file_digest(f, "sha256").hexdigest()
        if (
            digest != original["files"][name]["sha256"]
            or digest != manifest["source_fifa_files"][name]
        ):
            raise ValueError("FIFA lineage mismatch")
    for name, schema in [("markets", MARKETS), ("prices", PRICE_SCHEMA)]:
        path = root / (name + ".parquet")
        with path.open("rb") as f:
            digest = hashlib.file_digest(f, "sha256").hexdigest()
        if digest != manifest["files"][name]["sha256"]:
            raise ValueError("Parquet hash mismatch")
        if pq.read_metadata(path).num_rows != manifest["files"][name]["rows"]:
            raise ValueError("Row count mismatch")
        if not pq.read_schema(path).equals(schema):
            raise ValueError("Schema mismatch")
    raw = {s["sha256"]: s for s in manifest["raw_sources"]}
    checked = 0
    if cache is not None:
        for digest, meta in raw.items():
            if meta.get("format") == "becker_kalshi_trades":
                with Path(meta["path"]).open("rb") as f:
                    actual = hashlib.file_digest(f, "sha256").hexdigest()
            else:
                actual = hashlib.sha256(
                    gzip.decompress((Path(cache) / meta["raw_file"]).read_bytes())
                ).hexdigest()
            if actual != digest:
                raise ValueError("Raw source hash mismatch")
            checked += 1
    fifa_matches = pq.read_table(source / "matches.parquet").to_pylist()
    win = windows(fifa_matches, pq.read_table(source / "events.parquet").to_pylist())
    lookup = {m["match_id"]: m for m in fifa_matches}
    tokens = []
    settlement = []
    hda = Counter()
    for market in pq.read_table(root / "markets.parquet").to_pylist():
        m = json.loads(market["source_market_json"])
        if (
            market["market_id"] != "kalshi:" + m["ticker"]
            or market["event_id"] != m["event_ticker"]
            or market["venue"] != "kalshi"
        ):
            raise ValueError("Contract identity mismatch")
        if market["match_id"] not in lookup or market["catalog_sha256"] not in raw:
            raise ValueError("Missing fixture/catalog provenance")
        if market["hda_outcome"]:
            hda[market["match_id"]] += 1
            f = lookup[market["match_id"]]
            home, away = f["regulation_home_score"], f["regulation_away_score"]
            result = "home" if home > away else "away" if away > home else "draw"
            if m.get("result") in ("yes", "no") and (m["result"] == "yes") != (
                result == market["hda_outcome"]
            ):
                settlement.append(m["ticker"])
        for i, side in enumerate(("Yes", "No")):
            token = market["tokens"][i]
            if (
                token["outcome"] != side
                or token["outcome_index"] != i
                or token["token_id"] != market["market_id"] + ":" + side.lower()
            ):
                raise ValueError("Logical outcome identity mismatch")
            tokens.append(
                dict(
                    match_id=market["match_id"],
                    market_id=market["market_id"],
                    token_id=token["token_id"],
                    outcome=side,
                    outcome_index=i,
                    points=token["points"],
                )
            )
    with duckdb.connect() as con:
        con.read_parquet(str(root / "prices.parquet")).create_view("prices")
        token_table = pa.Table.from_pylist(tokens)
        con.register("tokens", token_table)
        source_table = pa.table({"sha256": list(raw)})
        con.register("sources", source_table)
        window_table = pa.Table.from_pylist(
            [dict(match_id=mid, **w) for mid, w in win.items()]
        )
        con.register("windows", window_table)
        checks = {
            "duplicate logical outcomes": "SELECT count(*)-count(DISTINCT token_id) FROM tokens",
            "orphan prices": "SELECT count(*) FROM prices p ANTI JOIN tokens t USING(match_id,market_id,token_id,outcome,outcome_index)",
            "missing price provenance": "SELECT count(*) FROM prices p ANTI JOIN sources s ON p.source_response_sha256=s.sha256",
            "invalid prices": "SELECT count(*) FROM prices WHERE venue IS DISTINCT FROM 'kalshi' OR NOT isfinite(price) OR price IS NULL OR price<0 OR price>1 OR timestamp IS NULL OR NOT isfinite(timestamp)",
            "duplicate timestamps": "SELECT count(*) FROM (SELECT token_id,timestamp FROM prices GROUP BY ALL HAVING count(*)>1)",
            "wrong window": "SELECT count(*) FROM prices p JOIN windows w USING(match_id) WHERE timestamp<start_ts OR timestamp>end_ts OR in_game IS DISTINCT FROM (timestamp>=kickoff AND timestamp<=terminal)",
            "token counts": "SELECT count(*) FROM tokens t WHERE points != (SELECT count(*) FROM prices p WHERE p.token_id=t.token_id)",
        }
        for label, sql in checks.items():
            if con.execute(sql).fetchone()[0]:
                raise ValueError(label)
    return dict(
        verified=True,
        markets=manifest["files"]["markets"]["rows"],
        prices=manifest["files"]["prices"]["rows"],
        matches_with_hda=sum(n == 3 for n in hda.values()),
        raw_responses_verified=checked,
        settlement_disagreements=settlement,
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dataset", type=Path, default=Path("data/processed/fifa-kalshi"))
    p.add_argument("--fifa", type=Path, default=Path("data/processed/fifa"))
    p.add_argument("--cache", type=Path)
    args = p.parse_args()
    print(json.dumps(verify(args.dataset, args.fifa, args.cache), indent=2))


if __name__ == "__main__":
    main()
