"""Read Parquet as the original FIFA timeline contract used by the replay UI."""

import json
import hashlib
from pathlib import Path
import pyarrow.parquet as pq
from .client import write_json
from .dataset import resolve_snapshot


def load_match(dataset, match_id):
    root = resolve_snapshot(dataset)
    filters = [("source", "=", "fifa"), ("match_id", "=", str(match_id))]
    matches = pq.read_table(root / "matches.parquet", filters=filters).to_pylist()
    if len(matches) != 1:
        raise ValueError("Expected exactly one FIFA match: " + str(match_id))
    match = matches[0]
    if match["timeline_status"] != "ok":
        raise ValueError("Match has no valid replay timeline: " + str(match_id))
    events = pq.read_table(root / "events.parquet", filters=filters).to_pylist()
    events.sort(key=lambda e: e["source_index"])
    if len(events) != match["event_count"]:
        raise ValueError("Incomplete Parquet replay events")
    return match, events


def load_timeline(dataset, match_id, sanitize_locations=False):
    """Lossless events by default; optionally suppress known placeholder positions.

    No event is deduplicated on EventId, no clock is synthesized, and ambiguous
    zero coordinates (e.g. World Cup corners) are not discarded.
    """
    match, rows = load_match(dataset, match_id)
    raw_match = json.loads(match["source_match_json"])
    events = []
    for row in rows:
        event = json.loads(row["source_event_json"])
        if sanitize_locations and row["location_status"] in (
            "placeholder",
            "out_of_range",
        ):
            event.pop("PositionX", None)
            event.pop("PositionY", None)
        events.append(event)
    return {
        key: raw_match.get(key)
        for key in ("IdMatch", "IdCompetition", "IdSeason", "IdStage", "IdGroup")
    } | {"Event": events}


def export_replay(dataset, match_id, output, sanitize_locations=False):
    dataset = resolve_snapshot(dataset)
    match, _ = load_match(dataset, match_id)
    timeline = load_timeline(dataset, match_id, sanitize_locations)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    path = output / ("fifa_" + str(match_id) + "_timeline.json")
    write_json(path, timeline)
    write_json(
        output / ("fifa_" + str(match_id) + "_match.json"),
        json.loads(match["source_match_json"]),
    )
    write_json(
        output / "replay-manifest.json",
        {
            "match_id": str(match_id),
            "dataset_version": match["dataset_version"],
            "events": len(timeline["Event"]),
            "source_response_sha256": match["timeline_response_sha256"],
            "sanitize_locations": sanitize_locations,
            "market_data_included": False,
            "limitations": "Event locations only; no tracking, pass stream, lineups, or fabricated market prices.",
        },
    )
    return path


def preview_existing_renderer(dataset, renderer_path, output):
    """Run the earlier fixed Czechia/South Africa renderer against Parquet.

    This optional bridge does not copy or modify code from the separate replay
    branch. Market fixtures are still supplied by that renderer, not this feed.
    """
    import importlib.util
    import sys

    dataset = resolve_snapshot(dataset)
    renderer_path = Path(renderer_path).resolve()
    root = renderer_path.parents[2]
    # The installed-style renderer imports rin_gym.paths from its own checkout.
    sys.path.insert(0, str(root))
    try:
        spec = importlib.util.spec_from_file_location(
            "_rin_existing_replay", renderer_path
        )
        renderer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(renderer)
        if not hasattr(renderer, "initialize"):
            raise ValueError(
                "Use the main chat renderer with initialize()/main() entry points"
            )
        original_load = renderer.load
        timeline = load_timeline(dataset, "400021440")
        renderer.load = (
            lambda name: timeline
            if name == "fifa_400021440_timeline"
            else original_load(name)
        )
        renderer.main(["--preview", "--output", str(Path(output).resolve())])
        match, _ = load_match(dataset, "400021440")
        write_json(
            Path(output) / "parquet-replay-manifest.json",
            {
                "match_id": "400021440",
                "events": len(timeline["Event"]),
                "dataset_version": match["dataset_version"],
                "timeline_source_sha256": match["timeline_response_sha256"],
                "renderer_sha256": hashlib.sha256(
                    renderer_path.read_bytes()
                ).hexdigest(),
                "input": "FIFA events read from Parquet; existing renderer supplies its market fixtures",
            },
        )
    finally:
        sys.path.pop(0)
