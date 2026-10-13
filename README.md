# rin-gym

Sports forecasting research using game events and prediction-market histories. The current research focus is live home-win/draw/away-win probability forecasts, with calibration and held-out match evaluation.

## Repository layout

| Directory | Purpose |
| --- | --- |
| `docs/` | Data-source references and instructions for running pipelines and applications |
| `research/` | Research questions, audit findings and proposed evaluation methods |
| `app/` | Visualization, dashboard and simulation application code |
| `pipelines/` | Extraction, transformation, data joins and validation |
| `data/` | Source manifests, generated tables, examples and ignored raw caches |
| `artifacts/` | Generated images, videos and other presentation outputs |

Start with the [research directions](research/forecasting/research-directions.md), [data-source guide](docs/data-sources/README.md) and [pipeline instructions](docs/pipelines/README.md).

## Current evidence

- A 60-game NBA/EPL/Euro audit establishes downloadable event/market overlap in the sampled cohorts.
- FIFA World Cup 2026 timelines contain 9,675 events across 104 games, including 6,905 coordinate-bearing events.
- The [2026-10-13 mapping audit](artifacts/fifa-market-mapping/REPORT.md) records 484 FIFA fixtures with complete Polymarket regulation home/draw/away mappings and 42,746 priced contracts across all retained market families.
- Spatial observations are event locations, not continuous player or ball tracking. Historical prices do not establish executable fills or publication timing.

The [initial audit reports](research/initial-audit/) preserve scope and limitations. Forecasting experiments and learned simulators are research directions, not completed capabilities in this import.

## Run the data pipelines

Python 3.11 or newer; acquisition and audit code uses the standard library.

```sh
python3 -m pipelines.sports_data.run_audit
python3 -m pipelines.sports_data.fifa_census
python3 -m pipelines.sports_data.fifa_join
python3 -m pipelines.sports_data.verify
```

The first run needs public internet access. Downloads are cached under `data/raw/sports/`; generated tables live under `data/processed/sports/`. Raw caches are excluded from Git.

## FIFA Parquet ingestion

Ingest Premier League 2025/26 and World Cup 2026 into shared local match/event tables:

```sh
python3 -m pip install -r pipelines/fifa/requirements.txt
python3 -m pipelines.fifa ingest
python3 -m pipelines.fifa verify --cache data/raw/fifa
```

The [pipeline guide](docs/pipelines/fifa-parquet.md) documents the schemas, cache/refresh behavior, and replay adapter. The completed [ingestion report](artifacts/fifa-pipeline/REPORT.md) covers 484 matches and 40,848 events.

## Match and market entity resolution

The [entity-resolution guide](docs/pipelines/fifa-market-resolution.md) explains
how FIFA fixtures map to Polymarket events, contracts, outcome tokens, and prices.
It covers exact team aliases and kickoff matching, provider and token-registry
checks, duplicate quarantine, regulation versus advancement markets, and price
freshness. Excluded mappings remain in the raw data and audit ledgers. The guide
also documents the limits of duplicate detection, fraud checks, and player-prop
labeling, with links to the implementing code.

## Kalshi market histories

The [Kalshi guide](docs/pipelines/kalshi.md) covers official API acquisition,
optional Jon Becker trade-Parquet imports, contract resolution, DuckDB replay
and trading simulation. Kalshi uses the existing Polymarket core schemas with
explicit venue metadata and separate output directories.
