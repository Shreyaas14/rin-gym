"""Fetch every available outcome of all fixture-linked market families.

Two sidecar tables (markets, prices) avoid multiplying the soccer event table by
thousands of props. This resolves fixtures and token outcomes, not player IDs or
provider-specific stat definitions. All original contracts stay in the audit.
"""

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlencode
import uuid

import pyarrow as pa
import pyarrow.parquet as pq

from .contracts import POLICY_VERSION
from .client import write_json
from .dataset import resolve_snapshot
from .markets import MarketClient, array, epoch, validated_history

TOKEN = pa.struct(
    [
        ("outcome_index", pa.int32()),
        ("outcome", pa.string()),
        ("token_id", pa.string()),
        ("status", pa.string()),
        ("reason", pa.string()),
        ("points", pa.int64()),
        ("in_game_points", pa.int64()),
        ("first_timestamp", pa.float64()),
        ("last_timestamp", pa.float64()),
        ("history_sha256", pa.string()),
    ]
)
PRICES = pa.schema(
    [
        ("match_id", pa.string()),
        ("market_id", pa.string()),
        ("token_id", pa.string()),
        ("outcome_index", pa.int32()),
        ("outcome", pa.string()),
        ("timestamp", pa.float64()),
        ("price", pa.float64()),
        ("in_game", pa.bool_()),
        ("source_response_sha256", pa.string()),
    ]
)


def build_requests(contracts, matches, events):
    windows = {}
    for match in matches:
        mid = match["match_id"]
        times = [epoch(e["event_time_utc"]) for e in events[mid] if e["event_time_utc"]]
        starts = [
            epoch(e["event_time_utc"])
            for e in events[mid]
            if e["event_kind"] == "period_start"
            and e["period_code"] == 3
            and e["event_time_utc"]
        ]
        if not times or len(starts) != 1:
            continue
        kickoff, end = starts[0], max(times)
        windows[mid] = dict(
            start_ts=int(min(epoch(match["scheduled_kickoff_utc"]), kickoff) - 3600),
            end_ts=int(end + 300),
            kickoff=kickoff,
            terminal=end,
        )
    tokens, grouped, excluded = {}, defaultdict(list), []
    for contract in contracts:
        market = json.loads(contract["source_market_json"])
        ids = array(market.get("clobTokenIds"))
        outcomes = array(market.get("outcomes"))
        reason = None
        if (
            not isinstance(ids, list)
            or not isinstance(outcomes, list)
            or not ids
            or len(ids) != len(outcomes)
            or not all(ids)
        ):
            reason = "no_complete_outcome_token_mapping"
        elif len(set(ids)) != len(ids):
            reason = "duplicate_token_ids"
        elif contract["match_id"] not in windows:
            reason = "no_valid_match_window"
        if reason:
            excluded.append(
                dict(
                    match_id=contract["match_id"],
                    market_id=contract["market_id"],
                    status="excluded",
                    reason=reason,
                    tokens=[],
                )
            )
            continue
        for index, (token, outcome) in enumerate(zip(ids, outcomes)):
            if token in tokens:
                raise ValueError("Token has multiple market owners: " + token)
            row = dict(
                match_id=contract["match_id"],
                market_id=contract["market_id"],
                outcome_index=index,
                outcome=str(outcome),
                token_id=str(token),
            )
            tokens[token] = row
            grouped[contract["match_id"]].append(row)
    requests = []
    for mid, rows in grouped.items():
        window = windows[mid]
        for i in range(0, len(rows), 20):
            batch = rows[i : i + 20]
            body = dict(
                markets=[r["token_id"] for r in batch],
                start_ts=window["start_ts"],
                end_ts=window["end_ts"],
                fidelity=1,
            )
            requests.append((batch, body, window))
    return requests, excluded


