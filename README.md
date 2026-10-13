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
- Two FIFA matches have verified joins to all three Polymarket regulation-time outcome markets.
- Spatial observations are event locations, not continuous player or ball tracking. Historical prices do not establish executable fills or publication timing.

The [initial audit reports](research/initial-audit/) preserve scope and limitations. Forecasting experiments and learned simulators are research directions, not completed capabilities in this import.

## Python setup

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then run `uv sync --locked` from this directory. uv manages Python 3.12 and `.venv`; no activation or pip commands are needed. Dependencies live in `pyproject.toml` and exact resolutions in `uv.lock`.

Use `uv run --locked python ...` for commands. For the [RL trainer](app/trade_env/README.md#tinker-training), use `uv sync --locked --extra train` and `uv run --locked --extra train python ...`. The optional `replay` extra supplies NumPy/Pillow for the external replay renderer.

Add dependencies with `uv add PACKAGE` (or `uv add --optional train PACKAGE`), and commit both `pyproject.toml` and `uv.lock`. `uv lock --upgrade-package PACKAGE` deliberately updates a locked dependency.

## Run the data pipelines

Use `uv` from the repository root. `.python-version` selects Python 3.12; acquisition and audit code uses the standard library.

```sh
uv run --locked python -m pipelines.sports_data.run_audit
uv run --locked python -m pipelines.sports_data.fifa_census
uv run --locked python -m pipelines.sports_data.fifa_join
uv run --locked python -m pipelines.sports_data.verify
```

The first run needs public internet access. Downloads are cached under `data/raw/sports/`; generated tables live under `data/processed/sports/`. Raw caches are excluded from Git.

## FIFA Parquet ingestion

Ingest Premier League 2025/26 and World Cup 2026 into shared local match/event tables:

```sh
uv sync --locked
uv run --locked python -m pipelines.fifa ingest
uv run --locked python -m pipelines.fifa verify --cache data/raw/fifa
```

The [pipeline guide](docs/pipelines/fifa-parquet.md) documents the schemas, cache/refresh behavior, and replay adapter. The completed [ingestion report](artifacts/fifa-pipeline/REPORT.md) covers 484 matches and 40,848 events.
