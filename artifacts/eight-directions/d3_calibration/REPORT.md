# Direction 3: probability calibration

**Classification: promising for retrospective calibration; negative against the market baseline; pilot probability improvement inconclusive.**

Scalar temperature fitted on 39 earlier development matches reduced Brier and log loss on 33 later development matches. The original and conservative closed-loop arms were fitted independently. Both still underperform the market baseline. The separate 16-match forecast-only pilot shows a small strict-score gain driven by validity rather than improved conditional forecasting.

## Design and provenance

This is a post-hoc development-data analysis. The protocol was persisted before computing aggregate analysis results; source examples were inspected to identify schemas. Only checksummed copies under `inputs` were used. Earlier/later partitions are frozen from timestamps at the June 21, 2026 UTC boundary, with whole matches and all repeats kept together. The historical assessments have already been inspected; they are not untouched evaluations. The pilot reuses 16 of these 72 games and is analyzed separately by base and final model.

Exact intervention: fit a single temperature independently in each closed-loop arm by minimizing equal-match-weight valid-only log loss over T ∈ [0.25, 8], using 80 deterministic golden-section iterations. Freeze T for assessment and compute `softmax(log(clip(p, 1e-6, 1))/T)`. Invalid outputs stay invalid. No temperature is fitted on the pilot assessment cohort. No observations, execution settings, or decisions were changed; this is an offline forecast transformation and makes no claim about counterfactual trading profits.

Primary scores average decisions/repeats inside each match, then average matches. Invalid vectors receive Brier 2 and log loss −log(1e-6). All effect intervals use 4,000 paired whole-match bootstrap draws with seed 20261013. Shared-team dependence is unaccounted. The saved source has three repeats and 1,077 decisions per closed-loop arm: 359 per repeat, because one earlier match has four decisions rather than five. Later assessment contains 495 decisions per arm.

## Assessment comparison

| Arm / audit | Matches | Valid rate | Raw Brier | Calibrated Brier | Market Brier | Raw log loss | Calibrated log loss | Market log loss |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| closed_loop/conservative / strict | 33 | 99.19% | 0.4603 | 0.4365 | 0.3393 | 0.8810 | 0.8407 | 0.5897 |
| closed_loop/conservative / tolerance_0.05 | 33 | 100.00% | 0.4462 | 0.4217 | 0.3393 | 0.7736 | 0.7322 | 0.5897 |
| closed_loop/original / strict | 33 | 99.39% | 0.4566 | 0.4269 | 0.3393 | 0.8580 | 0.8088 | 0.5897 |
| closed_loop/original / tolerance_0.05 | 33 | 100.00% | 0.4460 | 0.4157 | 0.3393 | 0.7774 | 0.7274 | 0.5897 |
| pilot/base / strict | 16 | 73.75% | 0.9791 | — | 0.4010 | 4.3815 | — | 0.6846 |
| pilot/base / tolerance_0.05 | 16 | 100.00% | 0.5727 | — | 0.4010 | 0.9555 | — | 0.6846 |
| pilot/final / strict | 16 | 76.25% | 0.9518 | — | 0.4010 | 4.0734 | — | 0.6846 |
| pilot/final / tolerance_0.05 | 16 | 100.00% | 0.5858 | — | 0.4010 | 0.9772 | — | 0.6846 |

All loss differences below are left minus right; negative is improvement. Arm metrics are not pooled.

| Comparison | Brier effect [95% match CI] | Log-loss effect [95% match CI] |
|---|---:|---:|
| closed_loop/original / strict / calibrated_minus_raw | -0.0298 [-0.0452, -0.0140] | -0.0492 [-0.0731, -0.0244] |
| closed_loop/original / strict / calibrated_minus_market | +0.0875 [+0.0307, +0.1401] | +0.2191 [+0.0786, +0.3849] |
| closed_loop/original / tolerance_0.05 / calibrated_minus_raw | -0.0303 [-0.0462, -0.0144] | -0.0501 [-0.0743, -0.0249] |
| closed_loop/original / tolerance_0.05 / calibrated_minus_market | +0.0763 [+0.0241, +0.1237] | +0.1377 [+0.0537, +0.2130] |
| closed_loop/conservative / strict / calibrated_minus_raw | -0.0238 [-0.0376, -0.0093] | -0.0404 [-0.0616, -0.0183] |
| closed_loop/conservative / strict / calibrated_minus_market | +0.0972 [+0.0374, +0.1514] | +0.2510 [+0.0946, +0.4218] |
| closed_loop/conservative / tolerance_0.05 / calibrated_minus_raw | -0.0245 [-0.0386, -0.0098] | -0.0414 [-0.0629, -0.0191] |
| closed_loop/conservative / tolerance_0.05 / calibrated_minus_market | +0.0823 [+0.0283, +0.1317] | +0.1425 [+0.0545, +0.2215] |
| pilot_strict_final_minus_base | -0.0273 [-0.0796, +0.0134] | -0.3081 [-0.7882, +0.0216] |
| pilot_strict_common_valid_final_minus_base | +0.0084 [+0.0000, +0.0251] | +0.0135 [+0.0000, +0.0405] |
| pilot_tolerance_final_minus_base | +0.0131 [-0.0007, +0.0333] | +0.0217 [-0.0012, +0.0553] |

## Formatting, calibration, and discrimination

