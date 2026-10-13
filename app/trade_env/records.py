"""match_record/v1: one JSON object per match, stored as .jsonl or .jsonl.gz."""

import gzip
import json
import os
from pathlib import Path

SCHEMA = "match_record/v1"
OUTCOMES = ("home", "draw", "away")
PERIODS = ("h1_start", "h1_end", "h2_start", "h2_end")
EVENT_FIELDS = (
    "t",
    "utc",
    "seq",
    "period",
    "minute",
    "type",
    "kind",
    "team",
    "team_id",
    "player_id",
    "home_goals",
    "away_goals",
    "x",
    "y",
    "description",
)
REQUIRED_EVENT_FIELDS = (
    "t",
    "seq",
    "period",
    "kind",
    "team",
    "home_goals",
    "away_goals",
)


def dumps(record):
    return json.dumps(
        record, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )


def result_of(home_goals, away_goals):
    if home_goals == away_goals:
        return "draw"
    return "home" if home_goals > away_goals else "away"


def is_number(value):
    return type(value) in (int, float)


def validate(record):
    """Return the record unchanged, or raise ValueError naming the first problem."""
    if not isinstance(record, dict):
        raise ValueError("record must be a JSON object")
    label = "match " + str(record.get("match_id")) + ": "

    def check(ok, message):
        if not ok:
            raise ValueError(label + message)

    check(record.get("schema") == SCHEMA, "schema must be " + SCHEMA)
    for key in (
        "match_id",
        "source",
        "home",
        "away",
        "home_team_id",
        "away_team_id",
        "scheduled_utc",
    ):
        check(isinstance(record.get(key), str) and record[key], "missing " + key)
    periods = record.get("periods") or {}
    times = [periods.get(key) for key in PERIODS]
    check(all(is_number(t) for t in times), "periods must be four epoch seconds")
    check(times[0] < times[1] <= times[2] < times[3], "periods are out of order")
    regulation = record.get("regulation") or {}
    goals = (regulation.get("home_goals"), regulation.get("away_goals"))
    check(
        all(type(g) is int and g >= 0 for g in goals),
        "regulation goals must be non-negative integers",
    )
    check(
        regulation.get("result") == result_of(*goals),
        "regulation result disagrees with regulation goals",
    )
    check(type(record.get("extra_time")) is bool, "extra_time must be true or false")
    events = record.get("events")
    check(isinstance(events, list) and events, "events must be a non-empty list")
    seq = -1
    for event in events:
        check(
            isinstance(event, dict)
            and all(key in event for key in REQUIRED_EVENT_FIELDS),
            "event after seq " + str(seq) + " is missing fields",
        )
        check(type(event["seq"]) is int and event["seq"] > seq, "event seq must rise")
        seq = event["seq"]
        check(is_number(event["t"]), "event t must be epoch seconds")
        check(event["t"] <= times[3], "event after h2_end at seq " + str(seq))
        check(event["team"] in ("home", "away", None), "bad event team")
        check(
            type(event["home_goals"]) is int and type(event["away_goals"]) is int,
            "event goals must be integers",
        )
    top = (
        max(event["home_goals"] for event in events),
        max(event["away_goals"] for event in events),
    )
    check(top == goals, "highest event score is not the regulation score")
    prices = record.get("prices")
    check(
        isinstance(prices, dict) and set(prices) == set(OUTCOMES),
        "prices must have exactly home, draw and away",
    )
    for outcome in OUTCOMES:
        series = prices[outcome]
        check(isinstance(series, list) and series, "no prices for " + outcome)
        check(
            all(
                isinstance(sample, list)
                and len(sample) == 2
                and type(sample[0]) is int
                and is_number(sample[1])
                and 0 <= sample[1] <= 1
                for sample in series
            ),
            "price samples for " + outcome + " must be [int seconds, 0..1]",
        )
        check(
            all(a[0] < b[0] for a, b in zip(series, series[1:])),
            "price times for " + outcome + " must strictly increase",
        )
    markets = record.get("markets")
    if markets is not None:
        check(
            isinstance(markets, dict)
            and isinstance(markets.get("slug"), str)
            and all(
                isinstance(markets.get(outcome), dict)
                and isinstance(markets[outcome].get("market_id"), str)
                and isinstance(markets[outcome].get("token_id"), str)
                for outcome in OUTCOMES
            ),
            "markets must give slug plus market_id and token_id per outcome",
        )
    return record


def iter_records(path, check=True):
    """Yield records from .jsonl, .jsonl.gz, a .json file (one record or a list),
    or a directory of such .json files."""
    path = Path(path)
    if path.is_dir():
        for part in sorted(path.glob("*.json")):
            yield from iter_records(part, check)
        return
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        if path.suffix == ".json":
            data = json.load(handle)
            rows = data if isinstance(data, list) else [data]
        else:
            rows = (json.loads(line) for line in handle if line.strip())
        for record in rows:
            yield validate(record) if check else record


def load_records(path, check=True):
    return list(iter_records(path, check))


def load_record(path, match_id, check=True):
    for record in iter_records(path, check=False):
        if record.get("match_id") == str(match_id):
            return validate(record) if check else record
    raise KeyError("No record for match " + str(match_id))


def write_records(path, records):
    """Validate and write one record per line; same records give the same bytes."""
    path = Path(path)
    lines = [dumps(validate(record)) + "\n" for record in records]
    body = "".join(lines).encode()
    if path.suffix == ".gz":
        body = gzip.compress(body, mtime=0)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    temporary.write_bytes(body)
    os.replace(temporary, path)
    return len(lines)
