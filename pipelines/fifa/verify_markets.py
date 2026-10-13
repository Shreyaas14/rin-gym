"""Verify market snapshots, source hashes, identities, and backward replay joins."""

import argparse
from bisect import bisect_left
from collections import Counter, defaultdict
from functools import lru_cache
import gzip
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from .dataset import resolve_snapshot, verify
from .markets import epoch, validated_history
from .contracts import (
    POLICY_VERSION,
    SERIES,
    event_provenance,
    contract_provenance,
    validate_registry,
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def manifest(root):
    data = json.loads((root / "manifest.json").read_text())
    for name, info in data["files"].items():
        path = root / (name + ".parquet")
        require(
            hashlib.sha256(path.read_bytes()).hexdigest() == info["sha256"],
            "Parquet hash mismatch: " + str(path),
        )
        require(
            pq.read_metadata(path).num_rows == info["rows"],
            "Row count mismatch: " + str(path),
        )
    return data


def verify_prices(price_path, markets, cache, sources, windows):
    """Stream Parquet against raw token histories with a bounded response cache."""
    expected = {}
    request_tokens = defaultdict(set)
    for meta in sources:
        body = meta.get("request_body")
        tokens = (
            body.get("markets", [])
            if isinstance(body, dict)
            else parse_qs(urlsplit(meta["url"]).query).get("market", [])
        )
        request_tokens[meta["sha256"]].update(tokens)

    @lru_cache(maxsize=16)
    def response(digest):
        body = gzip.decompress((cache / (digest + ".json.gz")).read_bytes())
        require(hashlib.sha256(body).hexdigest() == digest, "Raw history hash mismatch")
        return json.loads(body)

    @lru_cache(maxsize=32)
    def raw_history(digest, token):
        require(token in request_tokens[digest], "History request token mismatch")
        history = response(digest).get("history")
        if isinstance(history, dict):
            history = history.get(token)
        return validated_history({"history": history})

    for market in markets:
        for token in market["tokens"]:
            if token["status"] == "included":
                tid = token["token_id"]
                require(tid not in expected, "Token reused by another contract")
                expected[tid] = (
                    market["match_id"],
                    market["market_id"],
                    token["outcome_index"],
                    token["outcome"],
                    token["points"],
                    token["in_game_points"],
                    token["history_sha256"],
                )
    counts, in_game, last = Counter(), Counter(), {}
    rows = 0
    for batch in pq.ParquetFile(price_path).iter_batches(batch_size=100000):
        table = pa.Table.from_batches([batch])
        require(
            all(c.null_count == 0 for c in table.columns), "Null price identity/value"
        )
        require(
            pc.all(pc.is_finite(table["price"])).as_py()
            and pc.all(pc.is_finite(table["timestamp"])).as_py(),
            "Nonfinite price/time",
        )
        require(
            pc.all(
                pc.and_(
                    pc.greater_equal(table["price"], 0),
                    pc.less_equal(table["price"], 1),
                )
            ).as_py(),
            "Price outside [0,1]",
        )
        data = table.to_pydict()
        for mid, kid, tid, oi, on, at, price, ig, sha in zip(
            *(
                data[k]
                for k in [
                    "match_id",
                    "market_id",
                    "token_id",
                    "outcome_index",
                    "outcome",
                    "timestamp",
                    "price",
                    "in_game",
                    "source_response_sha256",
                ]
            )
        ):
            require(tid in expected, "Orphan price token")
            identity = expected[tid]
            require(
                (mid, kid, oi, on) == identity[:4] and sha == identity[6],
                "Price token identity/provenance mismatch",
            )
            require(
                at > last.get(tid, float("-inf")),
                "Repeated or unordered token timestamp",
            )
            raw = raw_history(sha, tid)
            require(len(raw) == identity[4], "Raw history sample count mismatch")
            index = counts[tid]
            require(
                index < len(raw) and (at, price) == (raw[index]["t"], raw[index]["p"]),
                "Price sample differs from raw history",
            )
            require(mid in windows, "Missing source game window")
            kickoff, terminal = windows[mid]
            require(ig == (kickoff <= at <= terminal), "Incorrect in-game flag")
            last[tid] = at
            counts[tid] += 1
            in_game[tid] += int(ig)
        rows += len(table)
    require(set(counts) == set(expected), "Price token coverage mismatch")
    require(
        all(counts[t] == e[4] and in_game[t] == e[5] for t, e in expected.items()),
        "Token sample count mismatch",
    )
    return rows, len(counts)


def run(history_root, cache):
    history_root = resolve_snapshot(history_root)
    broad = manifest(history_root)
    linked_root = Path(broad["source_mapping_snapshot"])
    linked = manifest(linked_root)
    fifa = Path(linked["source_snapshot"])
    source_check = verify(fifa)
    require(
        hashlib.sha256((fifa / "manifest.json").read_bytes()).hexdigest()
        == linked["source_manifest_sha256"],
        "FIFA manifest changed",
    )
    require(
        hashlib.sha256(
            (linked_root / "fixture-contracts.parquet").read_bytes()
        ).hexdigest()
        == broad["source_contracts_sha256"],
        "Fixture catalog changed",
    )
    hashes = {m["sha256"] for m in linked["raw_sources"] + broad["raw_sources"]}
    for digest in hashes:
        require(
            hashlib.sha256(
                gzip.decompress((cache / (digest + ".json.gz")).read_bytes())
            ).hexdigest()
            == digest,
            "Raw response corrupt: " + digest,
        )
    require(
        linked.get("contract_policy_version") == POLICY_VERSION
        and broad.get("contract_policy_version") == POLICY_VERSION,
        "Missing current contract certification",
    )
    catalog_events, registry, registry_hashes = {}, {}, {}
    for meta in linked["raw_sources"]:
        if "/events?" not in meta["url"] and "/v2/tokens?" not in meta["url"]:
            continue
        payload = json.loads(
            gzip.decompress((cache / (meta["sha256"] + ".json.gz")).read_bytes())
        )
        if "/events?" in meta["url"]:
            for event in payload:
                catalog_events[str(event["id"])] = event
        else:
            for row in payload["data"]:
                registry[row["token_id"]] = row
                registry_hashes[row["token_id"]] = meta["sha256"]
    certified = pq.read_table(linked_root / "fixture-contracts.parquet").to_pylist()
    for contract in certified:
        event = catalog_events[contract["polymarket_event_id"]]
        market = json.loads(contract["source_market_json"])
        source_market = next(
            (m for m in event["markets"] if str(m["id"]) == contract["market_id"]), None
        )
        require(market == source_market, "Contract differs from raw catalog")
        comp = contract["competition_id"]
        require(
            event_provenance(comp, event) is None
            and contract_provenance(comp, event, market) is None,
            "Unapproved listing entered certified catalog",
        )
        require(
            validate_registry(market, event, registry) is None,
            "Token registry identity mismatch",
        )
        require(
            contract["condition_id"] == market["conditionId"]
            and contract["series_id"] == SERIES[comp]
            and contract["contract_policy_version"] == POLICY_VERSION,
            "Contract proof fields disagree",
        )
        tokens = json.loads(market["clobTokenIds"])
        require(
            contract["token_registry_sha256"]
            == sorted({registry_hashes[t] for t in tokens}),
            "Wrong registry provenance hash",
        )
    certified_ids = {c["market_id"] for c in certified}
    match_table = pq.read_table(linked_root / "matches.parquet")
    event_table = pq.read_table(linked_root / "events.parquet")
    for name, table in [("matches", match_table), ("events", event_table)]:
        original = pq.read_table(fifa / (name + ".parquet"))
        selected = original.filter(
            pc.is_in(
                original["match_id"], value_set=match_table["match_id"].combine_chunks()
            )
        )
        require(
            table.select(original.schema.names).equals(selected),
            "Original FIFA columns changed: " + name,
        )
    histories = {}
    for match in match_table.to_pylist():
        require(
            [m["outcome"] for m in match["prediction_markets"]]
            == ["home", "draw", "away"],
            "Bad HDA order",
        )
        for market in match["prediction_markets"]:
            digest = market["history_sha256"]
            raw = json.loads(
                gzip.decompress((cache / (digest + ".json.gz")).read_bytes())
            )["history"]
            histories[market["token_id"]] = ([p["t"] for p in raw], raw)
    usable = 0
    for event in event_table.to_pylist():
        usable += int(event["odds_usable"])
        for odds in event["market_odds"]:
            if odds["price_timestamp"] is None:
                require(not odds["fresh"], "Missing price marked fresh")
                continue
            timestamp = event["event_time_utc"].timestamp()
            times, raw = histories[odds["token_id"]]
            index = bisect_left(times, timestamp) - 1
            require(
                index >= 0
                and raw[index]["t"] == odds["price_timestamp"]
                and raw[index]["p"] == odds["price"],
                "Replay join is not latest strictly preceding sample",
            )
            require(
                odds["age_seconds"] == timestamp - odds["price_timestamp"],
                "Incorrect price age",
            )
            require(
                odds["fresh"]
                == (odds["age_seconds"] <= linked["max_price_age_seconds"]),
                "Incorrect freshness",
            )
        if event["odds_usable"]:
            require(
                event["period_code"] in (3, 5)
                and all(o["fresh"] for o in event["market_odds"]),
                "Invalid usable event",
            )
    markets = pq.read_table(history_root / "markets.parquet").to_pylist()
    require(
        all(m["market_id"] in certified_ids for m in markets),
        "Uncertified market entered price dataset",
    )
    fifa_events = pq.read_table(
        fifa / "events.parquet",
        columns=["match_id", "event_time_utc", "event_kind", "period_code"],
    ).to_pylist()
    times, starts = defaultdict(list), defaultdict(list)
    for event in fifa_events:
        if event["event_time_utc"] is None:
            continue
        at = epoch(event["event_time_utc"])
        times[event["match_id"]].append(at)
        if event["period_code"] == 3 and event["event_kind"] == "period_start":
            starts[event["match_id"]].append(at)
    windows = {
        mid: (anchors[0], max(times[mid]))
        for mid, anchors in starts.items()
        if len(anchors) == 1
    }
    rows, token_count = verify_prices(
        history_root / "prices.parquet", markets, cache, broad["raw_sources"], windows
    )
    audit = json.loads((history_root / "market-resolution.json").read_text())
    require(
        len(audit) == broad["coverage"]["raw_fixture_contracts"],
        "Missing contract audit rows",
    )
    require(
        {a["market_id"] for a in audit if a["status"] == "included"}
        == {m["market_id"] for m in markets},
        "Excluded contracts entered final table",
    )
    return dict(
        verified=True,
        source_matches=source_check["matches"],
        source_events=source_check["events"],
        retained_matches=len(match_table),
        retained_events=len(event_table),
        usable_replay_events=usable,
        final_markets=len(markets),
        certified_contracts=len(certified),
        contract_policy_version=POLICY_VERSION,
        price_tokens=token_count,
        price_points=rows,
        raw_response_hashes_verified=len(hashes),
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset", type=Path, default=Path("data/processed/fifa-market-history")
    )
    parser.add_argument("--cache", type=Path, default=Path("data/raw/polymarket"))
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    result = run(args.dataset, args.cache)
    if args.report:
        from .client import write_json

        write_json(args.report, result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
