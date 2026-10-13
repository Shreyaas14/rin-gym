# Fresh joint forecasts when both games are tied

Follow-up: the [expanded fresh-single test](../parlay-eval-fresh-singles-expanded/REPORT.md) now covers all 132 eligible tied observations. Its frozen result does not establish improvement; the small favorable single-signal point estimate remains uncertain after validity/fallback diagnostics.

**Direct joint forecasting did not beat the market baseline.** The trained checkpoint returned all nine joint outcome cells, but 65 of 72 joint responses failed the frozen probability-sum validation. Fresh standalone forecasts were all valid; their products showed a small, uncertain improvement over market on the same pairs.

Estimated full-rate token cost: **$0.0411**, well below the authorized $5 budget. The entire maximum-output reservation was **$0.08335**. These are logged-token estimates, not a provider invoice; they exclude cache discounts. Rates were verified against [Tinker's published Qwen3-8B pricing](https://tinker-docs.thinkingmachines.ai/tinker/models/models_and_pricing/).

The protected-cohort ledger guard was identical before and after every request and at completion. It remains `pending_enrollment`, with zero enrolled protected matches. Existing test/demo games were excluded. No enrollment, protected forecast, retraining, data ingestion, trade, commit or push occurred.

## Frozen experiment

- Checkpoint: run 3 final, `tinker://d2426bc1-ec71-559d-bb81-e17da9372e58:train:0/sampler_weights/final`.
- Cohort: 24 outcome-independent seeded fixture pairs, covering 48 distinct development games; no game appears in more than one pair and no pair shares a team.
- Both observations have a tied score at the original delayed event cutoff. Both legs use the same active-play checkpoint. Pair selection ignores eventual regulation results.
- Phase coverage: 14 pairs at `h1+15`, six at `h1+30`, one at `h2+15`, three at `h2+30`.
- Three fresh repeats at temperature 1, fixed seeds, 384 output-token cap, and the existing thinking-disabled renderer.
- Per repeat: one direct joint request, one standalone request for leg A, and one for leg B. Total: **216 forecast requests**.
- Joint schema: nine named cells for A result × B result, covering home/draw/away. Singles emit their three result probabilities. No numeric probability example is provided in either prompt.
- Both arms receive the same existing flat observation text, including visible event prefixes and displayed market history. Buying costs, position and action instructions are removed. Hidden labels, delayed fills and scoring objects are never transmitted.
- Baseline: independent product of normalized contemporaneous market result probabilities, evaluated on exactly the same cases.
- Validation: exact schema, finite values in [0,1], total probability within 0.02 of one, then renormalize the accepted small rounding error. Invalid or truncated outputs receive joint Brier 2 and clipped worst-case log loss. No inference retry or repair replaces an invalid primary result.
- Primary score: nine-cell Brier, averaged over repeats within pair and then over the 24 disjoint pairs. Secondary: log loss, validity, matched fresh-single product and own-marginal product diagnostic.

`protocol.json`, source/code hashes, all prompts, selected cases and maximum-token preflight were saved before inference. Five parser and isolation tests passed before sending requests. The user explicitly approved transmission to Tinker using the configured account.

## Results

Lower scores are better. Failure-inclusive primary results:

| Forecaster | Valid responses | Joint Brier | Joint log loss |
|---|---:|---:|---:|
| Direct joint prompt | 7/72, **9.7%** | 1.8903 | 12.6709 |
| Product of fresh standalone forecasts | 72/72, **100%** | **0.8307** | **1.8818** |
| Product of market probabilities | 72/72, **100%** | 0.8344 | 1.8924 |

All 144 individual standalone outputs were valid. There were no API failures and no truncations. Every joint output had the full numeric nine-cell schema; 65 failed because probability mass was outside the predeclared tolerance. Total emitted mass across the 72 joint responses ranged from **0.45 to 1.501**. Returning the requested keys is therefore insufficient for using these outputs as joint probabilities.

The failure-inclusive direct-joint minus market Brier difference is **+1.0559**, with a 4,000-draw disjoint-pair bootstrap interval of **[+0.9085, +1.1938]**. Positive is worse. This mainly reflects the reliability failure, rather than isolating the accuracy of an otherwise usable joint model.

On the seven jointly valid repeat-cases, joint Brier is 0.8721, versus 0.9284 for both matched controls. That validity-selected tiny subset must not be used to claim superiority. It excludes most attempted outputs and does not represent the full frozen experiment.

## Small positive control result

The product of fresh standalone forecasts improves Brier from market's 0.8344 to **0.8307**: a mean difference of **−0.0037**, roughly 0.44% relative improvement. The disjoint-pair bootstrap interval is **[−0.0119, +0.0039]**, including zero. Log-loss difference is −0.0106, interval [−0.0312, +0.0077].

Favorite-ticket hit rate is 31.9% for fresh single products and 29.2% for market; this is descriptive, and the forecasters can choose different tickets. None of these diagnostics establishes a profitable edge at actual Combo quotes.

The standalone product is a more usable candidate than the direct nine-cell prompt under the current output contract. The small gain is a lead for further evaluation, not a validated improvement. It differs from prior offline calibration tests because it uses fresh, forecast-only, position-free prompts with no numeric example; these results do not isolate which prompt or state change caused the difference.

## Explicit post-hoc normalization diagnostic

For debugging only, normalize every full-schema positive-mass joint response regardless of its original total. All 72 were repairable this way. The resulting Brier is **0.8912**, still worse than market **0.8344** and fresh singles **0.8307** on the same cases.

This diagnostic was added after observing the sum failures. It changes the frozen acceptance rule and is **not** the primary result or a successful valid-output rate. It suggests that fixing normalization alone would not make these saved direct-joint outputs superior on this sample. A future run could explicitly request nonnegative weights with deterministic normalization, or use a constrained probability output representation, but its results would be a new experiment. No additional paid run was made.

## Scope and limitations

These are phase-matched retrospective pairs, often from different dates; the two cutoffs are not one actual simultaneous historical trading time. The task tests joint forecasting composition, not live execution. Observations come from the existing frozen development release and retain its event-delay assumptions; historical event time is not verified publication time.

The 48 fixtures are distinct across pairs, but teams can recur across different pairs. Bootstrap intervals average repeats within pair and resample 24 pairs; they do not account for all shared-team/tournament dependence. The cohort is small and mostly early first-half states.

The original model and these historical games were already used in development, and the both-tied hypothesis came from inspected context experiments. Fresh inference is not an untouched holdout or prospective demonstration. No actual Combo quote, liquidity, fill or payout was used, and no P&L result is claimed.

An initial sandboxed attempt stalled during sampling-client initialization and was stopped before any forecast request receipts existed. Its marker is preserved in `STARTED-network-stall.json`. After explicit Tinker transmission approval, the network-enabled run made exactly the frozen 216 requests. `connection-recovery.json` records this setup recovery; no duplicate forecast run occurred.

## Verification and reproduction

Verification passed: all 216 request/prediction receipts matched; 288 forecast scores were independently reconstructed from full joint cells; prompts matched the frozen safe source tasks; all 48 game IDs were distinct; source/code fingerprints, token reservations and holdout guards matched. All five offline unit tests passed.

Actual logged usage: **172,206 input tokens and 12,602 output tokens**. Full-rate estimate: `(172206 × 0.195 + 12602 × 0.60) / 1,000,000 = $0.04114137`. The configured request reservation ceiling is $2.50; the maximum-output reservation used was only $0.08335, leaving substantial margin below $5. This is an estimate and application reservation, not a provider-enforced dollar billing cap.

From `/Users/alexkan/Desktop/frontiers`, recompute existing results without inference:

```sh
rin-gym-forecast-only/.venv/bin/python outputs/parlay-eval-fresh-joint/score.py
rin-gym-forecast-only/.venv/bin/python outputs/parlay-eval-fresh-joint/verify.py
rin-gym-forecast-only/.venv/bin/python outputs/parlay-eval-fresh-joint/normalization_diagnostic.py
```

Tests from the experiment directory:

```sh
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 ../../rin-gym-forecast-only/.venv/bin/python -m unittest test_experiment -v
```

The paid command used was:

```sh
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 rin-gym-forecast-only/.venv/bin/python -u outputs/parlay-eval-fresh-joint/experiment.py --live
```

It now refuses to restart because `STARTED.json` exists. Do not remove the marker or rerun the paid experiment merely to reproduce the report. Prepared prompts, protocol, receipts, completions, scored cases, metrics, diagnostic and verification are saved alongside this document.
