# Arbitrary FIFA match replay with DuckDB

Import FIFA Parquet and optional mapped Polymarket or Kalshi histories into a local DuckDB
database. Select any match by its FIFA ID and render stills or video. Runtime
replay reads only the database; no APIs or original Parquet files are needed.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r app/replay/requirements.txt
.venv/bin/python -m app.replay import \
  --dataset data/processed/fifa \
  --markets data/processed/fifa-market-history \
  --database data/replay.duckdb
.venv/bin/python -m app.replay list-matches \
  --database data/replay.duckdb --search Liverpool
.venv/bin/python -m app.replay render \
  --database data/replay.duckdb \
  --match zhs8gg1hvcuqvhkk2itb54pg \
  --output artifacts/fifa-replay/liverpool-bournemouth --preview
# Omit --preview for the full H.264 video; requires FFmpeg/libx264.
.venv/bin/python -m app.replay render \
  --database data/replay.duckdb --match 400021513 \
  --output artifacts/fifa-replay/germany-paraguay
```

`--dataset` takes the original FIFA snapshot directory or its `current.json`
parent, containing `matches.parquet`, `events.parquet`, and `manifest.json`.
`--markets` takes the all-family odds snapshot with `markets.parquet`,
`prices.parquet`, and its manifest from the separate odds-ingestion pipeline.
Omit `--markets` for event-only replay with explicit missing-odds labels.

## Download the shared datasets

From the repository root on this PR's branch, download the
[versioned dataset release](https://github.com/Shreyaas14/rin-gym/releases/tag/data-2026-10-13-v1).
Repository read access and an authenticated GitHub CLI are required.

```sh
gh release download data-2026-10-13-v1 --repo Shreyaas14/rin-gym \
  --dir data/downloads/data-2026-10-13-v1
(cd data/downloads/data-2026-10-13-v1 && shasum -a 256 -c SHA256SUMS)
for archive in data/downloads/data-2026-10-13-v1/*.tar.gz; do
  tar -xzf "$archive"
done
for dataset in fifa fifa-markets fifa-market-history; do
  shasum -a 256 -c "data/processed/$dataset/SHA256SUMS"
done
```

Stop if any checksum fails. Extract into a fresh checkout/data directory to avoid
replacing existing `current.json` pointers. Then run the import commands above.
The `fifa-markets` archive contains optional enriched events and mapping reports;
the replay's `--markets` argument uses `fifa-market-history` instead.

The dataset tree can be moved to any directory. Manifest-relative FIFA links are
resolved from the odds snapshot directory. Legacy producer absolute links and
release-v1 `data/processed/...` links resolve to the sibling
`fifa/snapshots/<version>` within the downloaded tree. Import never opens the
producer's path or resolves these links against the working directory. Keep the
`fifa` and `fifa-market-history` directories together in that layout, or use a
manifest-relative link for a custom layout. The source match-table checksum
comparison remains required when a link is present; missing or mismatched linked
snapshots fail import. The original release tag pins the earlier PR commit;
use this updated branch to get this portability fix.

## Database

Materialized tables are `matches`, `events`, `markets`, and `prices`, plus
`replay_metadata` with source paths, manifest hashes, and import version. All
market families are available for queries; this renderer displays only the
pipeline's mapped regulation home/draw/away contracts. Prices are stored in
match/token/time order, and queries use parameterized DuckDB SQL.

Import verifies Parquet hashes, unique match/event/market identities, event
counts, foreign keys, and consistency with the FIFA snapshot used to map odds.
It builds a temporary database and publishes only after validation. Existing files
require explicit `--replace`; a failed rebuild preserves the previous database.
Do not replace a database while another application is writing it. Runtime
connections are read-only. Generated databases and standard replay outputs are
Git-ignored.

## Replay behavior

- Teams, competition, feed, score, and output metadata come from the selected match.
- Events follow recorded UTC timestamps with source-order tie breaking. Concurrent
  scores use the maximum within that timestamp only: a stale assist record cannot
  undo a goal. Earlier state never reads a future match score.
- Half-time, extra time, and shootouts follow explicit period boundaries. Penalty
  scores are separate from game scores.
- Markers show observed event coordinates in the provider's orientation. Missing,
  placeholder, ambiguous-zero, and invalid locations are suppressed. Historical
  markers represent past actions, not persistent player positions.
- Odds use the upstream market/outcome/token mapping. Ambiguous or incomplete
  H/D/A mappings disable the odds display; the renderer does not pick contracts
  by name similarity or trading volume.
- Samples must strictly precede replay UTC. Values over 90 seconds old are hidden
  and labelled stale; chart segments do not bridge stale gaps. Regulation odds
  stop at the second-half end marker, including in extra-time/shootout matches.
  Values are not normalized to sum to one.
- Nonlinear gap compression and holds animate recorded events without inventing
  player or ball trajectories. Occurrence timestamps are not first-seen times;
  retrospective replay does not establish tradable forecasting latency.

Outputs include `poster.png`, `final.png`, goal stills, extra-time/shootout start
stills when applicable, and `render-manifest.json` in both preview and video modes.
Video is `replay.mp4`, silent 1600×900 at 24 fps by default. `--fps` accepts 1–60;
`--max-seconds` produces an explicitly marked truncated video for smoke testing.
Use a separate output directory per match. Fonts fall back to Pillow when
Arial/DejaVu are unavailable.

## Checks

```sh
.venv/bin/python -m unittest discover -s tests/replay -v
.venv/bin/python -m unittest discover -s tests/fifa -v
ruff check app/replay tests/replay
.venv/bin/python -m app.replay verify --database data/replay.duckdb
```

See the [verification report](../../artifacts/replay-generalization/REPORT.md)
for the complete 484-match audit and sample images.
