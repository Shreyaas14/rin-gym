"""Ingest official Kalshi soccer markets into replay-compatible Parquet snapshots."""

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import uuid

import pyarrow as pa
import pyarrow.parquet as pq

from pipelines.fifa.client import write_json
from pipelines.fifa.dataset import resolve_snapshot, verify
from pipelines.fifa.market_history import TOKEN, PRICES
from pipelines.fifa.markets import CONTRACTS, epoch
from .client import KalshiClient
from .history import becker_trades, candles, number, trades
from .resolve import POLICY, family, resolve

MARKETS = (
    CONTRACTS.append(pa.field("market_family", pa.string()))
    .append(pa.field("tokens", pa.list_(TOKEN)))
    .append(pa.field("all_outcomes_have_history", pa.bool_()))
    .append(pa.field("entity_resolution_scope", pa.string()))
    .append(pa.field("venue", pa.string()))
    .append(pa.field("event_id", pa.string()))
)
PRICE_SCHEMA = PRICES.append(pa.field("venue", pa.string())).append(
    pa.field("price_type", pa.string())
)
for field in ("bid", "ask", "volume"):
    PRICE_SCHEMA = PRICE_SCHEMA.append(pa.field(field, pa.float64()))


def catalog(client, matches, selected_series=None):
    catalog, meta = client.request("series", category="Sports")
    series = [
        s
        for s in catalog["series"]
        if s["ticker"].startswith(("KXEPL", "KXWC")) and s["category"] == "Sports"
    ]
    if selected_series:
        if not set(selected_series) <= {s["ticker"] for s in series}:
            raise ValueError("Unknown or non-soccer Kalshi series")
        series = [s for s in series if s["ticker"] in selected_series]
    dates = [m["scheduled_kickoff_utc"].date() for m in matches]

    def stamp(date):
        return int(
            datetime.combine(date, datetime.min.time(), timezone.utc).timestamp()
        )

    lower, upper = (
        stamp(min(dates) - timedelta(days=14)),
        stamp(max(dates) + timedelta(days=28)),
    )
    rows, sources = [], [meta]
    for s in sorted(series, key=lambda s: s["ticker"]):
        count = 0
        for endpoint in ("historical/markets", "markets"):
            for page, metadata in client.pages(
                endpoint,
                "markets",
                series_ticker=s["ticker"],
                min_close_ts=lower,
                max_close_ts=upper,
            ):
                sources.append(metadata)
                for market in page:
                    rows.append(
                        dict(
                            market=market,
                            catalog_sha256=metadata["sha256"],
                            series=s["ticker"],
                            endpoint=endpoint,
                        )
                    )
                count += len(page)
        print(f"Catalog {s['ticker']}: {count}", flush=True)
    return rows, sources


def windows(matches, events):
    grouped = defaultdict(list)
    for event in events:
        grouped[event["match_id"]].append(event)
    result = {}
    for match in matches:
        rows = grouped[match["match_id"]]
        start = [
            epoch(e["event_time_utc"])
            for e in rows
            if e["event_kind"] == "period_start"
            and e["period_code"] == 3
            and e["event_time_utc"]
        ]
        times = [epoch(e["event_time_utc"]) for e in rows if e["event_time_utc"]]
        if len(start) == 1 and times:
            result[match["match_id"]] = dict(
                start_ts=int(
                    min(start[0], epoch(match["scheduled_kickoff_utc"])) - 3600
                ),
                end_ts=int(max(times) + 300),
                kickoff=start[0],
                terminal=max(times),
            )
    return result


def history(client, item, window, mode, cutoff, archive=None):
    m = item["entry"]["market"]
    ticker = m["ticker"]
    sources = []
    volume = number(m.get("volume_fp", m.get("volume")))
    if volume == 0:
        return [], sources
    if archive is not None:
        return trades(archive.get(ticker, []), ticker, window), sources
    if mode == "trades":
        payloads = []
        for endpoint, before, after in [
            ("historical/trades", None, cutoff),
            ("markets/trades", cutoff, None),
        ]:
            if (before is not None and window["end_ts"] < before) or (
                after is not None and window["start_ts"] >= after
            ):
                continue
            for page, meta in client.pages(
                endpoint,
                "trades",
                ticker=ticker,
                min_ts=window["start_ts"],
                max_ts=window["end_ts"],
            ):
                sources.append(meta)
                payloads.extend((row, meta["sha256"]) for row in page)
        return trades(payloads, ticker, window), sources
    if item["entry"]["endpoint"] == "historical/markets":
        endpoint = f"historical/markets/{ticker}/candlesticks"
    else:
        endpoint = f"series/{item['entry']['series']}/markets/{ticker}/candlesticks"
    payload, meta = client.request(
        endpoint,
        start_ts=window["start_ts"],
        end_ts=window["end_ts"],
        period_interval=1,
    )
    sources.append(meta)
    return candles(payload, ticker, window, meta["sha256"]), sources


