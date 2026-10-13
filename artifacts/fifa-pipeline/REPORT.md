# FIFA Parquet ingestion validation

Run completed October 13, 2026 UTC, using fresh public FIFA calendar and timeline responses. This PR is based on `main` and is independent of the forecast/replay work in PR #2.

| Cohort | Matches | Events |
| --- | ---: | ---: |
| Premier League 2025/26 | 380 | 31,173 |
| World Cup 2026 | 104 | 9,675 |
| Total | 484 | 40,848 |

- Published `matches.parquet`: **145,839 bytes**.
- Published `events.parquet`: **7,470,649 bytes**.
- Combined Parquet: **7.62 MB**, including lossless source-record JSON.
- All **486 raw response hashes** verified.
- All **40,848 source events**, across **484 timelines**, compare equal after Parquet decoding; no EventId-only deduplication.
- All 484 completed matches have reconciled final scores and explicit regulation-end labels.
- Nine World Cup games contain extra time; four contain shootouts. Regulation and shootout scores are stored separately.
- 29,473 events have nonzero coordinates; 82 World Cup corners at `(0,0)` remain explicitly ambiguous. There are 8,143 known Premier League placeholder pairs and 3,150 records without a full coordinate pair.
- 220 substitution records occur before their assigned period's start, across 149 matches. Their raw clocks are retained; derived period elapsed time is null rather than negative. These and ambiguous-zero locations are reported as quality flags, not dropped or silently changed.
- No timeline fetch or structural normalization failures.

The source acquisition times and Parquet hashes are in the local snapshot manifest. The compact committed [ingestion summary](ingestion-summary.json) records output hashes and counts; the complete Parquet tables and raw caches remain ignored/local. Storage paths and commands are documented in the [pipeline guide](../../docs/pipelines/fifa-parquet.md).

## Tests and commands

27 unit/integration tests passed, covering pagination, Retry-After, nonretryable errors, cache verification, offline behavior, unknown codes, missing/duplicate event identities, coordinates, regulation/extra time/shootouts, failure-safe publication, Parquet round trips, and replay exports.

```sh
.venv/bin/python -m unittest discover -s tests/fifa -v
.venv/bin/python -m pipelines.fifa ingest --refresh
.venv/bin/python -m pipelines.fifa verify --cache data/raw/fifa
.venv/bin/python -m pipelines.fifa export-replay \
  --match zhs8gg1hvcuqvhkk2itb54pg \
  --output artifacts/fifa-replay/premier-league --sanitize-locations
ruff check pipelines/fifa tests/fifa
ruff format --check pipelines/fifa tests/fifa
git diff --check
```

## Existing replay compatibility

The earlier `main` chat's Czechia–South Africa renderer successfully generated all three stills using its **94 FIFA events read from Parquet**. Its existing market histories were used unchanged; this pipeline does not acquire new market data. The complete source events are preserved, including their raw ordering, timestamp text, commentary, and optional fields.

```sh
.venv/bin/python -m pipelines.fifa replay-preview \
  --renderer /Users/alexkan/Desktop/frontiers/rin-gym-simulation/app/replay/render.py \
  --output artifacts/fifa-pipeline/replay-preview
```

![Existing replay rendered from Parquet](replay-preview/preview-goal-1.png)

[Replay provenance](replay-preview/parquet-replay-manifest.json) records the dataset version, source timeline hash, and exact renderer-file hash. The renderer remains in the separate working checkout; its code is not copied into this PR. The provided adapter exports any ingested match in the original FIFA timeline format, while the older visual renderer itself still has a fixed demo match. The StatsBomb possession/360 viewer is a different contract and is not represented as FIFA-compatible.
