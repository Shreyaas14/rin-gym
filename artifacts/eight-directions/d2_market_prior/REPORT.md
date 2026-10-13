# Direction 2: Market-prior forecasting

**Classification: negative for the fitted blend’s incremental value over market on this development assessment.** The blend improves the original model head but is worse than the contemporaneous market on both proper scores. Fresh explicit-prior prompting is a separately predeclared component; its status is completed; see fresh_results.json and matched-study section.

## Hypothesis, intervention and provenance

A model may add small useful corrections to a market prior. We used the original arm of the completed closed-loop replay: 72 previously inspected historical World Cup development matches, three sampled trajectories, 1,077 forecast points. Market probabilities come from the normalized displayed mids at each exact observed timestamp. All task-specific event/price cutoffs and delays remain frozen (`event_delay_s=60`, `fill_delay_s=60`, `price_max_age_s=90`). No outcome-based exclusions were made.

The original model already sees current and historical market prices and buying costs. Its standalone head is **market-visible**, not a market-blind model. Prompt availability is not evidence of incremental forecast value. `verification.json` reconstructs all 1,077 original prompt hashes from the source task messages. Source copies and checksums are pinned in `protocol.json` and `inputs/MANIFEST.json`.

A single convex blend, `p=(1-alpha)*market+alpha*model`, was fitted by the analytical minimizer of equally match-weighted multiclass Brier loss on the frozen earlier 39 games. The frozen boundary is 2026-06-21 UTC. All decisions and repeats stay within each match partition. The fitted model weight is **0.400042** (market weight 0.599958). The later 33 games assess the frozen weight. This is a post-hoc research direction with a protocol saved before computing its results; these games have been repeatedly inspected in prior work.

## Proper scores (lower is better)

| Forecast | Fitting Brier (39 games) | Assessment Brier (33 games) | Assessment log loss | Valid rate |
|---|---:|---:|---:|---:|
| market_only | 0.476336 | 0.339342 | 0.589719 | 100.0% |
| original_model_with_market_visible | 0.487696 | 0.446011 | 0.777433 | 100.0% |
| fitted_blend | 0.467242 | 0.364533 | 0.649232 | 100.0% |

Brier is the three-outcome sum, not divided by three. Log loss uses a `1e-4` floor for every arm. Invalid original outputs would receive Brier 2, log loss `-log(1e-4)` and no trade; blend preserves invalidity rather than silently replacing failures with market forecasts. There were no invalid source forecasts. Decisions and three repeats are averaged within match before averaging across games.

| Assessment paired comparison | Brier delta, 95% interval | Log-loss delta, 95% interval |
|---|---:|---:|
| original_model_with_market_visible_minus_market_only | +0.106670 [+0.051199, +0.158322] | +0.187714 [+0.100685, +0.268540] |
| fitted_blend_minus_market_only | +0.025191 [+0.004726, +0.043932] | +0.059513 [+0.024641, +0.091458] |
| fitted_blend_minus_original_model_with_market_visible | -0.081479 [-0.115252, -0.046014] | -0.128201 [-0.177392, -0.076066] |

Intervals use 10,000 paired match bootstrap resamples with seed `20261013`; all decisions and repeats remain together. They treat matches as exchangeable and do not account for teams repeated across games. Assessment intervals are conditional on the frozen fitted weight; they do not propagate uncertainty from refitting the earlier cohort. Fitting Brier improves slightly versus market (.467242 vs .476336), but that advantage reverses later. The later market-only Brier is .339342 versus .364533 for the frozen blend. Gains over the original head alone therefore do not establish useful correction of the market.

## Fixed-rule frozen-signal trading

Every forecast uses the identical external executor: 0.25 shares, more than 0.05 forecast edge over displayed all-in cost, maximum all-in entry/fill cost 0.65, one accepted entry held to settlement. Fee/spread and delayed fill evidence are read by the execution simulator after selection; fills and final outcomes never enter the order selector. The predictions retain original observed positions; changed trading positions do not feed back into forecasts. These are frozen-tape counterfactuals, not fresh closed-loop outcomes. Market/no-trade makes no trades under this rule.

Dollar amounts sum 99 game-repeats (33 games × 3), not 99 independent games. Exposure is entry debit; turnover is purchased shares. Portfolio-wide concurrent exposure is not simulated.

