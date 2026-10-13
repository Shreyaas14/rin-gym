# Direction 5: information timing and compact memory

Classification: cadence **inconclusive**, memory **inconclusive**. Eight fixed chronological development matches, two seeds, three arms with two separate primary contrasts. No checkpoint training or protected evaluation.

Observed forecasts largely repeat the numeric schema example: sparse control79/80 (98.75%), sparse memory80/80 (100%), minute1163/1194 (97.4%) output exactly home0.40/draw0.30/away0.30. This is consistent with example anchoring or output collapse after switching to a compact forecast-only prompt; the mechanism is untested. Validity100% does not establish useful forecasting. Minute Brier0.6272 is far worse than contemporaneous market0.3224 (paired excess0.30476;95%CI[0.14450,0.44489]). The efficacy of cadence/memory remains unresolved, and no profitability/generalization follows from the positive fixed-executor pilot P&L.

Same compact current-observation forecast prompt in all arms. Sparse original five checkpoints versus supported minute updates (first half15–44; halftime; second half1–44). Compact memory adds last three earlier sparse snapshots of observed score, cumulative counts and prices; memory collection uses only original sparse checkpoints. Memory never contains outcomes, future fills or previous model forecasts. The current event window remains defined by prior original sparse checkpoint in every arm.

The primary cadence comparison is an **information-age comparison** on the shared minute clock: sparse predictions carry forward, dense predictions refresh. Original sparse checkpoints are shown separately to control added easy late observations. The primary memory contrast holds sparse cadence fixed. Forecast-only compact prompts consistently replace the original five-field trading template, so this is a cadence pilot with a modified common prompt.

|Clock / arm|Brier|Log loss|Valid rate|Mean forecast age seconds|Market Brier|
|---|---:|---:|---:|---:|---:|
|shared_minute_grid / sparse_no_memory|0.635563|1.054063|1.000|767.2|0.322421|
|shared_minute_grid / sparse_memory|0.640000|1.060132|1.000|767.2|0.322421|
|shared_minute_grid / minute_no_memory|0.627182|1.039817|1.000|0.0|0.322421|
|original_sparse_checkpoints / sparse_no_memory|0.635563|1.054063|1.000|0.0|0.343056|
|original_sparse_checkpoints / sparse_memory|0.640000|1.060132|1.000|0.0|0.343056|
|original_sparse_checkpoints / minute_no_memory|0.635563|1.054063|1.000|0.0|0.343056|

Negative score deltas favor the intervention. Equal match weighting averages minutes then seeds. 95% intervals resample eight whole matches; correlated shared teams and prior development use limit interpretation.

|Clock / contrast|Brier delta [95% CI]|Log-loss delta [95% CI]|
|---|---:|---:|
|shared_minute_grid / cadence_information_age|-0.008380 [-0.025141, 0.000000]|-0.014246 [-0.042737, 0.000000]|
|shared_minute_grid / memory_fixed_sparse|0.004437 [0.000000, 0.013312]|0.006069 [0.000000, 0.018207]|
|original_sparse_checkpoints / cadence_information_age|0.000000 [0.000000, 0.000000]|0.000000 [0.000000, 0.000000]|
|original_sparse_checkpoints / memory_fixed_sparse|0.004437 [0.000000, 0.013312]|0.006069 [0.000000, 0.018207]|

Secondary fixed executor: 0.25 shares, displayed edge >0.05 and all-in cost <=0.65; delayed fills60 seconds, maxwait120, cap0.65; max one entry held to settlement. Pending orders pause later orders. Only execution sees fills; pricing uses identical fees/spread, cancellation assumptions, and risk policy across arms. Turnover is entry shares; entry+settlement also provided in JSON. No-trade P&L=0.

|Arm|Net P&L|Gross losses|Average win|Average loss|Worst loss|Max exposure|Entry turnover|Trade coverage|
|---|---:|---:|---:|---:|---:|---:|---:|---:|
|sparse_no_memory|0.0707|-0.3125|0.1916|-0.0223|-0.0377|0.0584|4.0000|1.0000|
|sparse_memory|0.0707|-0.3125|0.1916|-0.0223|-0.0377|0.0584|4.0000|1.0000|
|minute_no_memory|0.4576|-0.2527|0.1776|-0.0211|-0.0377|0.0865|4.0000|1.0000|

Requests 1354/1354; max-token estimate $0.243916; durable 2x reservations $0.487832; completed full-rate logged-token estimate $0.167468. Actual invoice billing unavailable. $5 direction cap, $0.50 headroom, no SDK/application retries or automatic restarts.

Holdout guard before/after identical: `2f6051410929bd283e984574ecce2a944e82aa12911a13d24c194f48dc5a995d`, state `pending_enrollment`, protected matches 0. No enrollment or protected inference/scoring. All release reads filtered to predeclared eight match IDs; selective-load receipts document table filters.

Coverage: 597/600 requested minute observations; three quotes rejected on contemporaneous price-sum coherence, 40/40 original checkpoints supported. No missing events invented. The late stop at44 minutes excludes stoppage and remains the predeclared limited minute ablation. Historical period boundaries supply original halftime/start labels as in existing replay; it does not establish real-time phase detection. Two seeds do not increase the independent match count. Invalid forecasts get worst Brier2/log loss floor penalty and no trade. Future book liquidity, cash/inventory concurrency and true order routing are not validated.

Next discriminating experiment: freeze more whole matches prospectively, replay truly observed wall-clock phase detection, and test compact memory at fixed sparse cadence while scoring a separately enrolled protected cohort once. No profitability claim follows from this development pilot.

Reproduce scoring with `PYTHONPATH=. HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 /Users/alexkan/Desktop/frontiers/rin-gym-forecast-only/.venv/bin/python -m experiments.eight_directions.d5_timing_memory.analyze`. Live calls cannot automatically restart; `study --live` is guarded by the durable start marker.

Metadata correction: France–Iraq is match `400021492`, with a source halftime gap of 7912.041 seconds (131.87 minutes); the frozen protocol limitation accidentally wrote `400021511`, which is Panama–Croatia. `timing_metadata_correction.json` and `timing_audit.json` document this label correction. Both games remain included in every arm; cohort and scoring are unchanged.

Budget amendment: latest user cap is $5 combined across the entire task. This frozen direction adds maximum-token cost $0.243916 and durable doubled reservations $0.487832; no additional paid work is undertaken. The root aggregate ledger governs the combined cap.

Effect concentration: all nonzero Brier differences come from Portugal–Uzbekistan `400021503`; all net P&L differences come from Norway–Senegal `400021491`. Seven of eight match-level differences are exactly zero for each main effect. This reinforces the small-pilot uncertainty; these matches were never selected or excluded after seeing results.

Next immediate free design check: audit the checkpoint on the original observation template and inspect template-example repetition. A future approved matched pilot should remove numeric schema examples or use the original supported template before spending on more cadence/memory inference; no additional calls were made here.

Memory intervention also adds a history label and increases prompt length, including an empty history at the first point. This pilot tests the complete compact-history prompt intervention; it does not isolate content from instruction/length effects.

Supplementary match-cluster uncertainty against the contemporaneous market baseline is in `market_comparison.json`; negative score deltas favor the model. Baseline comparison does not change the two primary contrasts or classifications.

Complete offline reproduction, including annotations and verification: `PYTHONPATH=. HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 /Users/alexkan/Desktop/frontiers/rin-gym-forecast-only/.venv/bin/python -m experiments.eight_directions.d5_timing_memory.finalize`.
