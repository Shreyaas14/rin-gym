# D8: narrow observable-regime specialization

**Classification: negative.** No regime qualifies under the frozen earlier-cohort selection rule; no specialized policy is selected or promoted. No inference, training or paid-service requests were made ($0 incremental spend).

## Hypothesis and registered intervention

A small observable soccer regime may give the existing original forecasting policy lower proper-score loss than its contemporaneous market baseline, reproducibly across a chronological development partition.

This is a post-hoc development screen of four predeclared observable slices on the original recorded checkpoint and forecast tape. It changes which observations are scored, while keeping probabilities, positions, sizing, prices and execution untouched. The input schema and known earlier risk report were inspected before registration; the D8 aggregate was not. The `protocol.json` file was written before aggregate analysis.

The slices are tied score at `h2+30`, an observed positive red-card count in the anchored prompt prefix, halftime (`ht`), and maximum absolute shown-price movement of at least $0.15 since the previous available sparse observation of the same trajectory. The first observation cannot satisfy the movement slice. Membership sees only observation timestamps, prefix text, observed score and shown prices. Scoring labels and future fills are used only after membership is determined.

Selection requires at least five earlier matches, positive market-minus-model Brier improvement and positive log-loss improvement. Among qualifiers, greatest Brier improvement wins, with a fixed lexical tie break. `selection.json` records the frozen selection before later-cohort aggregation. No candidate qualified; all later slice scores are descriptive and cannot be used to reselect. The stopping rule prohibits threshold tuning and retrying until a positive finding.

## Frozen data and uncertainty

Known original `forecast-rules-run-3` repeated-validation snapshots contain 72 development matches, three recorded repeats each, and 1,077 observations. The frozen chronological boundary is 2026-06-21 UTC: 39 earlier matches (582 repeated observations) for selection and 33 later matches (495 observations) for assessment. Whole matches and their repeats remain together. Exact IDs, source paths and SHA256 checksums are in `protocol.json`; inputs are copied snapshots under `inputs/earlier-run-risk/`. No data release or active experiment was loaded.

Checkpoint provenance is `tinker://d2426bc1-ec71-559d-bb81-e17da9372e58:train:0/sampler_weights/final` (Qwen/Qwen3-8B, LoRA rank 32, training batch 48). `checkpoint_provenance.json` copies the orchestrator's existing free metadata verification; D8 did not contact the service or load weights. The frozen recorded forecast tape supplies every forecast.

The market baseline is normalized shown YES quotes, not an executable bid/ask. Probabilities use the original finite-vector validity tolerance of 0.05 before normalization. Invalid forecasts would receive Brier 2 and log loss −log(10⁻⁶); all 1,077 recorded observations are valid, so valid-only and invalid-inclusive results agree. The red-card prefix parser supported all 1,077 observations. Original information delays remain `event_delay_s=60`, `fill_delay_s=60`, and `price_max_age_s=90`.

Each score averages observations and repeats within a match, then averages matches equally. Paired market-minus-model differences use 4,000 whole-match percentile bootstrap resamples, seed 20261013; positive improvement favors the model. Bootstrap samples preserve all repetitions. A one-match slice has no estimable between-match interval. Shared-team dependence is not modeled. Four-slice selection, overlapping regimes, and repeated prior development use make the intervals descriptive; there is no multiplicity correction or independent generalization claim.

## Proper-score results

| Cohort / slice | Matches | Observations | Model Brier | Market Brier | Brier improvement [95% CI] | Log-loss improvement [95% CI] |
|---|---:|---:|---:|---:|---|---|
| fit / overall unchanged forecasts | 39 | 582 | 0.4842 | 0.4763 | -0.0079 [-0.0715, 0.0607] | -0.0429 [-0.1406, 0.0644] |
| fit / tied_late | 13 | 39 | 0.6628 | 0.6378 | -0.0250 [-0.1285, 0.0872] | -0.0811 [-0.2231, 0.0861] |
| fit / observed_red_card | 4 | 27 | 0.2665 | 0.2140 | -0.0524 [-0.1280, -0.0013] | -0.0918 [-0.1734, -0.0215] |
| fit / halftime | 39 | 117 | 0.4843 | 0.4966 | 0.0124 [-0.0584, 0.0907] | -0.0200 [-0.1246, 0.0948] |
| fit / large_observed_price_move | 25 | 114 | 0.4731 | 0.4214 | -0.0517 [-0.1053, 0.0040] | -0.1132 [-0.1905, -0.0287] |
| assessment / overall unchanged forecasts | 33 | 495 | 0.4494 | 0.3393 | -0.1100 [-0.1580, -0.0569] | -0.1910 [-0.2660, -0.1058] |
| assessment / tied_late | 14 | 42 | 0.6009 | 0.4879 | -0.1130 [-0.2230, 0.0046] | -0.1817 [-0.3407, 0.0066] |
| assessment / observed_red_card | 1 | 15 | 0.1186 | 0.0016 | -0.1170 not estimable | -0.2610 not estimable |
| assessment / halftime | 33 | 99 | 0.4623 | 0.3422 | -0.1201 [-0.1741, -0.0618] | -0.2063 [-0.2913, -0.1149] |
| assessment / large_observed_price_move | 17 | 78 | 0.4034 | 0.2659 | -0.1375 [-0.1900, -0.0789] | -0.2410 [-0.3179, -0.1479] |