def download(client, request):
    batch, body, window = request
    try:
        response, meta = client.get(
            "https://clob.polymarket.com/batch-prices-history", json_body=body
        )
        histories = response.get("history")
        if not isinstance(histories, dict):
            raise ValueError("invalid_batch_history_response")
        if set(histories) - set(body["markets"]):
            raise ValueError("unexpected_batch_tokens")
    except (OSError, ValueError, URLError) as error:
        return (
            [],
            [
                row
                | dict(
                    status="excluded",
                    reason="request_error: " + str(error),
                    points=0,
                    in_game_points=0,
                    first_timestamp=None,
                    last_timestamp=None,
                    history_sha256=None,
                )
                for row in batch
            ],
            None,
        )
    prices, results, sources = [], [], [meta]
    for row in batch:
        result = row | dict(
            status="excluded",
            reason=None,
            points=0,
            in_game_points=0,
            first_timestamp=None,
            last_timestamp=None,
            history_sha256=meta["sha256"],
        )
        try:
            if row["token_id"] not in histories:
                # Batch APIs can omit assets. Verify the single-token endpoint
                # before calling a mapped contract unavailable.
                url = "https://clob.polymarket.com/prices-history?" + urlencode(
                    dict(
                        market=row["token_id"],
                        startTs=body["start_ts"],
                        endTs=body["end_ts"],
                        fidelity=body["fidelity"],
                    )
                )
                payload, token_meta = client.get(url)
                sources.append(token_meta)
            else:
                payload, token_meta = {"history": histories[row["token_id"]]}, meta
            result["history_sha256"] = token_meta["sha256"]
            history = validated_history(payload)
            if any(
                p["t"] < body["start_ts"] or p["t"] > body["end_ts"] for p in history
            ):
                raise ValueError("history_outside_requested_window")
            result.update(
                status="included",
                points=len(history),
                first_timestamp=float(history[0]["t"]),
                last_timestamp=float(history[-1]["t"]),
            )
            for point in history:
                in_game = window["kickoff"] <= point["t"] <= window["terminal"]
                result["in_game_points"] += int(in_game)
                prices.append(
                    row
                    | dict(
                        timestamp=float(point["t"]),
                        price=float(point["p"]),
                        in_game=in_game,
                        source_response_sha256=token_meta["sha256"],
                    )
                )
        except (OSError, URLError) as error:
            result["reason"] = "request_error: " + str(error)
        except (ValueError, TypeError, KeyError) as error:
            result["reason"] = str(error)
        results.append(result)
    return prices, results, sources