| Signal | Net P&L | Gross losses | Avg win | Avg loss | Worst loss | Entry exposure | Max match exposure | Turnover shares | Trading coverage |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| market_only | $0.0000 | $0.0000 | — | — | $0.0000 | $0.0000 | $0.0000 | 0.00 | 0.0% |
| original_model_with_market_visible | $-0.4330 | $-2.8322 | $0.1846 | $-0.0333 | $-0.1042 | $3.6830 | $0.1093 | 24.50 | 99.0% |
| fitted_blend | $-0.6106 | $-1.6127 | $0.2004 | $-0.0237 | $-0.0635 | $1.8606 | $0.0635 | 18.25 | 73.7% |
| No trade | $0 | $0 | — | — | $0 | $0 | $0 | 0 | 0% |

| Assessment paired P&L comparison | Delta per game-repeat | 95% match-bootstrap interval |
|---|---:|---:|
| original_model_with_market_visible_minus_market_only | $-0.004373 | [$-0.028108, $+0.022872] |
| fitted_blend_minus_market_only | $-0.006168 | [$-0.018889, $+0.010908] |
| fitted_blend_minus_original_model_with_market_visible | $-0.001795 | [$-0.021506, $+0.015946] |

The blend reduces entries and exposure while producing slightly more loss than the fixed-rule original signal. P&L differences are uncertain; the intervals include zero. No profitability claim follows from forecast-score improvements over the original model. Fractional shares, midpoint-derived quotes and cost-capped delayed acceptance do not establish real exchange liquidity or fill feasibility.

## Optional fresh matched prompt study and budget

Predeclared design: 33 unchanged assessment games, three repeats, all 495 original observed state/position prompts, two matched arms (990 calls). The control uses the original prompt. Treatment appends only an explicit normalized-market-prior instruction with at most 0.10 absolute adjustment per outcome. Both retain the complete five-field schema, renderer, checkpoint, temperature 1, 256-token cap and identical matched seeds. Original historical positions are held fixed across arms. The completed fresh responses supply a prompt-only frozen-state comparison, followed by identical frozen-signal execution.

