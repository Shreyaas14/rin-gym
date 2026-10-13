"""Build match_record/v1 files: FIFA events joined to Polymarket 1X2 YES prices."""

import argparse
import bisect
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import contextlib
from datetime import datetime
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import time
import unicodedata

from pipelines.fifa.client import COHORTS, Client, atomic_bytes

from .records import OUTCOMES, SCHEMA, result_of, write_records

GAMMA = "https://gamma-api.polymarket.com/"
CLOB = "https://clob.polymarket.com/"
CATALOGS = {
    "world-cup-2026": "tag_id=102232",
    "premier-league-2025-26": "series_id=10188",
}
PAGE = 100
START_TOLERANCE = 3600
PRICE_LEAD, PRICE_TAIL = 3600, 120
COVERAGE_RULE = (
    "in-play minutes are a 60 s grid from h1_start+120 s to h2_end, halftime "
    "skipped; a minute is fresh when all three YES prices are at most 90 s old; "
    "price_sum is the sum of the three raw prices over fresh minutes"
)
# Polymarket spelling -> FIFA spelling, both already reduced by team_key().
ALIASES = {
    "unitedstates": "usa",
    "drcongo": "congodr",
    "bosniaherzegovina": "bosniaandherzegovina",
    "brighton": "brightonandhovealbion",
    "tottenham": "tottenhamhotspur",
    "newcastle": "newcastleunited",
    "westham": "westhamunited",
    "wolves": "wolverhamptonwanderers",
}
MARKET_FIELDS = (
    "id",
    "question",
    "outcomes",
    "outcomePrices",
    "clobTokenIds",
    "gameStartTime",
)
EVENT_COLUMNS = [
    "match_id",
    "source_index",
    "event_time_utc",
    "period_code",
    "match_minute_raw",
    "event_type_label",
    "event_kind",
    "team_id",
    "player_id",
    "home_score",
    "away_score",
    "x_raw",
    "y_raw",
    "event_description",
    "source_event_json",
]


class Excluded(ValueError):
    """A match that cannot become a record; the message is the reported reason."""


def curl(url, timeout=45):
    # urllib loses responses behind some proxies (IncompleteRead); curl does not.
    command = ["curl", "-sS", "--fail", "--compressed", "-m", str(timeout)]
    command += ["-H", "User-Agent: rin-gym-trade-env/1.0"]
    command += ["-H", "Accept: application/json", url]
    for attempt in range(4):
        result = subprocess.run(command, capture_output=True)
        if result.returncode == 0:
            return result.stdout
        if attempt < 3:
            time.sleep(1 + attempt)
    error = result.stderr.decode(errors="replace").strip()[:200]
    raise OSError("curl failed: " + url + " " + error)


@contextlib.contextmanager
def curl_opener(request, timeout=45):
    yield io.BytesIO(curl(request.full_url, timeout))


def cached_json(directory, name, url, offline=False):
    """One URL per name: <name>.json.gz holds the body, <name>.url the request."""
    path, source = directory / (name + ".json.gz"), directory / (name + ".url")
    if path.exists() and source.exists() and source.read_text() == url:
        return json.loads(gzip.decompress(path.read_bytes()))
    if offline:
        raise FileNotFoundError("Uncached URL in offline mode: " + url)
    body = curl(url)
    data = json.loads(body)
    atomic_bytes(path, gzip.compress(body, mtime=0))
    atomic_bytes(source, url.encode())
    return data


