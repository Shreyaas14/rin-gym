# FIFA calendar and event ingestion

The default run acquires Premier League 2025/26 and World Cup 2026 into the same two Parquet schemas: one row per match and one row per source event. The full-schema reference is [fifa-parquet-schema.md](fifa-parquet-schema.md).

## Install and run

Python 3.11+; run from the repository root. PyArrow is the only ingestion dependency.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r pipelines/fifa/requirements.txt
.venv/bin/python -m pipelines.fifa ingest
.venv/bin/python -m pipelines.fifa verify --cache data/raw/fifa
```

The default run makes 486 public requests: two calendars and 484 timelines. It follows calendar continuation tokens, retries transient errors (honoring Retry-After), and uses four concurrent timeline requests. No FIFA credentials are needed for the tested public endpoints. Network availability and provider behavior can change.

Output is local and ignored by Git:

```text
data/processed/fifa/current.json
data/processed/fifa/snapshots/<dataset_version>/matches.parquet
data/processed/fifa/snapshots/<dataset_version>/events.parquet
data/processed/fifa/snapshots/<dataset_version>/manifest.json
data/raw/fifa/<sha256>.json.gz
data/raw/fifa/requests/<url_sha256>.json
```

Raw payloads are content addressed. Cache reads verify hashes; new fetches retain retrieval time and URL. A successful run creates an immutable snapshot, verifies both Parquet files, and atomically changes `current.json`. A failed run leaves the current dataset unchanged. Reruns produce no duplicate logical rows; they keep earlier snapshots. There is no automatic retention cleanup.

A run publishes exactly the selected scopes. Selecting only one cohort produces a snapshot containing only that cohort; it does not silently merge previous selections. Run with no cohort arguments to publish both defaults again. Concurrent writers have last-successful-publisher-wins pointer semantics; use one ingestion process per output root if publication order matters.

```sh
# Reuse verified cache only, with no network.
.venv/bin/python -m pipelines.fifa ingest --offline

# Fetch source corrections and publish a new snapshot.
.venv/bin/python -m pipelines.fifa ingest --refresh

# One named cohort, another explicit competition/season, or a custom output root.
.venv/bin/python -m pipelines.fifa ingest --cohort premier-league-2025-26
.venv/bin/python -m pipelines.fifa ingest --scope 17:285023 --output ./cup-parquet
```

`--workers` accepts 1–16. Default strict mode rejects publication if a required timeline fetch or structural normalization fails, or a completed match has an empty timeline. `--allow-partial` explicitly permits an incomplete dataset: every discovered calendar match remains present with its timeline status and null unavailable counts, and the manifest lists failures. Soft quality flags preserve unusual or ambiguous records instead of silently repairing them. Known historical cohorts additionally require the expected 380/104 calendar counts.

Prior audit caches can seed a network-free run. Only recognized FIFA calendar/timeline files with valid original hash metadata are imported; unrelated market/credential files are not read. The importer supports `.http.json` metadata from the league audit and `.meta.json` from the repository's older sports cache. The World Cup legacy calendar is mapped to the equivalent explicit competition/season query only after its complete scope is validated; the original URL is retained.

```sh
.venv/bin/python -m pipelines.fifa ingest --offline \
  --import-cache /path/to/premier-league-audit/raw \
  --import-cache /path/to/earlier-checkout/data/raw/sports
```

## Reading the tables

```python
import pyarrow.parquet as pq
from pipelines.fifa.dataset import resolve_snapshot

snapshot = resolve_snapshot("data/processed/fifa")
matches = pq.read_table(snapshot / "matches.parquet")
events = pq.read_table(
    snapshot / "events.parquet",
    filters=[("competition_id", "=", "2000000000")],
)
```

IDs are strings. Use `event_uid`, or the complete `(source, match_id, team_id, event_id)` identity, not EventId alone. Sort each match by `source_index` for original order; timestamps can tie. Final match labels must not be joined into live forecast inputs. The rows are historical annotations, not proof of when the events were first public.

`location_status` distinguishes present but uncalibrated coordinates, missing locations, known placeholders, ambiguous zeros, and out-of-range values. Raw coordinates are preserved. World Cup `(0,0)` corners are ambiguous rather than automatically removed. The feed does not contain continuous tracking or a complete pass/carry stream.

The observed `event_before_period_start` flags are substitutions recorded before their next half starts. They retain the event timestamp and raw period, but `period_elapsed_ms` is null rather than negative. Source clocks are not interpolated through breaks. Regulation, extra-time final scores, and shootout tallies have separate columns.

## Replay integration

`load_timeline()` returns the original FIFA `Event` contract used by the earlier World Cup renderer. It reads all events from Parquet; the raw cache is not required. Source JSON columns retain qualifiers, VAR objects, commentary, optional fields, score values, and exact timestamp strings.

```python
from pipelines.fifa.replay import load_timeline

timeline = load_timeline("data/processed/fifa", "400021440")
events = timeline["Event"]
```

Export any ingested match as the earlier FIFA file format:

```sh
.venv/bin/python -m pipelines.fifa export-replay \
  --match 400021440 --output artifacts/fifa-replay/czechia-south-africa

.venv/bin/python -m pipelines.fifa export-replay \
  --match zhs8gg1hvcuqvhkk2itb54pg \
  --output artifacts/fifa-replay/liverpool-bournemouth --sanitize-locations
```

`--sanitize-locations` removes only known placeholder/out-of-range pitch positions in the exported view. The default is lossless; raw Parquet is never changed. Exports include the match calendar record and provenance. They do not fabricate Polymarket histories.

The earlier `main` chat's offline World Cup renderer is still hardcoded to Czechia–South Africa. A bridge runs that existing renderer with its timeline supplied directly from Parquet, leaving its bundled market histories in place. It does not modify the other branch or imply the renderer is already a generic league-match browser. The separate StatsBomb play-by-play viewer has a different possession/360 contract; FIFA data cannot fill those missing observations.

With that renderer checkout available and its NumPy/Pillow dependencies installed in this environment:

```sh
.venv/bin/python -m pip install numpy Pillow
.venv/bin/python -m pipelines.fifa replay-preview \
  --renderer /path/to/replay-checkout/app/replay/render.py \
  --output artifacts/fifa-replay/preview
```

This invokes the renderer's `initialize`/`main` implementation from the current working replay checkout. No external renderer code is copied into this PR. For a future generic renderer, use `load_match()` for metadata/normalized events and `load_timeline()` for the existing source-event interface.

## Validation

```sh
.venv/bin/python -m unittest discover -s tests/fifa -v
.venv/bin/python -m pipelines.fifa verify --cache data/raw/fifa
```

Verification checks file hashes, exact Arrow types/nullability/metadata, counts, unique keys, match/event provenance, source-order continuity, and dataset versions. With `--cache`, it also verifies all source response hashes and every original event against Parquet. Unit tests cover network/cache failures, pagination, repeated IDs, nulls, unknown fields, extra time/shootouts, ambiguous coordinates, failed publication, and replay round trips.

The committed [ingestion report](../../artifacts/fifa-pipeline/REPORT.md) records the completed two-cohort run and replay screenshot. Full datasets and cache files remain local; rerun the commands above to acquire them.
