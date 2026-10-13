"""Read AK's frozen release offline; no acquisition or provider normalization."""

from collections import defaultdict
import json
import math
from pathlib import Path

import pyarrow.parquet as pq

from .config import OUTCOMES

RELEASE = Path("data/raw/release-data-2026-10-13-v1")
EVENT_FIELDS = {
    "seq": "source_index",
    "period": "period_code",
    "minute": "match_minute_raw",
    "type": "event_type_label",
    "kind": "event_kind",
    "team_id": "team_id",
    "player_id": "player_id",
    "home_goals": "home_score",
    "away_goals": "away_score",
    "x": "x_raw",
    "y": "y_raw",
    "description": "event_description",
}


def snapshot(root, name):
    base = Path(root) / "data/processed" / name
    version = json.loads((base / "current.json").read_text())["dataset_version"]
    if (
        not isinstance(version, str)
        or Path(version).name != version
        or version in (".", "..")
    ):
        raise ValueError("Invalid release snapshot version")
    return base / "snapshots" / version


def market_rate(raw):
    """Use the release's explicit fee schedule, never the legacy base-fee field."""
    if raw.get("feesEnabled") is False:
        return 0.0
    schedule = raw.get("feeSchedule") or {}
    rate = schedule.get("rate")
    if (
        raw.get("feesEnabled") is not True
        or schedule.get("exponent") != 1
        or type(rate) not in (int, float)
        or not math.isfinite(rate)
        or rate < 0
    ):
        raise ValueError(
            f"Missing or unsupported fee schedule for market {raw.get('id')}"
        )
    return rate


# Explicit release aliases, not fuzzy matching: an unfamiliar team must fail closed.
TEAM_ALIASES = {
    "bosnia-herzegovina": "bosnia and herzegovina",
    "dr congo": "congo dr",
    "brighton": "brighton & hove albion",
    "newcastle": "newcastle united",
    "tottenham": "tottenham hotspur",
    "united states": "usa",
    "west ham": "west ham united",
    "wolves": "wolverhampton wanderers",
}


def team_name(name):
    words = name.casefold().split()
    name = " ".join(w for w in words if w not in ("fc", "afc"))
    return TEAM_ALIASES.get(name, name)


def verify_market(match, market, row, raw):
    """Cross-check release labels against the actual contract, including team side."""
    outcome, token = market["outcome"], market["token_id"]
    title = raw.get("groupItemTitle", "")
    named = (
        title == "Draw" or title.startswith("Draw (")
        if outcome == "draw"
        else team_name(title) == team_name(match[outcome + "_team_name"])
    )
    outcomes = json.loads(raw.get("outcomes", "[]"))
    tokens = json.loads(raw.get("clobTokenIds", "[]"))
    if (
        row["match_id"] != match["match_id"]
        or row["market_id"] != market["market_id"]
        or str(raw.get("id")) != market["market_id"]
        or row["hda_outcome"] != outcome
        or len(outcomes) != len(tokens)
        or dict(zip(outcomes, tokens)).get("Yes") != token
        or not named
    ):
        raise ValueError(
            f"Release contract identity mismatch: {match['match_id']} {outcome}"
        )