def epoch(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def team_key(name):
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    text = re.sub(r"\b(fc|afc)\b", "", text.casefold().replace("&", "and"))
    key = re.sub(r"[^a-z0-9]", "", text)
    return ALIASES.get(key, key)


def ensure_dataset(dataset, cache, offline=False, workers=4):
    from pipelines.fifa.dataset import ingest, resolve_snapshot

    if not (resolve_snapshot(dataset) / "matches.parquet").exists():
        client = Client(cache, offline=offline, opener=curl_opener)
        ingest(client, dataset, workers=workers)
    return resolve_snapshot(dataset)


def load_matches(snapshot, competition_ids):
    """(match row, events in source order) per match, in kickoff order."""
    import pyarrow.parquet as pq

    filters = [("source", "=", "fifa"), ("competition_id", "in", competition_ids)]
    matches = pq.read_table(snapshot / "matches.parquet", filters=filters).to_pylist()
    events = {}
    for row in pq.read_table(
        snapshot / "events.parquet", columns=EVENT_COLUMNS, filters=filters
    ).to_pylist():
        events.setdefault(row["match_id"], []).append(row)
    for rows in events.values():
        rows.sort(key=lambda row: row["source_index"])
    matches.sort(key=lambda m: (m["scheduled_kickoff_utc"], m["match_id"]))
    return [(m, events.get(m["match_id"], [])) for m in matches]


def match_record(match, rows):
    """The FIFA half of a record: metadata, periods, regulation result, events."""
    if match["timeline_status"] != "ok" or len(rows) != match["event_count"]:
        raise Excluded("no complete FIFA timeline (" + match["timeline_status"] + ")")
    if any(row["event_time_utc"] is None for row in rows):
        raise Excluded("FIFA event without a timestamp")
    side = {match["home_team_id"]: "home", match["away_team_id"]: "away"}
    events = [
        {
            "t": row["event_time_utc"].timestamp(),
            "utc": json.loads(row["source_event_json"])["Timestamp"],
            "seq": row["source_index"],
            "period": row["period_code"],
            "minute": row["match_minute_raw"] or None,
            "type": row["event_type_label"],
            "kind": row["event_kind"],
            "team": side.get(row["team_id"]),
            "team_id": row["team_id"],
            "player_id": row["player_id"],
            "home_goals": row["home_score"],
            "away_goals": row["away_score"],
            "x": row["x_raw"],
            "y": row["y_raw"],
            "description": row["event_description"],
        }
        for row in rows
    ]

    def boundary(kind, period):
        found = [e for e in events if e["kind"] == kind and e["period"] == period]
        if len(found) != 1:
            raise Excluded(f"{len(found)} {kind} rows in period {period}, need 1")
        return found[0]

    whistle = boundary("period_end", 5)
    periods = {
        "h1_start": boundary("period_start", 3)["t"],
        "h1_end": boundary("period_end", 3)["t"],
        "h2_start": boundary("period_start", 5)["t"],
        "h2_end": whistle["t"],
    }
    goals = (whistle["home_goals"], whistle["away_goals"])
    extra_time = any(e["period"] in (7, 9) for e in events)
    if (*goals, extra_time) != (
        match["regulation_home_score"],
        match["regulation_away_score"],
        match["went_to_extra_time"],
    ):
        raise Excluded("timeline regulation score disagrees with the matches table")
    return {
        "schema": SCHEMA,
        "match_id": match["match_id"],
        "source": "fifa",
        "competition_id": match["competition_id"],
        "home": match["home_team_name"],
        "away": match["away_team_name"],
        "home_team_id": match["home_team_id"],
        "away_team_id": match["away_team_id"],
        "scheduled_utc": match["scheduled_kickoff_utc"].strftime("%Y-%m-%dT%H:%M:%SZ"),
        "periods": periods,
        "regulation": {
            "home_goals": goals[0],
            "away_goals": goals[1],
            "result": result_of(*goals),
        },
        "extra_time": extra_time,
        "stage_name": match["stage_name"],
        # Regulation only: extra-time and shootout rows are dropped.
        "events": [e for e in events if e["t"] <= periods["h2_end"]],
    }


def catalog(cache, query, offline=False):
    """Closed Gamma events for one tag or series, grouped by (home, away) key."""
    pairs = {}
    for offset in range(0, 100 * PAGE, PAGE):
        url = f"{GAMMA}events?{query}&closed=true&limit={PAGE}&offset={offset}"
        name = query.replace("=", "_") + "_" + str(offset)
        page = cached_json(cache / "gamma", name, url, offline)
        for event in page:
            names = (event.get("title") or "").split(" vs. ")
            if len(names) == 2:
                slim = {
                    "id": event["id"],
                    "slug": event["slug"],
                    "markets": [
                        {key: m.get(key) for key in MARKET_FIELDS}
                        for m in event.get("markets") or []
                    ],
                }
                pair = (team_key(names[0]), team_key(names[1]))
                pairs.setdefault(pair, {})[event["id"]] = slim
        if len(page) < PAGE:
            return {pair: list(events.values()) for pair, events in pairs.items()}
    raise ValueError("Gamma catalog exceeded 100 pages: " + query)


def find_markets(record, events):
    """Pick the match's Polymarket event and its home/draw/away YES tokens."""
    kickoff = epoch(record["scheduled_utc"])
    pair = (team_key(record["home"]), team_key(record["away"]))
    found = [
        event
        for event in events.get(pair, [])
        if any(
            m["gameStartTime"]
            and abs(epoch(m["gameStartTime"]) - kickoff) <= START_TOLERANCE
            for m in event["markets"]
        )
    ]
    if len(found) != 1:
        raise Excluded(f"{len(found)} Polymarket events match the teams and kickoff")
    event = found[0]
    sides = {pair[0]: "home", pair[1]: "away"}
    markets = {}
    for market in event["markets"]:
        if json.loads(market["outcomes"] or "[]") != ["Yes", "No"]:
            continue
        question = market["question"]
        winner = re.fullmatch(r"Will (.+) win on \d{4}-\d{2}-\d{2}\?", question)
        if question.endswith(" end in a draw?"):
            outcome = "draw"
        else:
            outcome = sides.get(team_key(winner[1])) if winner else None
        markets.setdefault(outcome, []).append(market)
    if set(markets) != set(OUTCOMES) or any(len(m) != 1 for m in markets.values()):
        counts = {str(key): len(value) for key, value in markets.items()}
        raise Excluded(
            f"{event['slug']} is not exactly three home/draw/away Yes/No markets: {counts}"
        )
    settled = [
        float(json.loads(markets[outcome][0]["outcomePrices"] or "[NaN]")[0])
        for outcome in OUTCOMES
    ]
    result = record["regulation"]["result"]
    if settled != [float(outcome == result) for outcome in OUTCOMES]:
        raise Excluded(
            f"{event['slug']} settled home/draw/away at {settled}, "
            f"regulation result is {result}"
        )
    return {"slug": event["slug"]} | {
        outcome: {
            "market_id": markets[outcome][0]["id"],
            "token_id": json.loads(markets[outcome][0]["clobTokenIds"])[0],
        }
        for outcome in OUTCOMES
    }


def fetch_prices(cache, record, markets, offline=False):
    """1-minute YES histories over [scheduled - 3600, h2_end + 120], raw."""
    start = int(epoch(record["scheduled_utc"]) - PRICE_LEAD)
    end = int(record["periods"]["h2_end"] + PRICE_TAIL)
    prices = {}
    for outcome in OUTCOMES:
        market = markets[outcome]
        url = (
            f"{CLOB}prices-history?market={market['token_id']}"
            f"&startTs={start}&endTs={end}&fidelity=1"
        )
        name = "history_" + market["market_id"]
        history = cached_json(cache / "prices", name, url, offline)["history"]
        series = [[point["t"], point["p"]] for point in history]
        if not series:
            raise Excluded("empty price history for " + outcome)
        if any(a[0] >= b[0] for a, b in zip(series, series[1:])) or any(
            not 0 <= p <= 1 for _, p in series
        ):
            raise Excluded("malformed price history for " + outcome)
        prices[outcome] = series
    return prices


def build_record(match, rows, events, cache, offline=False):
    record = match_record(match, rows)
    markets = find_markets(record, events)
    record["prices"] = fetch_prices(cache, record, markets, offline)
    record["markets"] = markets
    return record


def price_coverage(record, max_age=90):
    """(in-play minutes, sums of the three prices at the fresh ones)."""
    periods, prices = record["periods"], record["prices"]
    times = {o: [t for t, _ in prices[o]] for o in OUTCOMES}
    minutes, sums = 0, []
    for t in range(int(periods["h1_start"]) + 120, int(periods["h2_end"]), 60):
        if periods["h1_end"] < t < periods["h2_start"]:
            continue
        minutes += 1
        at = {o: bisect.bisect_right(times[o], t) - 1 for o in OUTCOMES}
        if all(at[o] >= 0 and t - times[o][at[o]] <= max_age for o in OUTCOMES):
            sums.append(sum(prices[o][at[o]][1] for o in OUTCOMES))
    return minutes, sums


def summarize(matches, records, exclusions):
    competitions = {}
    for cohort, (competition_id, _, _) in COHORTS.items():
        total = sum(m["competition_id"] == competition_id for m, _ in matches)
        if not total:
            continue
        kept = [r for r in records if r["competition_id"] == competition_id]
        coverage = [(r["match_id"], *price_coverage(r)) for r in kept]
        minutes = sum(n for _, n, _ in coverage)
        sums = [value for _, _, values in coverage for value in values]
        share = [(len(values) / n, match_id) for match_id, n, values in coverage if n]
        competitions[competition_id] = {
            "cohort": cohort,
            "matches": total,
            "records": len(kept),
            "excluded": total - len(kept),
            "results": dict(Counter(r["regulation"]["result"] for r in kept)),
            "extra_time": sum(r["extra_time"] for r in kept),
            "stages": dict(Counter(r["stage_name"] for r in kept)),
            "events": sum(len(r["events"]) for r in kept),
            "price_points": sum(len(r["prices"][o]) for r in kept for o in OUTCOMES),
            "price_coverage": {
                "in_play_minutes": minutes,
                "fresh_minutes": len(sums),
                "fresh_share": round(len(sums) / minutes, 6) if minutes else None,
                "matches_fully_fresh": sum(s == 1 for s, _ in share),
                "lowest_match_share": (
                    {"match_id": min(share)[1], "fresh_share": round(min(share)[0], 6)}
                    if share
                    else None
                ),
                "price_sum_min": round(min(sums), 6) if sums else None,
                "price_sum_max": round(max(sums), 6) if sums else None,
            },
        }
    return {
        "schema": SCHEMA,
        "matches": len(matches),
        "records": len(records),
        "excluded": len(exclusions),
        "competitions": competitions,
        "exclusions": exclusions,
        "price_coverage_rule": COVERAGE_RULE,
    }


def build(dataset, cache, cohorts=None, offline=False, workers=4):
    """Return (records, summary) for the chosen cohorts, in kickoff order."""
    cohorts = list(cohorts or COHORTS)
    snapshot = ensure_dataset(dataset, cache / "fifa", offline, workers)
    matches = load_matches(snapshot, [COHORTS[name][0] for name in cohorts])
    events = {
        COHORTS[name][0]: catalog(cache, CATALOGS[name], offline) for name in cohorts
    }

    def one(item):
        match, rows = item
        try:
            catalog_events = events[match["competition_id"]]
            return build_record(match, rows, catalog_events, cache, offline), None
        except Excluded as error:
            return None, {
                "match_id": match["match_id"],
                "competition_id": match["competition_id"],
                "home": match["home_team_name"],
                "away": match["away_team_name"],
                "scheduled_utc": match["scheduled_kickoff_utc"].strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                "reason": str(error),
            }

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(one, matches))
    records = [record for record, _ in results if record]
    exclusions = [excluded for _, excluded in results if excluded]
    summary = summarize(matches, records, exclusions)
    summary["dataset_version"] = snapshot.name
    return records, summary


