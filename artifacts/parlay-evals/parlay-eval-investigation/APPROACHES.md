# Two approaches to improving two-leg forecasts

Both approaches have now been run offline for **$0**, using existing saved run-3 forecasts and development data. **Neither establishes reliable superiority over raw market probabilities.** The protected-cohort ledger state and guards remained unchanged. No new ingestion, paid inference, training service call, commit or push was performed.

| | Approach A: general forecast correction | Approach B: active-play contexts |
|---|---|---|
| Question | Can calibration or a small correction make the saved probabilities better than market? | Does model information help in particular game states or after visible events? |
| Observations | First active checkpoint, `h1+15`; original position flat | Four active checkpoints; halftime excluded; later original positions vary |
| Methods | Temperature/draw calibration, market/model blending, ridge corrections using market, visible score and model residuals | Fixed context slices, within-context calibration/blending, event-gated correction with market elsewhere |
| Controls | Raw market, calibrated market, market/score-only ridge | Raw market, calibrated market, market-only event gate |
| Evaluation | Six whole-game folds, three partition seeds; both legs in one evaluation fold | Same match folds/seeds; prefix-only groups; both legs in one evaluation fold |
| Main result | Model calibration helps, but loses to raw market; ridge wins narrowly in only one partition | Event gate loses to raw market in all three partitions |
| Exploratory lead | Conditional residual information may exist, but gains are unstable | Both-games-tied blend improves in two partitions and worsens more in the third |
| Detail and reproduction | [Approach A report](../parlay-eval-improvement/REPORT.md) | [Approach B report](../parlay-eval-context/REPORT.md) |

## Approach A: results

Primary first-checkpoint partition: raw model Brier 0.7796; calibrated model 0.7453; score-aware model correction 0.7435; raw market **0.7403**. Calibration recovers much of the model's forecast deficit. The score-aware correction beats raw market by about 0.0013 in one alternate partition, but loses in the primary and third partitions. Its market/score-only control provides most of the alternate partition's gain.

The calibrated blend outperforms a recalibrated-market control in all three partitions but loses to the original market in all three. That is not sufficient to say the baseline was beaten; the control's recalibration itself made it worse.

## Approach B: results

Across four active checkpoints, raw market versus event-gated blend Brier is **0.6666 vs 0.6706**, **0.6477 vs 0.6522**, and **0.6526 vs 0.6549** across the three partitions. Lower is better. Fourteen of 18 event-gate fitting folds discard the model signal entirely.

For both games tied, raw market versus calibrated blend is **0.8261 vs 0.8162**, **0.8259 vs 0.8470**, and **0.8240 vs 0.8214**. This is a possible direction for further study, not a validated improvement. Recent-goal and red-card groups do not show superiority; red-card coverage is only five triggering games despite many derived pairs.

Approach A and B score different checkpoint cohorts. Do not compare their absolute Brier values and conclude B is better merely because later games are easier to predict. Every method must be compared against the market on its own identical observations and tickets.

## What the evidence supports

The stable benchmark remains raw market probabilities. The next focused model experiment could test both-tied states with fresh forecasts and the same market-only control, but these previously inspected games cannot serve as untouched confirmatory data. A larger leg count or searching for a profitable historical subset would not fix the evidentiary gap.

Both approaches measure independent products of per-leg probability vectors, with full nine-cell Brier as the main score. Neither evaluates fresh joint model reasoning, actual executable Combo quotes or a changed closed-loop betting policy. Saved later signals depend on their original positions. Repeated/overlapping tickets do not increase the independent game count. All positive slices are exploratory and must be documented together with the negative aggregate results.

## Verification

Approach A: 10,071 independent source-based joint-score checks; 36 residual fits unchanged under evaluation-label mutation; source hashes, fold separation and holdout guard verified.

Approach B: 78,038 independent recipe/pair-score checks; all 287 visible event prefixes unchanged when future events were removed or corrupted; aggregate metrics, representative fit isolation, source hashes and holdout guard verified.

The report links above contain the exact CLI commands and artifacts needed to rerun each approach.

## Fresh follow-up: both games tied

The authorized fresh Tinker comparison is complete; see the [fresh joint forecast report](../parlay-eval-fresh-joint/REPORT.md). It used 24 disjoint pairs, 48 development games, three repeats, and matched fresh standalone controls. Estimated full-rate token cost was $0.0411; the protected-cohort state was unchanged.

Direct nine-cell joint output failed normalization on 65 of 72 responses and did not beat market. Unconditional post-hoc repair still underperformed. Fresh standalone products were all valid and scored 0.8307 versus market 0.8344, but the mean difference of −0.0037 had a bootstrap interval spanning zero. That small control gain is an exploratory direction, not confirmed superiority. This paid follow-up is separate from the two original $0 offline approaches above.

## Expanded fresh-single follow-up

The [expanded study](../parlay-eval-fresh-singles-expanded/REPORT.md) issued 396 fresh single forecasts across all 132 eligible tied observations in 54 existing development games, costing an estimated $0.0595. It evaluates 2,146 additional pair/checkpoint combinations beyond the original 24. The protected-cohort guard remained unchanged.

The frozen equal-phase, failure-inclusive Brier is model **0.8479** versus market **0.8276**. Three invalid responses at one late-game observation propagate into 24 additional combinations. Validity-matched and market-fallback diagnostics retain a small favorable point estimate (fallback **0.8253 vs 0.8276**), but game-level bootstrap intervals span zero. The expansion does not establish an advantage. The original 24 pairs under new outputs are effectively tied with market.

The expanded combinations are dependent and mostly reuse the original games: only six fixture IDs and 84 match/checkpoint observations are new to this fresh-testing sequence. The report keeps the frozen negative result separate from post-hoc reliability diagnostics and documents all costs, controls and reproduction commands.