The strict probability-vector audit uses unique JSON keys, exactly home/draw/away probability keys, finite numeric values in [0,1], total tolerance 1e-6, and no truncation. It allows the native closed-loop schema to contain its additional fields. Native recorded validity is reported separately: historical closed-loop validity used additional action/forecast fields and a 0.05 sum tolerance. Our uniform tolerance audit changes ONLY the sum tolerance to 0.05 for every arm, explicitly normalizing accepted vectors. It retains exact JSON/duplicate-key checks, so it is not an exact replay of the historical permissive last-object parser. One original earlier-fit completion fails our JSON extraction. The market baseline uses the source task’s explicitly normalized `market_probs`; price slices use the unnormalized contemporaneous `shown_prices`.

Original closed-loop strict validity is 1,073/1,077 overall, versus native recorded 1,077/1,077. Conservative strict validity is 1,072/1,077, versus native recorded 1,077/1,077. Later strict valid rates are 99.39% and 99.19%; the tolerance audit accepts all later forecasts. Temperature changes no validity rate.

For original strict assessment, T = 0.724630; top-class ECE falls from 0.1804 to 0.1274 while top-class accuracy remains 71.31%. For conservative, T = 0.751251; ECE falls from 0.1684 to 0.1223 and accuracy remains 70.89%. T < 1 sharpens probabilities. This supports an underconfidence correction in these development samples. ECE uses fixed bins and valid-only equal-match weights; it is descriptive and was not optimized. Within-row ranking is invariant, so no top-class discrimination gain is claimed. Class-specific AUROC diagnostics and reliability tables are in `results.json`.

Pilot strict base/final validity rises from 59/80 (73.75%) to 61/80 (76.25%). Strict Brier changes by −0.0273 with an interval crossing zero. On the same jointly valid decisions, final minus base Brier is +0.00837; on uniformly tolerance-accepted vectors it is +0.01309 (95% interval −0.00072 to +0.03334). Thus the apparent strict-score gain does not establish better probabilities. These comparisons describe a reused cohort; pilot and closed-loop prompt/model arms are never merged.

## Prespecified slices

`results.json` includes raw/calibrated slices by final outcome, decision phase, top forecast probability, and displayed price of the forecast’s top side. Probability and price ranges are fixed at [0,.2), [.2,.4), [.4,.6), [.6,.8), [.8,1]. Each nonempty slice includes match and decision counts, invalid-inclusive and valid-only losses; overlapping bins across different decisions are descriptive, not separate independent samples. Class-specific probability calibration tables also include match counts and observed frequencies.

| Original strict assessment slice | Matches | Decisions | Raw Brier | Calibrated Brier |
|---|---:|---:|---:|
| result/home | 14 | 210 | 0.3706 | 0.3305 |
| result/draw | 7 | 105 | 0.7157 | 0.7436 |
| result/away | 12 | 180 | 0.4058 | 0.3545 |
| phase/h1+15 | 33 | 99 | 0.5033 | 0.4723 |
| phase/h1+30 | 33 | 99 | 0.5140 | 0.4817 |
| phase/ht | 33 | 99 | 0.4963 | 0.4663 |
| phase/h2+15 | 33 | 99 | 0.4287 | 0.4048 |
| phase/h2+30 | 33 | 99 | 0.3408 | 0.3092 |

## Limitations and next discriminating experiment

The 33 later matches provide a chronological check but remain repeatedly used historical development data. Temperatures, fixed-bin calibration, and secondary slice patterns can overfit this development domain. Tiny slice counts are exposed and should not drive threshold search. The bootstrap clusters repeats within matches but ignores dependence among shared teams. The pilot base/final predictions differ in training and output behavior; conditional/common-valid results help distinguish format from probability improvements without establishing causality.

Next, freeze the original-arm T = 0.724630 and strict parsing before gathering a genuinely new chronological cohort. Compare raw, calibrated, and market forecasts at the same observation times, with paired match intervals and a larger shared-team sensitivity analysis. Complete enrollment and use the existing sealed evaluation workflow before claiming protected evaluation. This study did not enroll or score any protected match.

## Guard, spending, and reproduction

Before/after read-only ledger guard: head `2f6051410929bd283e984574ecce2a944e82aa12911a13d24c194f48dc5a995d`, state `pending_enrollment`, protected matches 0. Actual analysis cohort is 72 allowed IDs. Seal is unchanged. Zero enrollment is pending, not empirical evidence from a protected holdout.

Direction budget: $5 hard cap; reserved $0; estimated new usage $0; actual new provider billing $0; paid requests 0. Existing source-run expenses are not charged again to this offline direction.

Run from `/Users/alexkan/Desktop/frontiers/rin-gym-research-eight`:

```sh
PYTHONPATH=. HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 /Users/alexkan/Desktop/frontiers/rin-gym-forecast-only/.venv/bin/python experiments/eight_directions/d3_calibration/analyze.py
PYTHONPATH=. HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 /Users/alexkan/Desktop/frontiers/rin-gym-forecast-only/.venv/bin/python experiments/eight_directions/d3_calibration/report.py
PYTHONPATH=. HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 /Users/alexkan/Desktop/frontiers/rin-gym-forecast-only/.venv/bin/python experiments/eight_directions/d3_calibration/verify.py
```

Outputs: pre-analysis `protocol.json`, `results.json`, transformed prediction `analysis_receipts.jsonl`, `guard_before.json`, `guard_after.json`, `spending.json`, `reproduction.log`, `verification.json`, and this report. Source checksums and protocol checksum are in results; no source checkout or shared experiment files were changed.