def add_arguments(parser):
    parser.add_argument("--cohort", action="append", choices=COHORTS)
    parser.add_argument(
        "--cache",
        type=Path,
        default=Path("data/raw/trade_env"),
        help="Raw responses: fifa/, gamma/ and prices/",
    )
    parser.add_argument(
        "--dataset", type=Path, help="FIFA Parquet root (default: CACHE/fifa-parquet)"
    )
    parser.add_argument(
        "--output", type=Path, default=Path("data/processed/trade_env/records.jsonl.gz")
    )
    parser.add_argument(
        "--summary", type=Path, help="Default: <output stem>-summary.json beside it"
    )
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--offline", action="store_true", help="Fail instead of fetching anything"
    )


def run(args):
    dataset = args.dataset or args.cache / "fifa-parquet"
    records, summary = build(
        dataset, args.cache, args.cohort, args.offline, args.workers
    )
    count = write_records(args.output, records)
    body = args.output.read_bytes()
    lines = gzip.decompress(body) if args.output.suffix == ".gz" else body
    summary["output"] = {
        "file": args.output.name,
        "records": count,
        "bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "jsonl_sha256": hashlib.sha256(lines).hexdigest(),
    }
    stem = args.output.name.split(".")[0]
    path = args.summary or args.output.with_name(stem + "-summary.json")
    path.write_text(json.dumps(summary, indent=2) + "\n")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "records": count,
                "excluded": summary["excluded"],
                "competitions": {
                    key: {k: c[k] for k in ("cohort", "matches", "records", "excluded")}
                    for key, c in summary["competitions"].items()
                },
            },
            indent=2,
        )
    )
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -m app.trade_env build-records", description=__doc__
    )
    add_arguments(parser)
    run(parser.parse_args(argv))


if __name__ == "__main__":
    main()
