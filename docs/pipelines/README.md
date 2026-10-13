# Running the data pipelines

Run commands from the repository root with Python 3.11+. No third-party Python dependencies are needed for these pipelines.

```sh
python3 -m pipelines.sports_data.run_audit
python3 -m pipelines.sports_data.analyze
python3 -m pipelines.sports_data.verify
python3 -m pipelines.sports_data.fifa_census
python3 -m pipelines.sports_data.fifa_join
```

`run_audit` acquires and analyzes the NBA/EPL/Euro cohorts. The committed NBA/EPL selection manifests freeze the original 20-game samples. `analyze` recomputes metrics from the cache; `verify` checks the 60-game invariants and hashes of cached sources.

`fifa_census` fetches the World Cup 2026 calendar and all returned timelines, then writes a normalized event CSV and coverage census. `fifa_join` joins two FIFA matches to the latest strictly preceding Polymarket sample for each regulation-time outcome. Price age is retained and a 90-second freshness flag is explicit.

## Paths

- `pipelines/sports_data/`: extraction, normalization, analysis and validation code.
- `data/raw/sports/`: ignored downloaded JSON, metadata and failed-request diagnostics.
- `data/manifests/sports/`: frozen cohort selections and original source provenance.
- `data/processed/sports/`: CSV tables and JSON audit results.

The initial import reorganizes exploratory code with minimal changes to its analysis. The cache primarily uses stable filenames; delete a specific cached response and its metadata to deliberately refresh it. Provider responses can change, so refreshed results need not reproduce the original hashes. The original source index is not silently updated by reruns.

Some analysis functions retain cohort-specific checks and an explicitly documented EPL reschedule override. These are research pipelines, not a generic production ingestion service.

## Shared FIFA Parquet pipeline

The [FIFA Parquet guide](fifa-parquet.md) covers ingestion of Premier League 2025/26 and World Cup 2026, immutable snapshots, validation, and the adapter for the existing FIFA replay visualization.

```sh
python3 -m pip install -r pipelines/fifa/requirements.txt
python3 -m pipelines.fifa ingest
python3 -m pipelines.fifa verify --cache data/raw/fifa
```

This new pipeline requires PyArrow; the earlier CSV audit commands above retain their existing dependencies.
