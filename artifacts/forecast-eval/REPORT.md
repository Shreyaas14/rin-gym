# World Cup replay forecast benchmark

Built from cached FIFA event timelines and public Polymarket history. Scope: the 72 group-stage games, regulation time plus stoppage time; home/draw/away outcomes.

## Held-out results

20 test games, 2,008 snapshots. Each match receives equal weight. Lower Brier and log loss are better. Brier is the **sum across three classes**, range 0–2; log loss uses natural logs and a 1e-6 floor on the realized-outcome probability.

| Forecaster | Brier | Log loss | 95% match-bootstrap Brier interval |
|---|---:|---:|---|
| Training climatology | 0.7610 | 1.2656 | [0.6400, 0.8820] |
| Pregame market, frozen | 0.4190 | 0.7396 | [0.2711, 0.5758] |
| Poisson · training rates | 0.4529 | 0.7636 | [0.3590, 0.5419] |
| Poisson · pregame initialized | 0.3248 | 0.5749 | [0.2027, 0.4553] |
| Poisson · validation calibrated | 0.3231 | 0.5719 | [0.1957, 0.4606] |
| Live Polymarket | 0.3300 | 0.5824 | [0.2043, 0.4597] |

Calibrated simulator minus live-market Brier: **-0.0069**, paired 95% match-bootstrap interval **[-0.0334, +0.0203]**. Positive means the simulator is worse. This interval measures variation across the sampled games, not uncertainty about source timestamps or generalization to another tournament.

## Split and selection

- Training: June 11–21 UTC, 39 games. Fit goal rates, period-duration distributions and climatology here only.
- Validation: June 22–24 UTC, 13 games. Select one temperature from the fixed grid by equal-match-weighted Brier, using zero-delay observations.
- Test: June 25–28 UTC, 20 games. No parameter selection on this set.
- Selected temperature: **0.85**. Validation results for this selected variant are selection-biased; the held-out test remains the comparison.
- Membership was fixed by calendar date before forecast scoring; concurrent matches are kept in the same split. This is a retrospective chronological holdout, not a prospective or blinded benchmark. Some source matches were inspected in the earlier data audit.

## Models

The simple simulator models future home and away goals as independent Poisson counts. The training-only variant estimates two constant per-minute goal rates from training games. The pregame variant finds a pair of full-match goal intensities that approximates the normalized pregame market distribution, then scales them by estimated remaining time. The calibrated variant applies the temperature selected on validation probabilities.

Remaining time uses training-only empirical half durations conditional on a half still being in progress. It includes stoppage time within each half but excludes halftime. When elapsed time exceeds all training durations, it retains one minute of uncertainty rather than declaring a deterministic result before the whistle. It does not read a test game's future whistle time. It ignores tactics, player strength beyond the pregame prior, red cards, shots and spatial coordinates.

Forecast scores use exact Poisson outcome probabilities, avoiding Monte Carlo noise. The simulator CLI independently samples goal-count continuations and example goal timelines under the same model. Those synthetic outcomes are not used as evaluation labels.

## Replay and timestamp rules

- One snapshot every 60 seconds, starting two minutes after actual kickoff; halftime and completed games are excluded. The two-minute burn-in supports the same snapshots at all tested observation delays.
- Features come from the event prefix at decision time minus 0, 30, 60 or 120 seconds. All delays use the same retained snapshots. Live market prices stay contemporaneous in delay stress tests.
- Pregame probabilities are frozen using samples at or before one minute before actual kickoff. Each must be no older than 180 seconds at that cutoff.
- Live comparison uses only samples at or before decision time, no older than 90 seconds. No future nearest-neighbor matching or interpolation. Missing market snapshots are excluded from **all** compared models: 0 excluded here.
- Independent home/draw/away Yes prices are divided by their sum to form a coherent three-class distribution. Original values, sample timestamps, ages and sums are retained in the CSV. Market normalization can change individual prices.
- Several same-timestamp shot/save/goal events carry conflicting score fields. The harness takes the maximum score within that already-observable batch; it rejects score decreases between different timestamp batches. The mapping manifest records these conflicts.
- Targets come from the FIFA regulation-end score and are cross-checked with the scoreboard and all three contract settlements. Targets are kept outside the forecaster's observation object.

**Timing limitation:** archived event timestamps do not establish first publication or receipt time. Delayed-observation scenarios are stress tests, not a correction that proves live tradability or information availability. Scorekeeping errors, late edits or prices reacting before archived events can affect results.

## Reading the evidence

The main table averages each game's snapshot losses first, then averages the games. Confidence intervals use 4,000 whole-match bootstrap draws. Paired comparisons resample model-minus-market differences on the same matches. Calibration curves are descriptive and also use equal game weights; do not treat thousands of snapshots as thousands of independent outcomes. Shared teams and tournament context can create dependence even between games, so the bootstrap is only an approximate within-tournament uncertainty estimate.

`report.html` lets you inspect delay sensitivity, game-phase/score-margin results, classwise calibration, and individual held-out games. The individual-game chart reveals the full historical forecast path for analysis; it is not an input to any prediction.

## Artifacts

Generated tables and metrics are in `data/processed/forecast/`; the HTML report and screenshots are in `artifacts/forecast-eval/`. The large prediction CSV is regenerated locally and excluded from Git.

- `predictions.csv`: every forecast, target and timestamp audit field; one row per match/snapshot/delay.
- `metrics.json`: aggregate and per-game scores, intervals, paired differences.
- `calibration.json`, `breakdowns.json`, `coverage.json`: diagnostics and exclusions.
- `model.json`, `selection.json`, `pregame_fits.json`: fitted parameters and validation selection.
- `run_manifest.json`: configuration, source-manifest hash, code hashes and assumptions.
- `../../data/manifests/forecast/manifest.json`, `../../data/manifests/forecast/splits.json`: cross-source joins, eligibility and frozen membership.

This is a working benchmark and a simple forecasting baseline. Twenty test games do not establish broad reliability, and reusing this test set for repeated model development would turn it into a validation set.
