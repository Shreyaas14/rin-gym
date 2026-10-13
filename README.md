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

The [initial audit reports](research/initial-audit/) preserve scope and limitations. The [forecast replay harness](app/forecast/README.md) adds a Poisson score simulator and a chronological benchmark across 72 group-stage games (39 training, 13 validation, 20 test). The simulator produces home/draw/away probabilities from observed score and time, with optional frozen pregame market initialization.

On the 20 test games, calibrated simulator Brier is 0.3231 versus 0.3300 for live Polymarket. The paired match-bootstrap interval for the difference includes zero, and a two-minute observation delay removes the advantage. This is an inconclusive retrospective result, not evidence of profitable betting. See the [methodology and results](artifacts/forecast-eval/REPORT.md).

## Run the data pipelines

Python 3.11 or newer; acquisition and audit code uses the standard library.

```sh
python3 -m pipelines.sports_data.run_audit
python3 -m pipelines.sports_data.fifa_census
python3 -m pipelines.sports_data.fifa_join
python3 -m pipelines.sports_data.verify
```

The first run needs public internet access. Downloads are cached under `data/raw/sports/`; generated tables live under `data/processed/sports/`. Raw caches are excluded from Git.

## Free local evaluation and protected holdout

Install with `uv sync --locked` or add optional free local MLflow with `uv sync --locked --extra tracking`. Run `uv run rin-gym eval init` to initialize the pending future-cohort policy, and `uv run rin-gym eval quality` for scorer, leakage and governance checks. Add `--mlflow` to log the quality receipt locally.

The [evaluation workflow](docs/pipelines/evaluation-quality.md) covers frozen future fixtures, one-time holdout scoring, and append-only prospective prediction logging. Enrollment remains pending until fixtures are selected. Existing historical test results are regression/development evidence for subsequent changes.

`rin-gym forecast prepare`, `run`, `verify`, and `simulate` also expose the existing forecast workflow. Commands write below the current directory; use `rin-gym --workspace /path/to/workspace ...` or `RIN_GYM_WORKSPACE` to select another data workspace. The original pipeline module commands above remain available from the checkout.

For Tinker fine-tuning, install the optional `tinker` extra and follow the [Tinker integration guide](docs/pipelines/tinker-evaluation.md). It adds chronological dataset exclusions, cookbook evaluators, and prospective checkpoint prediction logging; MLflow remains optional.

## FIFA Parquet ingestion

Ingest Premier League 2025/26 and World Cup 2026 into shared local match/event tables:

```sh
python3 -m pip install -r pipelines/fifa/requirements.txt
python3 -m pipelines.fifa ingest
python3 -m pipelines.fifa verify --cache data/raw/fifa
```

The [pipeline guide](docs/pipelines/fifa-parquet.md) documents the schemas, cache/refresh behavior, and replay adapter. The completed [ingestion report](artifacts/fifa-pipeline/REPORT.md) covers 484 matches and 40,848 events.