def match_record(match, rows):
    """Project release columns into the environment's compact in-memory record."""
    side = {match["home_team_id"]: "home", match["away_team_id"]: "away"}
    events = []
    for row in sorted(rows, key=lambda r: r["source_index"]):
        if row["event_time_utc"] is None:
            raise ValueError(f"Missing event timestamp in {match['match_id']}")
        events.append(
            {
                **{k: row[v] for k, v in EVENT_FIELDS.items()},
                "t": row["event_time_utc"].timestamp(),
                "utc": json.loads(row["source_event_json"])["Timestamp"],
                "team": side.get(row["team_id"]),
                "minute": row["match_minute_raw"] or None,
            }
        )
    periods = {}
    for name, kind, period in (
        ("h1_start", "period_start", 3),
        ("h1_end", "period_end", 3),
        ("h2_start", "period_start", 5),
        ("h2_end", "period_end", 5),
    ):
        found = [e["t"] for e in events if (e["kind"], e["period"]) == (kind, period)]
        if len(found) != 1:
            raise ValueError(f"{match['match_id']}: expected one {name}")
        periods[name] = found[0]
    if (
        not periods["h1_start"]
        < periods["h1_end"]
        <= periods["h2_start"]
        < periods["h2_end"]
    ):
        raise ValueError(f"{match['match_id']}: invalid period order")
    home, away = match["regulation_home_score"], match["regulation_away_score"]
    if (
        home is None
        or away is None
        or match["timeline_status"] != "ok"
        or len(rows) != match["event_count"]
    ):
        raise ValueError(f"{match['match_id']}: incomplete release match")
    return {
        "schema": "match_record/v1",
        "match_id": match["match_id"],
        "source": match["source"],
        "competition_id": match["competition_id"],
        "home": match["home_team_name"],
        "away": match["away_team_name"],
        "home_team_id": match["home_team_id"],
        "away_team_id": match["away_team_id"],
        "scheduled_utc": match["scheduled_kickoff_utc"].strftime("%Y-%m-%dT%H:%M:%SZ"),
        "periods": periods,
        "stage_name": match["stage_name"],
        "regulation": {
            "home_goals": home,
            "away_goals": away,
            "result": "draw" if home == away else "home" if home > away else "away",
        },
        "extra_time": match["went_to_extra_time"],
        "events": [e for e in events if e["t"] <= periods["h2_end"]],
    }


def load_release(root=RELEASE):
    """Matches/events/prices plus market metadata for recorded per-contract fees."""
    data = snapshot(root, "fifa-markets")
    history = snapshot(root, "fifa-market-history")
    matches = pq.read_table(data / "matches.parquet").to_pylist()
    events = defaultdict(list)
    columns = list(
        dict.fromkeys(
            ["match_id", *EVENT_FIELDS.values(), "event_time_utc", "source_event_json"]
        )
    )
    for row in pq.read_table(data / "events.parquet", columns=columns).to_pylist():
        events[row["match_id"]].append(row)
    selected = {}
    for match in matches:
        mappings = match["prediction_markets"]
        if len(mappings) != 3 or {m["outcome"] for m in mappings} != set(OUTCOMES):
            raise ValueError(f"{match['match_id']}: expected three regulation markets")
        for market in mappings:
            if market["token_id"] in selected:
                raise ValueError("Duplicate release token mapping")
            selected[market["token_id"]] = (match, market)
    ids = [m["market_id"] for match in matches for m in match["prediction_markets"]]
    metadata = pq.read_table(
        history / "markets.parquet",
        columns=[
            "match_id",
            "market_id",
            "hda_outcome",
            "tokens",
            "source_market_json",
        ],
        filters=[("market_id", "in", ids)],
    ).to_pylist()
    rates = {}
    for row in metadata:
        raw = json.loads(row["source_market_json"])
        for token in row["tokens"]:
            if token["token_id"] in selected:
                key = token["token_id"]
                if token["outcome"] != "Yes" or key in rates:
                    raise ValueError("Release token identity mismatch or duplicate")
                verify_market(*selected[key], row, raw)
                rates[token["token_id"]] = market_rate(raw)
    if set(rates) != set(selected):
        raise ValueError("Missing release market metadata")
    prices = defaultdict(list)
    for row in pq.read_table(
        history / "prices.parquet",
        columns=["match_id", "token_id", "timestamp", "price"],
        filters=[("token_id", "in", list(selected))],
    ).to_pylist():
        key = row["token_id"]
        if (
            row["match_id"] != selected[key][0]["match_id"]
            or not math.isfinite(row["timestamp"])
            or not 0 <= row["price"] <= 1
        ):
            raise ValueError("Invalid release price")
        prices[key].append([row["timestamp"], row["price"]])
    records = []
    for match in matches:
        record = match_record(match, events[match["match_id"]])
        record.update(prices={}, markets={}, fee_rates={})
        for market in match["prediction_markets"]:
            outcome, token = market["outcome"], market["token_id"]
            series = sorted(prices[token])
            if not series or any(a[0] >= b[0] for a, b in zip(series, series[1:])):
                raise ValueError(f"Empty or duplicate price timestamps for {token}")
            record["prices"][outcome] = series
            record["markets"][outcome] = {
                k: market[k] for k in ("market_id", "token_id")
            }
            record["markets"]["slug"] = market["slug"]
            record["fee_rates"][outcome] = rates[token]
        records.append(record)
    return sorted(records, key=lambda r: (r["scheduled_utc"], r["match_id"]))
