# Expanded fresh single forecasts for both-games-tied combinations

**The expansion does not establish an improvement over market.** The frozen failure-inclusive score is worse. Validity-matched and market-fallback diagnostics retain a small favorable point estimate, but their game-level uncertainty intervals include zero. No method is promoted.

Estimated full-rate token cost: **$0.0595**, below the authorized $5 budget. The maximum-output reservation was **$0.14236**. These are logged-token estimates, not a provider invoice; no cache discounts are assumed. Rates follow [Tinker's published Qwen3-8B pricing](https://tinker-docs.thinkingmachines.ai/tinker/models/models_and_pricing/).

The protected-cohort ledger guard and state were unchanged before, during and after the run. Enrollment remains pending; zero protected matches are enrolled. Existing test/demo games were excluded. No retraining, acquisition, actual trades, commit or push occurred.

## Frozen expansion

Use the exact checkpoint, single-forecast prompt, parser tolerance, temperature and output cap from the [initial fresh comparison](../parlay-eval-fresh-joint/REPORT.md): run 3 final Qwen3-8B, temperature 1, three repeats, thinking disabled, 384 output tokens, and probability-sum tolerance 0.02. No numeric probability example is included. No parameter fitting, prompt tuning or outcome-based case selection is performed in this expansion.

Select **all 132 eligible tied observations across 54 existing development games** at the four active-play checkpoints. Visible score is tied at the original delayed event cutoff, and each observation has an eligible same-phase partner without a shared team. The original flat observation text is stripped of positions/actions and supplied independently to the model. Hidden regulation labels and scoring objects never reach the inference request.

Freshly issue each observation three times: **396 requests**. Reuse each response across its eligible combinations rather than paying for a duplicate forecast per ticket. Those responses produce **2,170 pair/checkpoint combinations**, including **2,146 additional combinations** after excluding the original 24 exact fixture-pair/checkpoint cases.

| Checkpoint | Tied single observations | Additional pair/checkpoint combinations |
|---|---:|---:|
| `h1+15` | 49 | 1,094 |
| `h1+30` | 33 | 490 |
| `h2+15` | 23 | 239 |
| `h2+30` | 27 | 323 |

The original study used 48 games at one observation each. This expansion adds 84 previously untested match/checkpoint observations but only **six additional game IDs**. Most new combinations therefore reuse previously seen fixtures; this is not 2,146 independent examples or an untouched confirmation set.

The primary metric excludes the original 24 combinations, averages repeats within pair, pairs within phase, then gives the four phases equal weight. This prevents the many early-game combinations from dominating the aggregate. Pair-count-weighted results and each phase are also reported.

Uncertainty resamples the **54 underlying game IDs** 4,000 times and recomposes pair weights from their fixture multiplicities, preserving the fact that one forecast is shared across many tickets. It does not bootstrap ticket rows as independent examples. Shared-team and tournament dependence remains outside this approximation.

Protocol, source/code hashes, selected observations, pair membership and maximum-token preflight were saved before inference. Four preparation tests passed. The run made exactly the frozen 396 calls with no application retries or paid reruns.

## Primary results: additional combinations

Lower scores are better. All invalid outputs remain in the primary score.

| Forecaster | Equal-phase joint Brier | Equal-phase joint log loss |
|---|---:|---:|
| Product of fresh singles | 0.8479 | 2.1130 |
| Product of market probabilities | **0.8276** | **1.8893** |

Mean model-minus-market Brier is **+0.0203**, with a game-bootstrap interval of **[−0.0087, +0.0711]**. The point estimate is worse, but the interval does not establish a precise deterioration. Log-loss difference is +0.2237, interval [−0.0223, +0.7142].

Pair-count weighting also favors market: model Brier **0.8389** versus market **0.8271**. The negative primary result is therefore not merely a consequence of the equal-phase weighting choice.

| Checkpoint | Model Brier | Market Brier | Model minus market |
|---|---:|---:|---:|
| `h1+15` | **0.8251** | 0.8278 | −0.0027 |
| `h1+30` | 0.8334 | **0.8283** | +0.0051 |
| `h2+15` | **0.8405** | 0.8451 | −0.0046 |
| `h2+30` | 0.8927 | **0.8094** | +0.0834 |

These are predeclared phase diagnostics, not separately validated winning strategies. Phase rows share games and cannot be treated as independent studies.

Single-forecast Brier, averaged within each game and then across 54 games, is also worse than market: **0.5371 versus 0.5233**, including failures.

## Output reliability explains much of the late-game deficit

**393 of 396 single responses were valid: 99.24%.** There were no API failures or truncations. All three invalid responses came from `400021509:h2+30`; each returned:

```json
{"probs":{"home":0.435,"draw":0.455,"away":0.0875}}
```

The sum is **0.9775**, 2.25% below one and just outside the frozen 2% tolerance. The output cannot be accepted under that unchanged rule. Each invalid leg invalidates every product containing it, affecting 24 additional late-game pair-points and their three repeats. Assigning Brier 2 to those failures produces much of the final-phase penalty.

This illustrates why reusing many combinations does not create independent evidence: one observation failure propagates across dozens of apparent cases. The game-level bootstrap reflects shared fixture IDs; a ticket-row bootstrap would understate that coupling.

## Post-hoc diagnostics, separate from the frozen result

Two no-cost diagnostics were added after observing the three failures. Neither replaces the primary acceptance rule or score.

| Diagnostic | Model Brier | Market on identical cases | Difference | Game-bootstrap interval |
|---|---:|---:|---:|---|
| Common valid cases only | **0.8257** | 0.8281 | −0.0024 | [−0.0112, +0.0065] |
| Invalid leg falls back to its market vector | **0.8253** | 0.8276 | −0.0023 | [−0.0111, +0.0065] |

The common-valid diagnostic excludes the 24 affected additional combinations, leaving 2,122. Conditioning on output validity can bias the cohort, so it is diagnostic rather than headline performance.

The fallback uses only the invalid-output flag and the contemporaneous market probabilities for that leg; it never sees results or future prices. It retains all 2,146 additional combinations. This is a plausible reliability wrapper, but was not the frozen primary recipe and remains a post-hoc candidate. No additional inference was required.

Both diagnostics preserve a tiny positive point estimate, approximately a 0.28% Brier reduction for fallback, but both uncertainty intervals span no improvement. The early small gain has not disappeared entirely at the probability-signal level; it has **not become a demonstrated advantage** either.

## Rechecking the original 24 pairs

Under fresh outputs from this run, the original 24 pair/checkpoint cases score **0.83443** for the model versus **0.83441** for market when equally weighting those pairs, effectively a tie. The initial run scored model 0.83071 versus market 0.83441. Different fixed sampling seeds and fresh stochastic responses were used here, so this is a sensitivity check rather than exact reproduction. It reinforces that the original favorable difference was small and unstable.

## What this supports

The usable next candidate is a frozen single-forecast-plus-market-fallback pipeline, with explicit validation and further evaluation. These data do not justify claiming it beats market or increasing ticket risk/leg count on that assumption. This experiment does not retest direct joint output; the prior joint reliability failure remains separately documented.

The current evidence is retrospective and comes from already inspected development fixtures. The original checkpoint was developed using this tournament; new outputs cannot undo that reuse or rule out memorization. All four phases and repeats reuse regulation outcomes. Combinations are phase-matched synthetic pairs, often from different days; actual Combo quotes, simultaneous information availability, liquidity and P&L were not tested. Reconstructed event times remain subject to the existing final-feed/publication-time limitation.

## Verification, cost and reproduction

Independent verification passed: **13,020 joint scores** were reconstructed from the original single outputs; 396 receipt pairs, all 132 safe observation prompts, 2,170 pair memberships, the original-case exclusion, equal-phase aggregates, game-bootstrap intervals and post-hoc diagnostic scores matched. Source/code fingerprints, token reservations and holdout guards matched. Four preparation tests passed.

Logged usage: **262,167 input tokens and 13,953 output tokens**. Full-rate estimate:

`(262167 × 0.195 + 13953 × 0.60) / 1,000,000 = $0.059494365`

The maximum-output reservation was $0.14236, below the application ceiling of $2.50 and user budget of $5. This is a token estimate/reservation, not a provider-enforced billing cap or invoice.

From `/Users/alexkan/Desktop/frontiers`, recompute without inference:

```sh
rin-gym-forecast-only/.venv/bin/python outputs/parlay-eval-fresh-singles-expanded/score_expanded.py
rin-gym-forecast-only/.venv/bin/python outputs/parlay-eval-fresh-singles-expanded/diagnostics.py
rin-gym-forecast-only/.venv/bin/python outputs/parlay-eval-fresh-singles-expanded/verify_expanded.py
```

Tests from the experiment directory:

```sh
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 ../../rin-gym-forecast-only/.venv/bin/python -m unittest test_expanded -v
```

The paid command used was:

```sh
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 rin-gym-forecast-only/.venv/bin/python -u outputs/parlay-eval-fresh-singles-expanded/expanded.py --live
```

The runner refuses a paid restart because `STARTED.json` exists. Do not remove that marker to reproduce the report. Protocol, prompts, selected observations, pairs, prediction/request receipts, per-pair and per-game scores, metrics, diagnostics, verification and logs are saved alongside this document. Existing harness files and the other eval chat's artifacts were not edited.