def run(args):
    source = resolve_snapshot(args.dataset).resolve()
    verify(source)
    matches = pq.read_table(source / "matches.parquet").to_pylist()
    events = pq.read_table(source / "events.parquet").to_pylist()
    fifa_manifest = json.loads((source / "manifest.json").read_text())
    client = KalshiClient(args.cache, offline=args.offline, refresh=args.refresh)
    entries, sources = catalog(client, matches, getattr(args, "series", None))
    linked, audit = resolve(matches, entries)
    selected_matches = set(getattr(args, "match_id", None) or [])
    if selected_matches:
        if not selected_matches <= {m["match_id"] for m in matches}:
            raise ValueError("Unknown FIFA match ID")
        for item in linked:
            if item["match"]["match_id"] not in selected_matches:
                item["audit"].update(
                    status="excluded", reason="outside_selected_matches"
                )
        linked = [i for i in linked if i["match"]["match_id"] in selected_matches]
    version = (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        + "-"
        + uuid.uuid4().hex[:8]
    )
    out = args.output / "snapshots" / version
    out.mkdir(parents=True)
    write_json(out / "catalog-resolution.json", audit)
    write_json(
        out / "catalog-summary.json",
        dict(
            raw_contracts=len(entries),
            mapped=len(linked),
            reasons=dict(Counter(a["reason"] for a in audit if a["reason"])),
        ),
    )
    if args.catalog_only:
        print(
            json.dumps(
                dict(
                    snapshot=str(out),
                    mapped=len(linked),
                    reasons=dict(Counter(a["reason"] for a in audit if a["reason"])),
                ),
                indent=2,
            )
        )
        return out
    window = windows(matches, events)
    archive = None
    if args.becker_trades:
        archive, archive_sources = becker_trades(
            args.becker_trades, [i["entry"]["market"]["ticker"] for i in linked]
        )
        sources.extend(archive_sources)
    cutoff = 0
    if args.history == "trades" and archive is None:
        value, meta = client.request("historical/cutoff")
        sources.append(meta)
        cutoff = epoch(value["trades_created_ts"])
    # Keep H/D/A together in the import/audit and acquire it first.
    linked.sort(
        key=lambda i: (i["hda_outcome"] is None, i["entry"]["market"]["ticker"])
    )

    def fetch(item):
        w = window.get(item["match"]["match_id"])
        if not w:
            return item, [], [], "no_valid_match_window"
        rows, metadata = history(client, item, w, args.history, cutoff, archive)
        return item, rows, metadata, None if rows else "no_observed_prices"

    final = []
    price_rows = []
    points = 0
    included = Counter()
    # Failed requests propagate: raw cache/candidate snapshot remain, current does not change.
    with pq.ParquetWriter(
        out / "prices.parquet", PRICE_SCHEMA, compression="zstd"
    ) as writer:
        with ThreadPoolExecutor(args.workers) as pool:
            for index, (item, rows, metadata, reason) in enumerate(
                pool.map(fetch, linked), 1
            ):
                sources.extend(metadata)
                m = item["entry"]["market"]
                mid = item["match"]["match_id"]
                ticker = m["ticker"]
                item["audit"].update(
                    status="included" if rows else "excluded", reason=reason
                )
                if rows:
                    market_id = "kalshi:" + ticker
                    tokens = []
                    w = window[mid]
                    for n, side in enumerate(("yes", "no")):
                        token = market_id + ":" + side
                        for r in rows:
                            bid, ask = (
                                (r["bid"], r["ask"])
                                if side == "yes"
                                else (
                                    None if r["ask"] is None else 1 - r["ask"],
                                    None if r["bid"] is None else 1 - r["bid"],
                                )
                            )
                            price_rows.append(
                                dict(
                                    match_id=mid,
                                    market_id=market_id,
                                    token_id=token,
                                    outcome_index=n,
                                    outcome=side.title(),
                                    timestamp=r["timestamp"],
                                    price=r[side],
                                    in_game=w["kickoff"]
                                    <= r["timestamp"]
                                    <= w["terminal"],
                                    source_response_sha256=r["source_response_sha256"],
                                    venue="kalshi",
                                    price_type=r["price_type"],
                                    bid=bid,
                                    ask=ask,
                                    volume=r["volume"],
                                )
                            )
                        tokens.append(
                            dict(
                                outcome_index=n,
                                outcome=side.title(),
                                token_id=token,
                                status="included",
                                reason=None,
                                points=len(rows),
                                in_game_points=sum(
                                    w["kickoff"] <= r["timestamp"] <= w["terminal"]
                                    for r in rows
                                ),
                                first_timestamp=rows[0]["timestamp"],
                                last_timestamp=rows[-1]["timestamp"],
                                history_sha256=hashlib.sha256(
                                    json.dumps(rows, sort_keys=True).encode()
                                ).hexdigest(),
                            )
                        )
                    final.append(
                        dict(
                            match_id=mid,
                            competition_id=item["match"]["competition_id"],
                            market_id=market_id,
                            polymarket_event_id=None,
                            event_id=m["event_ticker"],
                            slug=ticker,
                            question=m["title"],
                            sports_market_type=family(item["entry"]["series"]),
                            market_family=family(item["entry"]["series"]),
                            hda_outcome=item["hda_outcome"],
                            kickoff_delta_seconds=None,
                            catalog_sha256=item["entry"]["catalog_sha256"],
                            source_market_json=json.dumps(m, sort_keys=True),
                            condition_id=None,
                            series_id=item["entry"]["series"],
                            contract_policy_version=POLICY,
                            token_registry_sha256=[],
                            tokens=tokens,
                            all_outcomes_have_history=True,
                            entity_resolution_scope="fixture_and_binary_outcome",
                            venue="kalshi",
                        )
                    )
                    included[mid] += bool(item["hda_outcome"])
                if len(price_rows) >= 50000:
                    writer.write_table(
                        pa.Table.from_pylist(price_rows, schema=PRICE_SCHEMA)
                    )
                    points += len(price_rows)
                    price_rows = []
                if index % 100 == 0 or index == len(linked):
                    print(
                        f"Histories {index}/{len(linked)}; priced contracts {len(final)}",
                        flush=True,
                    )
        if price_rows:
            writer.write_table(pa.Table.from_pylist(price_rows, schema=PRICE_SCHEMA))
            points += len(price_rows)
    if not final:
        raise ValueError(
            "No mapped contracts with observed prices; current snapshot unchanged"
        )
    pq.write_table(
        pa.Table.from_pylist(final, schema=MARKETS),
        out / "markets.parquet",
        compression="zstd",
    )
    write_json(out / "catalog-resolution.json", audit)
    write_json(
        out / "match-resolution.json",
        [
            dict(
                match_id=m["match_id"],
                status="included" if included[m["match_id"]] == 3 else "excluded",
                reason=None
                if included[m["match_id"]] == 3
                else "no_complete_priced_regulation_triplet",
            )
            for m in matches
        ],
    )
    files = {}
    for name in ("markets", "prices"):
        p = out / (name + ".parquet")
        files[name] = dict(
            rows=pq.read_metadata(p).num_rows,
            bytes=p.stat().st_size,
            sha256=hashlib.sha256(p.read_bytes()).hexdigest(),
        )
    coverage = dict(
        raw_contracts=len(entries),
        fixture_mapped_contracts=len(linked),
        priced_contracts=len(final),
        price_points=points,
        fifa_matches=len(matches),
        matches_with_complete_hda=sum(n == 3 for n in included.values()),
        exclusions=dict(Counter(a["reason"] for a in audit if a["reason"])),
    )
    manifest = dict(
        dataset_version=version,
        venue="kalshi",
        schema_version=1,
        contract_policy_version=POLICY,
        source_fifa_snapshot=os.path.relpath(source, out.resolve()),
        source_fifa_files={k: v["sha256"] for k, v in fifa_manifest["files"].items()},
        files=files,
        coverage=coverage,
        history_type="becker_trades" if archive is not None else args.history,
        selected_series=getattr(args, "series", None),
        selected_match_ids=sorted(selected_matches),
        source_reference="https://github.com/jon-becker/prediction-market-analysis",
        raw_sources=sources,
        price_semantics="Observed trades or completed one-minute trade closes; no forward fill; No is the complementary side of the same binary contract.",
        fixture_time_precision="Exact rule team pair and competition plus rule/ticker date matching FIFA UTC or US/Eastern date; no inferred kickoff timestamp.",
        limitations=[
            "Historical prices are not executable quotes. Candle timestamps are period ends, not original trade timestamps.",
            "Provider-specific props retain original rules; player/stat labels are not inferred.",
            "No zero-volume prices or previous-price forward fills. Market IDs and logical binary outcome IDs are namespaced.",
        ],
    )
    write_json(out / "manifest.json", manifest)
    write_json(args.output / "current.json", dict(dataset_version=version))
    print(json.dumps(dict(snapshot=str(out.resolve()), coverage=coverage), indent=2))
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dataset", type=Path, default=Path("data/processed/fifa"))
    p.add_argument("--output", type=Path, default=Path("data/processed/fifa-kalshi"))
    p.add_argument("--cache", type=Path, default=Path("data/raw/kalshi"))
    p.add_argument("--workers", type=int, default=6)
    scope = p.add_mutually_exclusive_group()
    scope.add_argument(
        "--series",
        action="append",
        help="Optional exact Kalshi series; repeat for multiple families",
    )
    scope.add_argument(
        "--all-market-types",
        action="store_true",
        help="Explicitly backfill all fixture-linked soccer series, including props; defaults to regulation home/draw/away",
    )
    p.add_argument(
        "--match-id",
        action="append",
        help="Optional FIFA fixture filter; repeat for multiple matches",
    )
    p.add_argument("--history", choices=["candles", "trades"], default="candles")
    p.add_argument(
        "--becker-trades",
        type=Path,
        help="Existing Becker data/kalshi/trades directory; uses observed trade histories",
    )
    p.add_argument("--catalog-only", action="store_true")
    p.add_argument("--offline", action="store_true")
    p.add_argument("--refresh", action="store_true")
    a = p.parse_args()
    if not a.series and not a.all_market_types:
        a.series = ["KXEPLGAME", "KXWCGAME"]
    if not 1 <= a.workers <= 16:
        p.error("--workers must be between 1 and 16")
    run(a)


if __name__ == "__main__":
    main()
