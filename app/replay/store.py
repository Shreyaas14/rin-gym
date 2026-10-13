"""Import immutable Parquet snapshots and query replays from a local DuckDB file."""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile

import duckdb

from pipelines.fifa.dataset import resolve_snapshot


def records(cursor):
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def checked_snapshot(path, names):
    root = resolve_snapshot(Path(path).expanduser().resolve())
    manifest = json.loads((root / "manifest.json").read_text())
    for name in names:
        file = root / (name + ".parquet")
        expected = manifest["files"][name]["sha256"]
        if hashlib.sha256(file.read_bytes()).hexdigest() != expected:
            raise ValueError("Parquet checksum mismatch: " + str(file))
    return root, manifest


def linked_fifa_snapshot(market_root, reference):
    """Resolve lineage inside a relocated bundle, never on the producer's host."""
    path = Path(reference)
    # Original pipeline manifests contain absolute paths; release v1 rewrites
    # them to data/processed/... paths. Both identify this same sibling snapshot.
    if path.is_absolute() or path.parts[:2] == ("data", "processed"):
        if (
            len(path.parts) < 3
            or path.parts[-3:-1] != ("fifa", "snapshots")
            or path.name in ("", ".", "..")
            or market_root.parent.name != "snapshots"
            or market_root.parent.parent.name != "fifa-market-history"
        ):
            raise ValueError(
                "Unsupported FIFA snapshot link; use a path relative to the "
                "market snapshot or the standard data/processed bundle layout"
            )
        return market_root.parents[2].joinpath(*path.parts[-3:])
    return market_root / path


def import_database(dataset, database, market_dataset=None, replace=False):
    """Materialize into a temporary database; publish only after FK validation."""
    database = Path(database).expanduser().resolve()
    if database.exists() and not replace:
        raise ValueError("Database exists; pass --replace to rebuild it explicitly")
    source, source_manifest = checked_snapshot(dataset, ["matches", "events"])
    market_root = market_manifest = None
    if market_dataset:
        market_root, market_manifest = checked_snapshot(
            market_dataset, ["markets", "prices"]
        )
        # Resolve against the downloaded odds snapshot, independent of cwd or
        # producer paths. Keep the source hash check before building a database.
        linked_source = market_manifest.get("source_fifa_snapshot")
        if linked_source:
            linked_root = linked_fifa_snapshot(market_root, linked_source)
            linked_manifest = json.loads((linked_root / "manifest.json").read_text())
            if (
                linked_manifest["files"]["matches"]["sha256"]
                != source_manifest["files"]["matches"]["sha256"]
            ):
                raise ValueError(
                    "Market histories were mapped against a different FIFA snapshot"
                )
    database.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(
        prefix="." + database.name, suffix=".duckdb", dir=database.parent
    )
    os.close(fd)
    temporary = Path(name)
    temporary.unlink()  # DuckDB creates its own file header.
    try:
        with duckdb.connect(str(temporary)) as con:
            con.execute("BEGIN")
            for table in ("matches", "events"):
                con.execute(
                    f"CREATE TABLE {table} AS SELECT * FROM read_parquet(?)",
                    [str(source / (table + ".parquet"))],
                )
            if con.execute(
                "SELECT count(*) != count(DISTINCT match_id) FROM matches"
            ).fetchone()[0]:
                raise ValueError("Match IDs are not unique")
            if con.execute(
                "SELECT count(*) != count(DISTINCT event_uid) FROM events"
            ).fetchone()[0]:
                raise ValueError("Event IDs are not unique")
            if con.execute(
                "SELECT count(*) FROM events e ANTI JOIN matches m USING(match_id)"
            ).fetchone()[0]:
                raise ValueError("Orphan FIFA events")
            if con.execute(
                "SELECT count(*) FROM matches WHERE source != 'fifa'"
            ).fetchone()[0]:
                raise ValueError("This renderer expects FIFA matches")
            if con.execute(
                "SELECT count(*) FROM matches m WHERE event_count != (SELECT count(*) FROM events e WHERE e.match_id=m.match_id)"
            ).fetchone()[0]:
                raise ValueError("Incomplete match timeline")
            if market_root:
                con.execute(
                    "CREATE TABLE markets AS SELECT * FROM read_parquet(?)",
                    [str(market_root / "markets.parquet")],
                )
                con.execute(
                    "CREATE TABLE prices AS SELECT * FROM read_parquet(?) ORDER BY match_id, token_id, timestamp",
                    [str(market_root / "prices.parquet")],
                )
                if con.execute(
                    "SELECT count(*) != count(DISTINCT market_id) FROM markets"
                ).fetchone()[0]:
                    raise ValueError("Market IDs are not unique")
                if con.execute(
                    "SELECT count(*) FROM markets k ANTI JOIN matches m USING(match_id)"
                ).fetchone()[0]:
                    raise ValueError(
                        "Market dataset references a different FIFA match universe"
                    )
                if con.execute(
                    "SELECT count(*) FROM prices p ANTI JOIN markets m ON p.market_id=m.market_id AND p.match_id=m.match_id"
                ).fetchone()[0]:
                    raise ValueError("Orphan market prices")
            else:
                con.execute(
                    "CREATE TABLE markets(match_id VARCHAR, market_id VARCHAR, market_family VARCHAR, hda_outcome VARCHAR, source_market_json VARCHAR, tokens JSON)"
                )
                con.execute(
                    "CREATE TABLE prices(match_id VARCHAR, market_id VARCHAR, token_id VARCHAR, outcome_index INTEGER, outcome VARCHAR, timestamp DOUBLE, price DOUBLE)"
                )
            metadata = dict(
                schema_version=1,
                imported_at_utc=datetime.now(timezone.utc).isoformat(),
                fifa_snapshot=str(source),
                fifa_manifest_sha256=hashlib.sha256(
                    (source / "manifest.json").read_bytes()
                ).hexdigest(),
                market_snapshot=str(market_root) if market_root else None,
                market_manifest_sha256=hashlib.sha256(
                    (market_root / "manifest.json").read_bytes()
                ).hexdigest()
                if market_root
                else None,
            )
            con.execute("CREATE TABLE replay_metadata(metadata JSON)")
            con.execute(
                "INSERT INTO replay_metadata VALUES (?)", [json.dumps(metadata)]
            )
            con.execute("COMMIT")
            counts = {
                n: con.execute(f"SELECT count(*) FROM {n}").fetchone()[0]
                for n in ("matches", "events", "markets", "prices")
            }
            con.execute("CHECKPOINT")
        if replace:
            os.replace(temporary, database)
        else:
            # Atomic no-clobber publication even if another import starts.
            os.link(temporary, database)
            temporary.unlink()
    finally:
        temporary.unlink(missing_ok=True)
        Path(str(temporary) + ".wal").unlink(missing_ok=True)
    return dict(database=str(database), bytes=database.stat().st_size, **counts)


