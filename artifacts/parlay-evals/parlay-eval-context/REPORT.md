# Approach B: active-play contexts and event-gated two-leg forecasts

**Result: no reliable advantage over the raw market baseline.** Event-gated calibration worsened aggregate two-leg Brier in every development partition. The both-games-tied slice is an exploratory lead: its calibrated blend improved in two partitions, but worsened in the third. No method is promoted.

Cost: **$0**. No inference, training service, ingestion or harness change. The protected-cohort guard and ledger state were unchanged (`pending_enrollment`, zero enrolled protected matches). Existing test/demo games were excluded.

This extends [Approach A: calibration and score corrections](../parlay-eval-improvement/REPORT.md). A joint overview of both approaches is in [APPROACHES.md](../parlay-eval-investigation/APPROACHES.md).

## Design fixed before context scoring

Use run 3's saved forecasts on 72 development games and three repeats at four active-play checkpoints: `h1+15`, `h1+30`, `h2+15`, `h2+30`. Exclude halftime. One game lacks the final checkpoint, leaving 287 unique match-checkpoints and 861 saved responses.

Use the same three seeded six-fold whole-match partitions as Approach A. Both legs of a scored ticket must be in the same evaluation fold; both legs of a fitting ticket must be in its fitting fold. Exclude shared-team fixture pairs. Repeat indices match across legs and are averaged before reporting scores. Parameters are never fitted on the evaluation games' outcomes.

Freeze eight overlapping groups: all active play; first half; second half; both games tied; a recent visible goal on either leg; a visible red card on either leg; uncertain market on either leg; and quiet pairs. Recent means the five minutes ending at the delayed observation cutoff. Uncertain means maximum normalized market probability below 0.6. Quiet means neither leg has a recent goal or a visible red card.

Prefix flags are reconstructed from the existing frozen release, projected to development IDs only. Event cutoff is decision time minus the original configured event delay. Goals are identified by increases in visible score fields, including own goals credited to the benefiting side; red cards include `red_card` and `second_yellow`. Every reconstructed score agrees with its saved task's observed score. Future events and final labels never determine group membership.

In each group, compare raw model and market against a group-fitted calibrated market and calibrated blend. The grid matches Approach A: model weight, temperature and draw multiplier. Require at least 20 fitting fixture IDs and 30 fitting pair-points, otherwise fall back to raw market. Counts include both triggering and partner games; they do not guarantee 20 independent event-triggering games.

The primary policy uses raw market for quiet pairs and a calibrated blend fitted only on event-pair training points for pairs with a visible goal or red card. An otherwise identical market-only event gate controls for calibration. This primary policy is frozen before scoring; it is not assembled afterward from whichever groups win.

The main metric is nine-cell joint Brier. Log loss, favorite-ticket hit rate and binary Brier on a common market-favorite ticket are secondary. This tests product probability forecasts, not direct joint reasoning, real Combo pricing or a changed betting policy.

## Primary policy results

Lower Brier is better. Each row compares methods on exactly the same evaluation pair-points.

| Partition seed | Pair-points | Raw market | Raw model | Event-gated market | Event-gated blend |
|---|---:|---:|---:|---:|---:|
| 20261013 | 1,469 | **0.6666** | 0.7231 | 0.6676 | 0.6706 |
| 20261014 | 1,479 | **0.6477** | 0.7183 | 0.6522 | 0.6522 |
| 20261015 | 1,497 | **0.6526** | 0.7172 | 0.6555 | 0.6549 |

The blend loses to raw market in every partition. Fourteen of the 18 event-gate fitting folds select model weight zero; three select 0.1 and one selects 0.25. Recent-goal-specific fits select zero model weight in 17 of 18 folds. Adding the saved model signal does not reliably help after these events.

## Context-specific results

Primary partition, seed `20261013`; lower joint Brier is better:

