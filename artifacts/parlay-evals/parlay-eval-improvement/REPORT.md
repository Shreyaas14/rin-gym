# Can we improve two-leg forecasts over the market?

This is Approach A. [Approach B](../parlay-eval-context/REPORT.md) now tests active-play states and event-gated corrections. [APPROACHES.md](../parlay-eval-investigation/APPROACHES.md) documents both designs, controls, results and limitations together.

**Not reliably with the tested offline adjustments.** Calibration substantially improves the saved model forecasts, but the raw market remains the strongest stable probability baseline. A score-aware correction beats it narrowly in one development split and loses in the other two. No checkpoint or recipe is promoted.

Cost: **$0**. No inference, ingestion, training service call, core harness change, commit or push. The protected-cohort ledger guard and state were unchanged. All data comes from the existing 72 historical development matches; these are not untouched holdout results.

## What was tested

The first experiment fixes the original `h1+15` checkpoint, where all three repeats of every game have valid forecasts generated while flat. It compares the full nine-cell joint distribution for two regulation-result legs, formed from the product of per-leg probabilities. Lower joint Brier is the primary objective; log loss, favorite-ticket hit rate and binary Brier on the same market-favorite ticket are secondary diagnostics. This is probability evaluation, not a quote-based profitability test.

Six whole-game folds contain 12 evaluation games each and 60 fitting games. A scored pair must contain two games in the same evaluation fold; pairs sharing a team are excluded. All repeats of a game remain together. Parameters are chosen using fitting games only, averaging three repeats within pair. Three outcome-independent partition seeds test sensitivity to how the small development cohort is divided. Results from different partitions reuse the same games and are not independent replications.

Stage one tests six recipes:

- Raw normalized market probabilities.
- Raw saved model probabilities.
- Market calibration: temperature plus a draw multiplier.
- Model calibration: the same two adjustments.
- A convex market/model blend.
- A calibrated blend, selected from 90 predeclared combinations of model weight, temperature and draw multiplier.

The candidate grid includes the original market with no correction, so the blend can learn to ignore the model. A deterministic tie preference favors no calibration and less model weight. Each outer fitting set selects parameters by two-leg Brier across its eligible training pairs, and those parameters are fixed before scoring that outer evaluation fold. `protocol.json` records the grid, source hashes and membership before scoring.

## Stage-one results

Primary partition, seed `20261013`; all recipes use the same eligible evaluation pairs:

| Recipe | Two-leg joint Brier ↓ | Joint log loss ↓ | Favorite-ticket hit rate |
|---|---:|---:|---:|
| Raw market | **0.7403** | **1.6045** | 39.2% |
| Raw model | 0.7796 | 1.7776 | 38.7% |
| Calibrated market | 0.7570 | 1.6382 | 40.0% |
| Calibrated model | 0.7453 | 1.6379 | 40.3% |
| Market/model blend | 0.7412 | 1.6212 | 39.2% |
| Calibrated blend | 0.7540 | 1.6448 | **40.6%** |

Calibrating the model reduces Brier from 0.7796 to 0.7453, but does not beat the raw market's 0.7403. The small hit-rate improvement for calibrated blend coexists with worse probability scores. It does not imply positive expected value at actual prices.

The calibrated blend includes model signal in all six primary fitting folds: five choose weight 0.5 and one chooses 0.25. This is evidence that model probabilities help the fitting objective, not that they improve generalization.

| Partition seed | Raw market | Calibrated market | Calibrated blend |
|---|---:|---:|---:|
| 20261013 | **0.7403** | 0.7570 | 0.7540 |
| 20261014 | **0.7240** | 0.7389 | 0.7293 |
| 20261015 | **0.7294** | 0.7373 | 0.7316 |

The calibrated blend improves on the recalibrated-market control in all three partitions, but both are worse than the original market. It would be misleading to claim we beat “the baseline” by choosing the weakened control. Calibration on only 60 fit games appears unstable here.

