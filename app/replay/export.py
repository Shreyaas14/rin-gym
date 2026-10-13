"""Export either venue's verified DuckDB histories to the trading simulator."""

from collections import defaultdict
import math
from pathlib import Path

from app.trade_env.build_records import Excluded, match_record
from app.trade_env.records import validate, write_records
from pipelines.fifa.client import write_json
from .store import list_matches, load_match


def record_from_replay(data):
    if set(data["markets"]) != {"home", "draw", "away"}:
        raise Excluded(data["odds_status"])
    rows = sorted(data["events"], key=lambda e: e["source_index"])
    record = match_record(data["match"], rows)
    record["market_venue"] = (
        data.get("source_metadata", {}).get("market_venue") or "polymarket"
    )
    record["price_history_type"] = data.get("source_metadata", {}).get(
        "price_history_type", "sampled"
    )
    record["markets"] = {"slug": record["market_venue"] + ":" + record["match_id"]}
    record["prices"] = {}
    for side, market in data["markets"].items():
        record["markets"][side] = {
            key: market[key] for key in ("market_id", "token_id")
        }
        # match_record/v1 uses integer seconds. A trade bucket is available only
        # after its last fractional timestamp; ceil never moves a price earlier.
        buckets = defaultdict(list)
        for p in market["prices"]:
            if p["t"] <= record["periods"]["h2_end"]:
                buckets[math.ceil(p["t"])].append(p)
        record["prices"][side] = [
            [t, max(points, key=lambda p: p["t"])["p"]]
            for t, points in sorted(buckets.items())
        ]
    return validate(record)


def export_records(database, output):
    records, audit = [], []
    for match in list_matches(database, limit=1000000):
        try:
            record = record_from_replay(load_match(database, match["match_id"]))
        except (Excluded, ValueError) as error:
            audit.append(
                dict(match_id=match["match_id"], status="excluded", reason=str(error))
            )
        else:
            records.append(record)
            audit.append(
                dict(match_id=match["match_id"], status="included", reason=None)
            )
    if not records:
        raise ValueError("No complete replay records to export")
    write_records(output, records)
    report = Path(str(output) + ".audit.json")
    write_json(report, audit)
    return dict(
        output=str(output),
        records=len(records),
        excluded=len(audit) - len(records),
        audit=str(report),
        market_venue=records[0]["market_venue"],
        execution_model="Historical observations with the trade_env configurable fee/spread/delay assumptions; not actual venue fills or a calibrated fee schedule.",
    )
