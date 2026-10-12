# Application code

Visualization, dashboard and simulation application code belongs here. The initial research import contains data pipelines and evaluation proposals; the event-replay application is a separate follow-up change.

## Forecast evaluation

[`forecast/`](forecast/README.md) contains the replay evaluation harness, Poisson score simulator, CLI and tests. It generates a standalone dashboard under `artifacts/forecast-eval/` and compares forecasts with live Polymarket on chronological held-out games.
