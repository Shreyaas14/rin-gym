# Offline parlay experiment: no demonstrated model advantage

Cost: **$0**. No model calls, training, ingestion or core harness changes. The holdout ledger guard and state were unchanged (`pending_enrollment`, zero enrolled protected matches). Only existing validation games were scored.

The saved forecasts can be composed into two- and three-leg tickets, but **the market product baseline scored better in every main comparison**. The forecasting-only pilot's base and final checkpoint give identical first-checkpoint results. One hypothetical pilot payout scenario returns a small profit; it does not establish a betting edge.

## Frozen experiment

Primary checkpoint: `h1+15`, before the original replay has entered any position. Each leg predicts the regulation home/draw/away result. Joint probabilities are products of the leg vectors: three cells for singles, nine for pairs and 27 for triples. This assumes independence; it does not measure direct model reasoning about a joint outcome.

The main cohort uses run 3's 72 development games and three saved repeats. Candidate sets include every distinct fixture combination without a shared team: 72 singles, 2,412 pairs and 49,896 triples. Each repeat uses the same repeat index for all legs. Repeats have equal weight; they do not increase the independent game count. The pilot comparison uses its 16 existing validation games, with 114 eligible pairs and 478 triples, and one response per model/checkpoint.

Combination membership depends only on fixture IDs and teams, never final results. `protocol.json` fixed the checkpoint, exclusions, invalid-output penalties, selection rule and pricing scenarios before the scoring loop. SHA-256 fingerprints identify the source tasks, saved predictions and experiment code. This is exploratory development work: the source games and their earlier results were already inspected.

These are phase-matched historical combinations, often from different days. They are not one executable historical slate. No later-game forecast is claimed to have been available for an earlier trade.

## Forecast accuracy

Brier is a proper probability score; lower is better. Compare model versus market **within the same leg count**. The increasing number of outcome cells changes the scale across leg counts.

| Saved forecast source | Legs | Model joint Brier | Market joint Brier | Model favorite-ticket hit rate | Market favorite-ticket hit rate |
|---|---:|---:|---:|---:|---:|
| Run 3 | 1 | 0.5211 | **0.4750** | 62.0% | 63.9% |
| Run 3 | 2 | 0.7745 | **0.7265** | 38.1% | 40.3% |
| Run 3 | 3 | 0.8952 | **0.8588** | 23.1% | 25.2% |
| Pilot base and final, identical | 1 | 0.6400 | **0.4912** | 50.0% | 68.8% |
| Pilot base and final, identical | 2 | 0.8696 | **0.7372** | 24.6% | 46.5% |
| Pilot base and final, identical | 3 | 0.9526 | **0.8624** | 11.7% | 30.8% |

“Favorite ticket” takes the most likely side for each leg; each forecaster may choose a different ticket. It is a hit-rate diagnostic, not the ticket-selection policy used for hypothetical returns. The report also exports binary Brier/calibration for the same market-favorite ticket across all forecasters, providing a common-event comparison.

All primary first-checkpoint forecasts were valid. On all 16 pilot games, both base and trained model returned exactly `home=0.4, draw=0.3, away=0.3`. Those equal the example values in the pilot system prompt. That is consistent with copying the example, although these saved outputs alone cannot establish the cause. The forecasts show no match-specific variation at this checkpoint, and training changes none of the parlay predictions or selections there.

## Dependence and uncertainty

The many overlapping pairs/triples do not provide thousands of independent samples. Headline all-combination metrics are descriptive. An outcome-independent seeded pairing produced 36 disjoint run-3 pairs covering all 72 games. Averaging the three repeat losses within each pair, the model-minus-market two-leg Brier difference is **+0.0848**, with a 4,000-draw pair-bootstrap interval of **[+0.0077, +0.1620]**. Positive means the model scored worse.

That interval describes one development pairing under its bootstrap assumptions; shared teams across pairs and tournament effects can still create dependence. It does not establish prospective performance. No confidence interval treats overlapping ticket rows or repeats as independent observations.

## Later-checkpoint pilot diagnostic

