"""Validated immutable snapshots, atomically published through current.json."""

from collections import Counter
from datetime import datetime, timezone
import hashlib
import gzip
import json
from pathlib import Path
import shutil
import tempfile
import uuid

import pyarrow as pa
import pyarrow.parquet as pq

from .client import COHORTS, dumps, write_json
from .normalize import normalize
from .schema import MATCHES, EVENTS, SCHEMA_VERSION, NORMALIZATION_VERSION


def table(rows, schema):
    result = pa.Table.from_pylist(rows, schema=schema)
    result.validate(full=True)
    for field in schema:
        if not field.nullable and result[field.name].null_count:
            raise ValueError("Required column contains null: " + field.name)
    return result


def resolve_snapshot(root):
    root = Path(root)
    if (root / "current.json").exists():
        version = json.loads((root / "current.json").read_text())["dataset_version"]
        if (
            not isinstance(version, str)
            or Path(version).name != version
            or version in (".", "..")
        ):
            raise ValueError("Invalid snapshot version")
        root = root / "snapshots" / version
    return root


def verify(root, cache=None):
    root = resolve_snapshot(root)
    manifest = json.loads((root / "manifest.json").read_text())
    tables = {}
    for name, schema in [("matches", MATCHES), ("events", EVENTS)]:
        path = root / (name + ".parquet")
        if (
            hashlib.sha256(path.read_bytes()).hexdigest()
            != manifest["files"][name]["sha256"]
        ):
            raise ValueError("Parquet hash mismatch: " + name)
        t = pq.read_table(path)
        if not t.schema.equals(schema, check_metadata=True):
            raise ValueError("Parquet schema mismatch: " + name)
        t.validate(full=True)
        for field in schema:
            if not field.nullable and t[field.name].null_count:
                raise ValueError("Required field is null: " + field.name)
        if t.num_rows != manifest["files"][name]["rows"]:
            raise ValueError("Row count mismatch: " + name)
        tables[name] = t.to_pylist()
    matches, events = tables["matches"], tables["events"]
    lookup = {(m["source"], m["match_id"]): m for m in matches}
    if len(lookup) != len(matches) or len({e["event_uid"] for e in events}) != len(
        events
    ):
        raise ValueError("Duplicate match/event key")
    counts, indices = Counter(), {}
    for e in events:
        key = (e["source"], e["match_id"])
        if key not in lookup:
            raise ValueError("Orphan event: " + e["event_uid"])
        m = lookup[key]
        identity = [e["source"], e["match_id"], e["team_id"], e["event_id"]]
        if hashlib.sha256(dumps(identity).encode()).hexdigest() != e["event_uid"]:
            raise ValueError("Event UID does not match its identity")
        if (e["competition_id"], e["season_id"], e["source_response_sha256"]) != (
            m["competition_id"],
            m["season_id"],
            m["timeline_response_sha256"],
        ):
            raise ValueError("Event/match source mismatch")
        if e["source_index"] != indices.get(key, -1) + 1:
            raise ValueError("Noncontiguous source order")
        indices[key] = e["source_index"]
        counts[key] += 1
    if any(
        m["event_count"] is not None and m["event_count"] != counts[key]
        for key, m in lookup.items()
    ):
        raise ValueError("Match event_count mismatch")
    if any(
        r["dataset_version"] != manifest["dataset_version"] for r in matches + events
    ):
        raise ValueError("Mixed dataset versions")
    raw_count = 0
    if cache is not None:
        raw_events = {}
        for source in manifest["sources"]:
            body = gzip.decompress(
                (Path(cache) / (source["sha256"] + ".json.gz")).read_bytes()
            )
            if hashlib.sha256(body).hexdigest() != source["sha256"]:
                raise ValueError("Raw response hash mismatch: " + source["url"])
            payload = json.loads(body)
            if "Event" in payload:
                raw_events[source["sha256"]] = payload["Event"]
            raw_count += 1
        for e in events:
            if (
                json.loads(e["source_event_json"])
                != raw_events[e["source_response_sha256"]][e["source_index"]]
            ):
                raise ValueError("Parquet event differs from raw response")
    return {
        "dataset_version": manifest["dataset_version"],
        "matches": len(matches),
        "events": len(events),
        "schema_version": SCHEMA_VERSION,
        "raw_responses_verified": raw_count,
        "verified": True,
    }