def run(args):
    source = resolve_snapshot(args.dataset)
    source_manifest = json.loads((source / "manifest.json").read_text())
    if source_manifest.get("contract_policy_version") != POLICY_VERSION:
        raise ValueError("Uncertified contract catalog; rerun pipelines.fifa.markets")
    info = source_manifest.get("files", {}).get("fixture-contracts", {})
    if hashlib.sha256(
        (source / "fixture-contracts.parquet").read_bytes()
    ).hexdigest() != info.get("sha256"):
        raise ValueError("Certified contract catalog hash mismatch")
    fifa = Path(source_manifest["source_snapshot"])
    contracts_table = pq.read_table(source / "fixture-contracts.parquet")
    contracts = contracts_table.to_pylist()
    matches = pq.read_table(fifa / "matches.parquet").to_pylist()
    events = defaultdict(list)
    for event in pq.read_table(
        fifa / "events.parquet",
        columns=["match_id", "event_time_utc", "event_kind", "period_code"],
    ).to_pylist():
        events[event["match_id"]].append(event)
    requests, excluded = build_requests(contracts, matches, events)
    version = (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        + "-"
        + uuid.uuid4().hex[:8]
    )
    out = args.output / "snapshots" / version
    out.mkdir(parents=True)
    client = MarketClient(args.cache, offline=args.offline, refresh=args.refresh)
    token_results, sources = [], []
    points = 0
    # Bounded submissions keep memory bounded even if an early HTTP call stalls.
    with pq.ParquetWriter(
        out / "prices.parquet", PRICES, compression="zstd"
    ) as writer, ThreadPoolExecutor(max_workers=args.workers) as pool:
        for start in range(0, len(requests), args.workers * 2):
            for prices, results, meta in pool.map(
                lambda r: download(client, r),
                requests[start : start + args.workers * 2],
            ):
                if prices:
                    writer.write_table(pa.Table.from_pylist(prices, schema=PRICES))
                    points += len(prices)
                token_results.extend(results)
                if meta:
                    sources.extend(meta)
            if start % (args.workers * 20) == 0 or start + args.workers * 2 >= len(
                requests
            ):
                print(
                    f"History batches {min(start+args.workers*2,len(requests))}/{len(requests)}; tokens {len(token_results)}; price points {points}",
                    flush=True,
                )
    by_market = defaultdict(list)
    for result in token_results:
        by_market[result["market_id"]].append(result)
    final, audit = [], list(excluded)
    for contract in contracts:
        results = by_market[contract["market_id"]]
        if not results:
            continue
        kept = [r for r in results if r["status"] == "included"]
        status = "included" if kept else "excluded"
        audit.append(
            dict(
                match_id=contract["match_id"],
                market_id=contract["market_id"],
                status=status,
                reason=None if kept else "no_usable_outcome_history",
                tokens=results,
            )
        )
        if kept:
            family = contract["sports_market_type"] or (
                "moneyline" if contract["hda_outcome"] else "untyped"
            )
            final.append(
                contract
                | dict(
                    market_family=family,
                    tokens=results,
                    all_outcomes_have_history=len(kept) == len(results),
                    entity_resolution_scope="fixture_and_outcome_token",
                )
            )
    schema = (
        contracts_table.schema.append(pa.field("market_family", pa.string()))
        .append(pa.field("tokens", pa.list_(TOKEN)))
        .append(pa.field("all_outcomes_have_history", pa.bool_()))
        .append(pa.field("entity_resolution_scope", pa.string()))
    )
    pq.write_table(
        pa.Table.from_pylist(final, schema=schema),
        out / "markets.parquet",
        compression="zstd",
    )
    write_json(out / "market-resolution.json", audit)
    files = {}
    for name in ("markets", "prices"):
        path = out / (name + ".parquet")
        files[name] = dict(
            rows=pq.read_metadata(path).num_rows,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            bytes=path.stat().st_size,
        )
    counts = dict(
        raw_fixture_contracts=len(contracts),
        final_markets=len(final),
        excluded_markets=len(contracts) - len(final),
        attempted_tokens=len(token_results),
        tokens_with_history=sum(t["status"] == "included" for t in token_results),
        tokens_with_in_game_history=sum(t["in_game_points"] > 0 for t in token_results),
        price_points=points,
        complete_outcome_markets=sum(m["all_outcomes_have_history"] for m in final),
        excluded_contract_reasons=dict(
            Counter(a["reason"] for a in audit if a["status"] == "excluded")
        ),
        token_exclusion_reasons=dict(
            Counter(t["reason"] for t in token_results if t["status"] != "included")
        ),
    )
    manifest = dict(
        dataset_version=version,
        source_mapping_snapshot=str(source.resolve()),
        contract_policy_version=POLICY_VERSION,
        source_contracts_sha256=hashlib.sha256(
            (source / "fixture-contracts.parquet").read_bytes()
        ).hexdigest(),
        source_fifa_snapshot=str(fifa.resolve()),
        files=files,
        coverage=counts,
        family_counts=dict(Counter(m["market_family"] for m in final)),
        raw_sources=sources,
        window="One hour before FIFA kickoff through five minutes after the last FIFA timeline event; fidelity=1 minute.",
        limitations=[
            "Price histories are sampled, not executable quotes.",
            "Fixture and token identities are resolved; player IDs, stat definitions, and supervised labels for props are not inferred.",
            "Partial-outcome markets are retained explicitly; inspect token statuses before use.",
            "Raw catalogs, empty responses, and rejected contracts are preserved.",
        ],
    )
    write_json(out / "manifest.json", manifest)
    # A transient transport error must not silently replace a complete snapshot.
    errors = [
        t for t in token_results if (t["reason"] or "").startswith("request_error:")
    ]
    if errors:
        raise RuntimeError(
            f"{len(errors)} token requests failed; raw cache and candidate snapshot retained, current pointer unchanged. Rerun to resume."
        )
    write_json(args.output / "current.json", dict(dataset_version=version))
    print(
        json.dumps(
            dict(
                snapshot=str(out.resolve()),
                coverage=counts,
                family_counts=manifest["family_counts"],
            ),
            indent=2,
        )
    )
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset", type=Path, default=Path("data/processed/fifa-markets")
    )
    parser.add_argument(
        "--output", type=Path, default=Path("data/processed/fifa-market-history")
    )
    parser.add_argument("--cache", type=Path, default=Path("data/raw/polymarket"))
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args(argv)
    if args.workers < 1:
        parser.error("--workers must be positive")
    run(args)


if __name__ == "__main__":
    main()