For the primary calibrated-blend recipe, a deterministic evaluation-only subset of 32 disjoint pairs gives a mean Brier difference versus raw market of +0.0147, with a descriptive pair-bootstrap interval of [−0.0191, +0.0507]. Versus calibrated market the difference is −0.0015, interval [−0.0225, +0.0222]. These intervals exclude neither direction. Shared teams across pairs and overlapping fit cohorts remain dependence concerns; the bootstrap does not account for full parameter-selection uncertainty.

## Second exploratory stage: visible score and learned corrections

After inspecting stage one, a separately recorded protocol tests a small ridge regression that predicts a correction to market probabilities. Inputs are the market home-minus-draw and away-minus-draw probabilities, a constant, and the visible score difference. The model arm additionally receives home/draw model-minus-market probability residuals. Outputs are projected onto a valid probability simplex.

Both arms use the same six outer folds and three partition seeds. Ridge strength is selected from six fixed values by three inner whole-game folds within the outer fitting set; only then are coefficients refitted on its 60 fitting games. Inner fitting, inner validation and outer evaluation never share match IDs within their respective comparisons. A market/score-only arm distinguishes model information from baseline correction. This is explicitly a second exploratory experiment, not a confirmatory analysis frozen before all prior results.

| Partition seed | Raw market Brier | Market + score correction | Market + score + model correction |
|---|---:|---:|---:|
| 20261013 | **0.7403** | 0.7471 | 0.7435 |
| 20261014 | 0.7240 | 0.7232 | **0.7227** |
| 20261015 | **0.7294** | 0.7338 | 0.7341 |

In the middle partition, the model-assisted correction improves Brier by about 0.0013 over raw market, roughly 0.18%. It also improves log loss there. In the primary and third partitions it worsens Brier. The market/score-only correction already provides most of the middle partition's gain. These results do not establish a stable model contribution.

The correction can produce zero cell probabilities after projection; clipped log loss penalizes this strongly. In the primary partition its log loss is 1.7251 versus raw market 1.6045. The probability-quality conclusions should consider this alongside the small Brier changes.

## Interpretation and next useful work

The raw model's poor calibration is fixable to a degree, but there is little stable evidence of information beyond the market in these first-checkpoint forecasts. More ticket legs or a search through payout assumptions will not establish that missing information. A score-only correction is also weak because the market already reflects the visible score.

With no new data or inference, the next bounded diagnostic would examine whether the saved model adds value at predeclared active-play states or after events, using whole-game folds and the same market-only control. Later saved forecasts are conditioned on the original replay positions and must retain that limitation. Such a diagnostic could locate useful situations, but a winning subset discovered on these games would need independent confirmation.

For a substantive model improvement, the stronger direction is a forecast objective that learns residual information beyond contemporaneous market probabilities, followed by a frozen execution rule. It could reuse the existing event prefixes and training data, but would require fresh model inference or training and its own budget. No paid run was started here.

We should continue using the raw market as the benchmark, preserve these negative outcomes, and avoid selecting a recipe merely because one split looks favorable.

## Verification and reproduction

Independent scalar scoring reconstructed all nine joint outcome probabilities from source forecasts for 10,071 recipe/pair evaluations, reproducing Brier and log loss. All source/code fingerprints matched. Every evaluation pair was checked against the declared fold membership. In all 36 residual fits, changing only evaluation-game result labels left fitted coefficients exactly unchanged. Before/after protected-cohort guards matched.

From `/Users/alexkan/Desktop/frontiers`:

```sh
rin-gym-forecast-only/.venv/bin/python outputs/parlay-eval-improvement/run.py
rin-gym-forecast-only/.venv/bin/python outputs/parlay-eval-improvement/residual.py
rin-gym-forecast-only/.venv/bin/python outputs/parlay-eval-improvement/verify.py
```

Artifacts include both protocols, parameter receipts, per-pair cases, metrics, logs and `verification.json`. All code is isolated under `outputs/parlay-eval-improvement/`; the active eval chat's source and artifacts were not edited.