def ingest(client, output, cohorts=None, workers=4, allow_partial=False, scopes=None):
    if not 1 <= workers <= 16:
        raise ValueError("workers must be between 1 and 16")
    selected = [
        (name, *COHORTS[name])
        for name in (cohorts if cohorts is not None else list(COHORTS))
    ]
    selected.extend(
        (f"{competition}:{season}", competition, season, None)
        for competition, season in (scopes or [])
    )
    if not selected or len({(c, s) for _, c, s, _ in selected}) != len(selected):
        raise ValueError("Select distinct, nonempty competition/season scopes")
    version = (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        + "-"
        + uuid.uuid4().hex[:8]
    )
    matches, events, sources, errors, coverage = [], [], [], [], []
    for name, competition, season, expected in selected:
        calendar, metadata = client.calendar(competition, season)
        sources.extend(metadata)
        if expected is not None and len(calendar) != expected:
            raise ValueError(
                f"{name}: expected {expected} matches, received {len(calendar)}"
            )
        if not calendar:
            raise ValueError("No matches returned: " + name)
        count = 0
        for (
            raw_match,
            calendar_meta,
            timeline,
            timeline_meta,
            error,
        ) in client.timelines(calendar, workers):
            if timeline_meta:
                sources.append(timeline_meta)
            try:
                m, rows = normalize(
                    raw_match, calendar_meta, timeline, timeline_meta, version, error
                )
            except (ValueError, TypeError, KeyError) as exc:
                error = str(exc)
                m, rows = normalize(
                    raw_match, calendar_meta, None, timeline_meta, version, error
                )
                m["timeline_status"] = "invalid"
                m["quality_flags"] = ["invalid_timeline"]
            required_empty = (
                m["match_status"] == "completed" and m["timeline_status"] == "empty"
            )
            if error or required_empty:
                errors.append(
                    {
                        "match_id": m["match_id"],
                        "error": error or "Completed match has no events",
                    }
                )
            matches.append(m)
            events.extend(rows)
            count += len(rows)
        coverage.append(
            {
                "cohort": name,
                "competition_id": competition,
                "season_id": season,
                "matches": len(calendar),
                "events": count,
            }
        )
        print(json.dumps(coverage[-1]), flush=True)
    output = Path(output)
    if errors and not allow_partial:
        write_json(
            output / "failed-runs" / (version + ".json"),
            {"errors": errors, "coverage": coverage, "sources": sources},
        )
        raise ValueError(
            f"{len(errors)} failed/invalid required timelines; published snapshot unchanged"
        )
    snapshots = output / "snapshots"
    snapshots.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".building-", dir=snapshots))
    try:
        # Stable layout: one complete match's source-ordered events stay together.
        matches.sort(key=lambda m: (m["competition_id"], m["season_id"], m["match_id"]))
        events.sort(
            key=lambda e: (
                e["competition_id"],
                e["season_id"],
                e["match_id"],
                e["source_index"],
            )
        )
        files = {}
        for name, rows, schema in [
            ("matches", matches, MATCHES),
            ("events", events, EVENTS),
        ]:
            path = temporary / (name + ".parquet")
            pq.write_table(
                table(rows, schema),
                path,
                compression="zstd",
                version="2.6",
                row_group_size=16384,
            )
            files[name] = {
                "rows": len(rows),
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        manifest = {
            "dataset_version": version,
            "schema_version": SCHEMA_VERSION,
            "normalization_version": NORMALIZATION_VERSION,
            "pyarrow_version": pa.__version__,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "coverage": coverage,
            "files": files,
            "sources": sources,
            "errors": errors,
            "allow_partial": allow_partial,
            "match_quality_flags": dict(
                Counter(f for m in matches for f in m["quality_flags"])
            ),
            "event_quality_flags": dict(
                Counter(f for e in events for f in e["quality_flags"])
            ),
            "location_status_counts": dict(
                Counter(e["location_status"] for e in events)
            ),
            "timeline_status_counts": dict(
                Counter(m["timeline_status"] for m in matches)
            ),
        }
        write_json(temporary / "manifest.json", manifest)
        verify(temporary)
        target = snapshots / version
        temporary.rename(target)
        write_json(output / "current.json", {"dataset_version": version})
        return target, manifest
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