The secondary two-leg diagnostic averages `h1+30`, halftime, `h2+15` and `h2+30` equally, over the same 114 fixture pairs.

| Forecaster | Valid combo responses | Brier including invalid responses | Brier on common-valid cases |
|---|---:|---:|---:|
| Base | 44.5% | 1.4868 | 0.8472 |
| Final | 49.1% | 1.4385 | 0.8560 |
| Market | 100% | **0.6149** | **0.5726** |

Both model legs must be valid for a combo to be valid. The common-valid cohort is the 44.5% of cases valid for every forecaster. Invalid joint outputs receive Brier 2, clipped worst-case log loss, and abstain from execution. The final model's improved failure-inclusive score here comes with better validity; its common-valid probability score is slightly worse than base. Invalid outputs were not silently discarded.

## Hypothetical payout experiment

For each fixture combination, consider every outcome ticket and price it with declared decimal return `D = min(20, 0.95 / market_joint_probability)`, including the returned stake. Select the ticket maximizing `model_probability*D - 1`; enter only if that value exceeds 5%. Invalid forecasts abstain. Realized profit per dollar is `D*all_legs_won - 1`.

The 5% payout haircut and 20x cap are experiment assumptions, not observed sportsbook odds or executable Polymarket quotes. Single-contract fees, fills and prices cannot establish the cost of an AND-payoff contract. Equal ticket weighting yields a hypothetical return per dollar staked, not a cash-constrained deployable portfolio.

| Forecast source | Legs | Selected cases, including repeats | Hypothetical return per dollar staked |
|---|---:|---:|---:|
| Run 3 | 1 | 195 | −60.0% |
| Run 3 | 2 | 7,117 | −25.7% |
| Run 3 | 3 | 126,700 | −16.9% |
| Pilot base/final | 1 | 15 | −40.8% |
| Pilot base/final | 2 | 114 | **+5.0%** |
| Pilot base/final | 3 | 206 | −24.1% |

The pilot pair result is positive under the declared schedule, but is generated from the same constant probabilities on only 16 reused games and is identical before/after training. The 114 tickets overlap extensively; it is not evidence from 114 independent opportunities. It also coexists with much poorer probability scores than the market. The predeclared payout sensitivities return +5.3% with no haircut and +4.1% with a 10% haircut; selection is recomputed for each schedule, so these are different hypothetical ticket portfolios. They do not validate a real price advantage. Three-leg pilot tickets lose money in the primary scenario.

The market-probability selector abstains in every pricing scenario: its own declared prices never imply an edge exceeding 5%. That is a mathematical consequence of this synthetic price construction, not evidence that a market trading strategy earns zero in reality. Compare probability scores against market; compare selected-ticket returns against abstention only within the declared toy execution model.

## What this tells us

The experiment does not support promoting the current checkpoints for parlay selection. Composing existing forecasts does not overcome their marginal forecast weaknesses. The small positive pilot pair result warrants skepticism, especially because it comes from non-varying template probabilities rather than improved forecasts.

The next informative model experiment would require fresh outputs: direct two-match joint forecasts against matched fresh single-match forecasts, or same-game result × next-goal forecasts. The saved marginal probabilities cannot identify a same-game joint distribution. No paid follow-up was started.

## Verification and reproduction

Independent verification passed: 877 scalar checks reconstructed probabilities, scores, ticket selection and payouts directly from original saved responses; aggregate scores and validity were checked across all 317,472 case/model rows; all five source/code fingerprints and the before/after ledger guard matched. Pilot cutoff times, regulation labels and market vectors matched the older reconstructed tasks for all 80 validation checkpoints before using the shared scoring data.

Run from `/Users/alexkan/Desktop/frontiers`:

```sh
rin-gym-forecast-only/.venv/bin/python outputs/parlay-eval-offline/run.py
rin-gym-forecast-only/.venv/bin/python outputs/parlay-eval-offline/verify.py
```

Artifacts: `protocol.json`, `summary.json`, `cases.jsonl.gz`, `verification.json` and `run.log`. The runner imports local offline loaders/guards and NumPy/PyArrow; it contains no inference client or acquisition call. No commit or push was made.