@contextmanager
def connect(database):
    with duckdb.connect(
        str(Path(database).expanduser().resolve()), read_only=True
    ) as con:
        if (
            con.execute(
                "SELECT metadata::JSON->>'schema_version' FROM replay_metadata"
            ).fetchone()[0]
            != "1"
        ):
            raise ValueError("Unsupported replay database schema")
        yield con


def list_matches(database, search=None, limit=30):
    with connect(database) as con:
        return records(
            con.execute(
                """SELECT match_id, competition_name, scheduled_kickoff_utc,
            home_team_name, away_team_name, final_home_score, final_away_score, event_count
            FROM matches WHERE (? IS NULL OR contains(lower(home_team_name || ' ' || away_team_name), lower(?)))
            ORDER BY scheduled_kickoff_utc, match_id LIMIT ?""",
                [search, search, limit],
            )
        )


def load_match(database, match_id):
    with connect(database) as con:
        matches = records(
            con.execute("SELECT * FROM matches WHERE match_id=?", [str(match_id)])
        )
        if len(matches) != 1:
            raise ValueError("Unknown FIFA match ID: " + str(match_id))
        match = matches[0]
        if match["timeline_status"] != "ok":
            raise ValueError("Match does not have a valid FIFA timeline")
        events = records(
            con.execute(
                "SELECT * FROM events WHERE match_id=? ORDER BY event_time_utc NULLS LAST, source_index",
                [str(match_id)],
            )
        )
        if not events or any(e["event_time_utc"] is None for e in events):
            raise ValueError("Replay requires timestamped events")
        candidates = records(
            con.execute(
                "SELECT * FROM markets WHERE match_id=? AND market_family='moneyline' AND hda_outcome IS NOT NULL",
                [str(match_id)],
            )
        )
        mapped = {}
        unavailable = "No complete regulation home/draw/away mapping in this database"
        for market in candidates:
            outcome = market["hda_outcome"]
            if outcome not in ("home", "draw", "away") or outcome in mapped:
                mapped = {}
                unavailable = "Ambiguous regulation contract mapping; odds disabled"
                break
            # Use the upstream resolver's outcome identity and token mapping;
            # the renderer never chooses contracts by name, volume, or payout.
            tokens = market["tokens"]
            if isinstance(tokens, str):
                tokens = json.loads(tokens)
            yes = [
                t for t in tokens if t["outcome"] == "Yes" and t["status"] == "included"
            ]
            if len(yes) != 1:
                mapped = {}
                unavailable = "Missing verified Yes-token history; odds disabled"
                break
            token = yes[0]["token_id"]
            prices = records(
                con.execute(
                    """SELECT timestamp AS t, price AS p FROM prices
                WHERE match_id=? AND market_id=? AND token_id=? ORDER BY timestamp""",
                    [str(match_id), market["market_id"], token],
                )
            )
            if not prices or any(p["t"] <= q["t"] for q, p in zip(prices, prices[1:])):
                raise ValueError("Missing or duplicate price timestamps")
            if any(
                not math.isfinite(p["t"])
                or not math.isfinite(p["p"])
                or not 0 <= p["p"] <= 1
                for p in prices
            ):
                raise ValueError("Invalid historical price value")
            mapped[outcome] = dict(
                market_id=market["market_id"], token_id=token, prices=prices
            )
        if set(mapped) != {"home", "draw", "away"}:
            mapped = {}
        return dict(
            match=match,
            events=events,
            markets=mapped,
            odds_status="Mapped regulation outcomes" if mapped else unavailable,
            database=str(Path(database).resolve()),
            source_metadata=json.loads(
                con.execute("SELECT metadata FROM replay_metadata").fetchone()[0]
            ),
        )