The exact-token preflight is 1,720,554 input tokens and at most 253,440 sampled tokens: **$0.487572** at the verified rates of $0.195/M prefill and $0.60/M sample. The conservative reservation with 2× uncertainty headroom is **$0.975144**, below the $5 direction cap. Central checkpoint metadata verification and readiness approval preceded every paid request in this single run. No automatic restart or application retries. Pricing source: [Tinker model pricing](https://tinker-docs.thinkingmachines.ai/tinker/models/models_and_pricing/), verified by orchestration on 2026-10-13.

Spending: reserved maximum $0.975144, completed token estimate $0.396008, paid requests 990. Verified billing is unavailable; token estimates are not an invoice. Existing source inference is prior sunk spending and is not new direction spending.

## Limitations and next discriminating experiment

The closed-loop source and historical development games were previously inspected; prompt/model changes and calibration could have been indirectly influenced by those games. Sparse observation times, possible knowledge of historical matches, market quality changes over time and shared-team dependence limit inference. The selected 40% model blend should stop as a candidate unless a genuinely new predeclared development cohort supplies contrary evidence. The fixed matched prompt study below tests explicit prior prompting while controlling schema, position and execution. Further study requires new uninspected chronological development games, rather than another fit or threshold search on these results. Any later protected evaluation requires separate enrollment and authorization; no generalization is claimed here.

## Holdout and reproduction

`guard.json` confirms an identical before/after ledger head: `2f6051410929bd283e984574ecce2a944e82aa12911a13d24c194f48dc5a995d`. State: `pending_enrollment`, **zero protected matches enrolled**. No protected cohort was predicted, queried for outcomes, enrolled or scored. This is an exclusion/integrity check, not an empirical protected-holdout test.

```sh
PYTHONPATH=. HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 /Users/alexkan/Desktop/frontiers/rin-gym-forecast-only/.venv/bin/python -m experiments.eight_directions.d2_market_prior.study
PYTHONPATH=. HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 /Users/alexkan/Desktop/frontiers/rin-gym-forecast-only/.venv/bin/python -m experiments.eight_directions.d2_market_prior.verify
PYTHONPATH=. HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 /Users/alexkan/Desktop/frontiers/rin-gym-forecast-only/.venv/bin/python -m experiments.eight_directions.d2_market_prior.fresh
PYTHONPATH=. /Users/alexkan/Desktop/frontiers/rin-gym-forecast-only/.venv/bin/python -m experiments.eight_directions.d2_market_prior.report
```

Run from the research worktree. The first two commands passed; the third is tokenizer-only preflight and performs no requests. `--live` is separate and must not be invoked without orchestrator readiness. Per-match scores, raw analysis rows, execution receipts, protocol, spending and verification are included alongside this report.

## Fresh matched market-prior prompt study

The fresh matched study completed 990 of 990 requests, retaining 495 paired forecast points and 33 assessment matches. Its incremental-market classification is **negative**. Invalid outputs are included with the same penalties in both model arms. The market baseline uses precisely the same retained timestamps.

| Fresh forecast | Brier | Log loss | Valid rate |
|---|---:|---:|---:|
| market_only | 0.339342 | 0.589719 | 100.0% |
| original_prompt | 0.445051 | 0.775840 | 100.0% |
| market_prior_prompt | 0.349047 | 0.610781 | 100.0% |

| Paired comparison | Brier delta, 95% interval | Log loss delta, 95% interval |
|---|---:|---:|
| original_prompt_minus_market_only | +0.105710 [+0.048637, +0.156818] | +0.186121 [+0.097399, +0.265103] |
| market_prior_prompt_minus_market_only | +0.009706 [+0.000769, +0.018322] | +0.021062 [+0.004559, +0.036726] |
| market_prior_prompt_minus_original_prompt | -0.096004 [-0.141744, -0.044615] | -0.165059 [-0.233466, -0.088712] |

The small-adjustment limit was an instruction, not an output projection. Among 495 valid treatment forecasts, 45 exceeded 0.10 absolute change in at least one outcome (9.1% of valid treatment forecasts). No prediction was excluded because it violated that instruction. The largest observed absolute adjustment was 0.328989. Thus observed cap adherence, forecast improvement and trading performance are distinct findings.

| Fresh frozen signal | Net P&L | Gross losses | Average win | Average loss | Worst loss | Entry exposure | Max exposure | Turnover | Coverage |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| market_only | $0.000000 | $0.000000 | — | — | $0.000000 | $0.000000 | $0.000000 | 0.00 | 0.0% |
| original_prompt | $-0.432963 | $-2.832237 | $0.184560 | $-0.033320 | $-0.104237 | $3.682963 | $0.109285 | 24.50 | 99.0% |
| market_prior_prompt | $-0.752058 | $-0.752058 | — | $-0.022790 | $-0.056945 | $0.752058 | $0.056945 | 8.25 | 33.3% |
| No trade | $0 | $0 | — | — | $0 | $0 | $0 | 0 | 0% |

Only complete paired decision trajectories contribute to trading: 99 of 99 game-repeat cases. Forecast pairs and trading trajectories have separate completeness rules, each recorded in `fresh_results.json`. The original observed positions are identical across prompts and frozen; they were not generated by these fresh predictions. This is fresh inference but counterfactual frozen-signal P&L, not new closed-loop trading.

| Paired comparison | Mean P&L delta/game-repeat | 95% interval |
|---|---:|---:|
| original_prompt_minus_market_only | $-0.004373 | [$-0.027817, $0.023396] |
| market_prior_prompt_minus_market_only | $-0.007597 | [$-0.011337, $-0.004179] |
| market_prior_prompt_minus_original_prompt | $-0.003223 | [$-0.030481, $0.019710] |

Fresh usage: 990 completed requests, **$0.396008** full-rate token estimate; retained maximum reservations **$0.975144** under the $5 cap. Billing evidence: None. These figures are not a provider invoice. `fresh_guard.json` confirms that the ledger remained unchanged.

The matched intervention tests instructions for using a prior the original prompt already exposed. Any advantage over the original prompt alone must still be compared with market-only. The study changes no fitting weights or trading thresholds after observing these results, and makes no claims about protected generalization.

```sh
PYTHONPATH=. HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 /Users/alexkan/Desktop/frontiers/rin-gym-forecast-only/.venv/bin/python -m experiments.eight_directions.d2_market_prior.fresh_analysis
PYTHONPATH=. /Users/alexkan/Desktop/frontiers/rin-gym-forecast-only/.venv/bin/python -m experiments.eight_directions.d2_market_prior.fresh_report
PYTHONPATH=. HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 /Users/alexkan/Desktop/frontiers/rin-gym-forecast-only/.venv/bin/python -m experiments.eight_directions.d2_market_prior.verify_fresh
```

Both selected variants should stop as candidates for demonstrated market edge on these games. A further discriminating study would freeze the model, prompt and executor and use additional uninspected chronological development games, rather than add repeats or tune thresholds on these 33 games. The protected holdout remains sealed.

The newest incremental paid-service cap is $5 for the entire task. The orchestrator allowed this existing frozen run to finish because combined reservations remained below that cap. This direction performed no further paid run; `budget_context.json` records the shared cap.