| Context | Evaluation pair-points | Raw market | Calibrated market | Calibrated blend |
|---|---:|---:|---:|---:|
| First half | 740 | **0.7267** | 0.7416 | 0.7480 |
| Second half | 729 | **0.6055** | 0.6114 | 0.6114 |
| Both games tied | 335 | 0.8261 | 0.8212 | **0.8162** |
| Recent visible goal, either leg | 308 | **0.6183** | 0.6215 | 0.6215 |
| Visible red card, either leg | 113 | **0.4682** | 0.5017 | 0.5067 |
| Uncertain market, either leg | 933 | 0.7680 | **0.7643** | 0.7672 |
| Quiet | 1,076 | **0.6944** | 0.7007 | 0.6995 |

The raw model does not beat raw market in any of these primary-partition slices. The uncertain-market blend is slightly better than raw market in the primary partition, but worse than market-only calibration, and it loses to raw market in both alternate partitions.

The both-tied slice has the most useful potential signal:

| Partition seed | Raw market | Calibrated blend | Blend minus market |
|---|---:|---:|---:|
| 20261013 | 0.8261 | **0.8162** | −0.0098 |
| 20261014 | **0.8259** | 0.8470 | +0.0211 |
| 20261015 | 0.8240 | **0.8214** | −0.0026 |

Negative is improvement. Log loss improves in the same two partitions and worsens in the other. Both-tied fitting folds retain model weight 0.5 in 15 folds and 0.25 in three, suggesting a conditional training signal. The evaluation results are unstable: the larger loss in the middle partition outweighs the gains elsewhere. These are repeated partitions of the same games, not three independent tournaments, and groups overlap. No multiple-comparison-adjusted significance claim is made.

## Coverage and evidence limits

- Only 31 unique checkpoints in 29 games fall within five minutes after a visible goal. Sparse checkpoints cannot assess rapid post-event adaptation or whether a short-lived trading opportunity existed between them.
- Visible red cards occur at 11 checkpoints in only five games. Pairing those games with other fixtures produces hundreds of pair-points, but does not create hundreds of independent red-card examples.
- Across all phases there are 9,581 eligible pair-points, reusing 2,412 distinct fixture pairs and 72 games. Headline split results are descriptive; pair-points, repeated forecasts and checkpoints are dependent.
- All 216 first-checkpoint responses were generated while flat. At later checkpoints many saved responses were conditioned on holdings: only 115 of 216 `h1+30`, 29 of 216 `h2+15` and 20 of 213 `h2+30` responses were flat. We evaluate those frozen signals; we did not ask the model to react to new positions under the event-gated policy.
- These matches were reused during original model development and prior investigations. Whole-game fitting/evaluation separation prevents new correction-fitting leakage, but cannot undo that historical reuse or establish prospective generalization.
- Reconstructed event times are not verified first-publication times. Keeping event delay preserves the original retrospective observation assumptions, not a proven live feed information set.
- Results are phase-matched synthetic combinations, often from different dates. Actual Combo quotes, simultaneous availability and executable liquidity are not evaluated.

The next justified step, if fresh inference is authorized, is a tightly scoped both-tied forecasting comparison with a market-only control and correctly aligned observations. These offline results support investigating that state, not deploying the fitted blend or claiming it is better overall. No paid follow-up was started.

## Verification and reproduction

Verification passed: 287 event prefixes were unchanged after future rows were removed or their score/card fields corrupted; 78,038 recipe/pair scores were independently reconstructed from all nine joint outcome cells; 102 aggregate metric rows matched; representative parameter selections remained unchanged after altering evaluation-game labels; all source/code fingerprints and holdout guards matched.

From `/Users/alexkan/Desktop/frontiers`:

```sh
rin-gym-forecast-only/.venv/bin/python outputs/parlay-eval-context/run_context.py
rin-gym-forecast-only/.venv/bin/python outputs/parlay-eval-context/verify_context.py
```

Saved artifacts include `protocol.json`, prefix-only `features.json`, `summary.json`, `cases.jsonl`, fitting receipts, `verification.json` and `run.log`. All experiment code is isolated under `outputs/parlay-eval-context/`. The existing eval harness and active eval chat's files were not edited.
