# Forecast replay and simulation harness

A working, offline-rerunnable benchmark for home/draw/away forecasting from the World Cup 2026 group stage. Uses the FIFA timelines already downloaded in `data/raw/sports` and adds all 216 regulation-time Polymarket contracts for 72 games.

Open [the interactive report](../../artifacts/forecast-eval/report.html) or [the methodology and results](../../artifacts/forecast-eval/REPORT.md). The report contains the actual held-out comparison, calibration, delay scenarios and selectable game trajectories.

## Run

From the workspace root, with Python 3.10+ and NumPy installed:

```sh
python3 -m pip install -r app/forecast/requirements.txt
python3 -m app.forecast prepare
python3 -m unittest discover -s app/forecast/tests -v
python3 -m app.forecast run
python3 -m app.forecast.verify
```

The first preparation fetches public data; subsequent runs verify and reuse cached downloads. No API keys, paid services or trading actions. Existing FIFA files are a prerequisite; `python3 -m pipelines.sports_data.fifa_census` fetches them if needed.

To serve the interactive report locally:

```sh
python3 -m http.server 8765 --bind 127.0.0.1 --directory .
```

Open `http://127.0.0.1:8765/artifacts/forecast-eval/report.html`. The HTML is standalone, has no external libraries, and can also be opened directly in a normal browser.

## Run thousands of continuations from one observed state

After fitting via `run`:

```sh
python3 -m app.forecast simulate --match 400021444 --utc 2026-06-25T02:30:00Z --runs 100000
```

This samples future home/away goals from the pregame-initialized Poisson model. It returns estimated outcome probabilities, exact probabilities for comparison, Monte Carlo sampling errors and three example goal timelines. These are coarse score continuations, not player- or ball-motion simulations. Sampling uncertainty is not uncertainty about the model's validity.

## Evaluation contract

`config.json` fixes the temporal split, freshness thresholds, snapshot schedule, temperature grid, bootstrap seed and delay scenarios. Splits are frozen from match dates before evaluation: 39 training games through June 21 UTC, 13 validation games through June 24, and 20 test games after that. No random snapshot splitting.

The forecaster's input is the immutable `Observation` object in `replay.py`: score, observed phase, elapsed time and observation provenance. Targets and future end times are excluded. The model can optionally receive the pregame rate pair calibrated from prices frozen before kickoff. It never receives live market prices.

Evaluation uses exact probabilities from the same Poisson model rather than noisy sampled frequencies. Training-only empirical half durations estimate remaining playing time; future test whistle times are not model inputs. Live Polymarket is a separate benchmark. A temperature transform is selected by validation Brier and frozen for the test set and every delay scenario.

The CSV has **one row per match × snapshot × observation delay**, with every model's probability vector, raw and normalized market values, sample timestamps, labels and prefix cutoff. Consumers should use the observed-score columns for features and the target column only for evaluation.

## Extend

- Add a forecast method in `model.py` that accepts only `Observation` plus explicitly permitted historical inputs.
- Add its predictions to `evaluate.py` on the existing common snapshot set.
- Select any hyperparameters using training/validation only. Now that the test scores are visible, use a new independent test set for later claims of improvement.
- Keep match-weighted scoring and paired whole-match bootstrap comparisons.
- Add richer state from event prefixes if desired; never insert final team aggregates or actual future period lengths into observations.

## Important limits

This is a retrospective, small, single-tournament benchmark. The source matches were already publicly available and some were inspected during the preceding data audit. Historical event times are not verified publication times. Delay tests expose sensitivity but cannot establish the true live information set. Goal intensities omit red cards, shot quality and tactics. Three independent binary market prices are normalized, and their original values are retained. There is no betting/execution evaluation.

The replay handles conflicting score fields on simultaneous FIFA events by using the maximum score in the already-visible batch. Decreases between different timestamp batches are excluded during preparation. `data/manifests/forecast/manifest.json` reports conflicts and all source matches, including any exclusions. Cached source files have URLs, retrieval times and SHA-256 hashes.

## Modules

| File | Responsibility |
|---|---|
| `data.py` | Cached public acquisition, mappings, labels, split manifests and source checks |
| `replay.py` | Prefix observations, backward price joins and common snapshot eligibility |
| `model.py` | Train-only Poisson model, pregame initialization and stochastic continuations |
| `metrics.py` | Proper scores, game weighting, paired bootstrap and calibration |
| `evaluate.py` | Fit, validation selection, held-out scoring and prediction exports |
| `report.py` | Offline interactive dashboard and Markdown report |
| `tests/` | Leakage, time joins, score batching, probability math and scoring tests |

## Repository paths

- `app/forecast/`: source, configuration and tests.
- `data/raw/forecast/`: ignored provider cache.
- `data/raw/sports/`: FIFA timelines shared with PR 1 pipelines.
- `data/manifests/forecast/`: fixed splits, joins and source index.
- `data/processed/forecast/`: generated metrics and prediction exports.
- `artifacts/forecast-eval/`: interactive report and review screenshots.

The 20 MB `predictions.csv` is reproducible and excluded from Git. Run the benchmark before using the dashboard’s CSV download link. Committed metrics and the standalone HTML can be reviewed without downloading provider caches.