Only halftime has positive earlier Brier point improvement (+0.0124), but its log loss worsens (−0.0200) and both intervals cross zero. It therefore fails selection. Tied-late and large-movement slices have worse earlier point scores. The observed-red-card slice has only four earlier matches and one later match, failing minimum coverage. On later matches, all four slices have worse Brier and log-loss point scores; tied-late uncertainty still crosses zero. This is a negative screen, with uncertainty about exact effects and sparse red-card coverage.

## Unchanged original trading diagnostic

This diagnostic reproduces original recorded P&L; it does not execute a specialized trading policy and is not a maker/taker comparison. Totals sum three repeats, which are not independent matches. Costs remain the original fixed fee coefficient and spread allowance. “Gross P&L” adds back their combined recorded costs; fee and executable spread/slippage are not separated in this diagnostic.

| Cohort | Traded replays / eligible | Profitable / all | Profitable / traded | Net P&L | Gross before combined costs | Combined costs | Avg win | Avg loss | Worst loss | Turnover | Exposure |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| fit | 115/117 | 53.0% | 53.9% | $-17.0518 | $-13.9390 | $3.1128 | $0.2252 | $-0.5852 | $-0.9928 | 167 | 80.4% |
| assessment | 96/99 | 66.7% | 68.8% | $1.2009 | $3.6155 | $2.4146 | $0.2332 | $-0.4730 | $-0.8325 | 131 | 75.4% |
| all | 211/216 | 59.3% | 60.7% | $-15.8510 | $-10.3235 | $5.5275 | $0.2293 | $-0.5446 | $-0.9928 | 298 | 78.1% |

Overall mean net P&L per game-replay is $-0.0734, with match-bootstrap interval [-0.1590, 0.0091]. Net losing replays total $-45.2050. A no-trade baseline is $0. The later cohort's small positive total is inconclusive and cannot establish specialization: original trading decisions were unchanged, and no regime qualified.

Turnover counts recorded contract transactions. Exposure is the fraction of decision chunks ending with a position, not time- or capital-weighted exposure. Profitable-match rates above are across match-repeat game-replays, never position win rate; individual position win rate and post-fill adverse movement are unavailable in this diagnostic. Original forecasts condition on the recorded original portfolio. No altered-position counterfactual, approximate order-book replay, or fresh closed-loop evaluation was performed.

## Limitations and next discriminating experiment

All outcomes belong to repeatedly inspected historical development data. Probabilities are estimates, not truth. Sparse observed price movement is not a high-frequency volatility measure. Four handpicked regimes are not an exhaustive search and do not establish that no useful specialization exists. Score comparisons do not prove profitable trades or credible maker fills. Red-card scarcity prevents meaningful regime validation.

The next discriminating experiment should prospectively register one regime and a sample-size target on newly collected development matches, before outcomes arrive, using the same checkpoint and matched observation inputs against the market. Collect observed red-card states and timestamped executable order books/trades if execution is later assessed. Do not recycle this assessment cohort to choose a revised threshold or regime. Any changed-position trading policy then needs separate bounded closed-loop inference and maker/taker-aware execution validation; no paid request is proposed here.

## Holdout and spend

Read-only integrity/overlap checks on the actual 72 cohort IDs ran before and after analysis. Both returned ledger head `2f6051410929bd283e984574ecce2a944e82aa12911a13d24c194f48dc5a995d`, state `pending_enrollment`, protected count 0. The ledger was not changed; no protected outcomes were read, forecast, scored or empirically tested. Pending enrollment with zero protected IDs is not evidence from a holdout. No active experiment was interrupted or modified.

The task ledger records zero paid requests, zero reservation and $0 incremental service spend. Billing was not queried; historical runs are sunk costs and are excluded. The latest user request caps execution work at $5 total, superseding an obsolete per-direction budget; D8 has no paid authorization. MLflow is not required.

## Reproduce

Run from `/Users/alexkan/Desktop/frontiers/rin-gym-research-eight`:

```sh
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH=. /Users/alexkan/Desktop/frontiers/rin-gym-forecast-only/.venv/bin/python -m experiments.eight_directions.d8_specialization.analyze
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH=. /Users/alexkan/Desktop/frontiers/rin-gym-forecast-only/.venv/bin/python -m unittest experiments.eight_directions.d8_specialization.test_analysis -v
```

The full verification receipt can be regenerated with:

```sh
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH=. /Users/alexkan/Desktop/frontiers/rin-gym-forecast-only/.venv/bin/python -m experiments.eight_directions.d8_specialization.verify
```

The analysis verifies input checksums and cohort identity, persists selection before later aggregation, produces raw observation receipts and JSON results, and checks the holdout seal. Focused tests cover observable membership, unsupported red-card parsing, first-quote exclusion, original probability validity, equal match weighting and singleton uncertainty. `verification.json` records test and deterministic rerun evidence.
