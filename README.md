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
