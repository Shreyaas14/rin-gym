# Halftime audit and forecasting-only pilot

The halftime labels are consistent with the existing target definition. The live Tinker pilot completed, but it did not demonstrate a meaningful forecasting improvement. The protected holdout remains **pending enrollment**, with no protected-cohort predictions or scoring.

## Halftime finding

Across 72 historical validation matches and 216 saved responses, the five-minute wall-clock horizon ended before the recorded second-half restart in every case. Recomputed labels are all `none`; the saved goal Brier scores reproduce exactly with those labels. Raw feed halftime/restart timestamps match the frozen release for all 72 games.

The model assigned an average 54.86% probability to a goal in those windows. That is a forecast/target mismatch. The task means five wall-clock minutes immediately after first-half end, not the first five playing minutes after restart.

No new source-data collection is needed, and no existing labels were changed. A separate frozen subset of existing tasks was constructed for this experiment. Changing the goal horizon to playing time would require a new target version and regenerated labels.

One source caveat remains: France–Iraq (`400021492`) has a recorded halftime break of 7,912 seconds, versus a minimum of 900 seconds across the cohort. Investigate whether that reflects a real interruption or timestamp quality before using elapsed halftime duration as a feature. This retrospective audit cannot attest historical feed availability or exclude later corrections.

## Completed live experiment

- Model: Qwen3-8B, fresh LoRA rank 32; learning rate `1e-5`.
- Four native Tinker cookbook RL optimizer updates, 16 training matches, four trajectories per match, 320 training forecasts.
- Reward: negative regulation-result Brier, averaged within each match. No P&L, goal or future-price reward.
- Frozen validation: 16 distinct later matches, 80 base and 80 final forecasts. No checkpoint selection or parameter sweep.
- Fixed execution: one share, buy only if probability minus displayed fee/spread-adjusted buy cost exceeds 2¢; hold to settlement. Replay uses delayed recorded fills. Exactly the same rule applies to base, final and market probabilities.

| Metric | Base model | Final checkpoint | Market probabilities |
|---|---:|---:|---:|
| Brier, lower is better | 0.9791 | 0.9518 | 0.4010 |
| Valid probability objects | 59/80 | 61/80 | 80/80 |
| Mean net P&L per match | −2.24¢ | −2.24¢ | 0¢ |
| Trades across 16 matches | 15 | 15 | 0 |

Invalid forecasts receive Brier 2 in the primary metric; they are not silently removed or normalized. All 21 invalid base outputs were valid JSON whose probabilities failed the sum-to-one check. Market probabilities did not trigger a purchase under this fixed rule; their zero P&L is a no-trade result.

The paired final-minus-base Brier change is **−0.0273**, with a 95% match-bootstrap interval of **[−0.0796, +0.0134]**. This is not convincing evidence of improvement. Base and final made identical trades. Final-minus-market Brier is +0.5508, with interval [+0.3402, +0.7589].

A post-hoc diagnostic on the 59 decisions valid under both models gives Brier 0.6158 before and 0.6248 after. This conditional subset is not the primary evaluation, but it reinforces that the small headline improvement should not be described as better probability forecasting: output validity improved slightly while this common-valid subset worsened.

The pilot establishes that the training/evaluation integration runs. It does not justify promoting this checkpoint. A next separately frozen experiment should address probability validity explicitly, then test forecast quality against the market baseline. Keep invalid-rate changes separate from changes in Brier on valid outputs, and do not tune against a protected readout.

## Cost and protection

Logged usage: 225,264 training input tokens, 11,746 training output tokens, 114,950 evaluation input tokens and 6,500 evaluation output tokens. The full-rate token-cost estimate is **$0.182**, versus a preflight maximum-token estimate of $0.220 and the authorized $5 limit. This is not a provider invoice; caching discounts are excluded. Rates were checked against [Tinker's published pricing](https://tinker-docs.thinkingmachines.ai/tinker/models/models_and_pricing/).

All 32 development match IDs were reserved using the existing exclusion-ledger API before paid use. Every training decision predates every validation decision. Test, demo and knockout games were excluded. Ledger integrity and exclusion checks passed before and after the run; the ledger head was unchanged during live training/evaluation. The holdout remains unenrolled, with no results inspected.

Prediction receipts are explicitly marked retrospective development; they are not prospective claims. Historical model memorization and shared-team dependence remain limitations. Sixteen matches are an integration pilot, not a robust generalization study.

## Artifacts and validation

- `protocol.json`: frozen cohorts, experiment settings, source hashes and ledger head.
- `halftime-audit.json`: per-match timestamps, raw-source hashes and label comparisons.
- `predictions.jsonl` / `requests.jsonl`: 160 evaluation predictions and pre-request observation hashes.
- `evaluation-matches.json` / `summary.json`: per-match replays, aggregate metrics and paired intervals.
- `training/`: native optimizer metrics, rollout logs and checkpoint records.
- `checkpoint.json`: final sampler, `tinker://f650882f-d0c5-5c81-a9ee-3243f51662f2:train:0/sampler_weights/final`.
- `usage.json`, `split-checks.json`, `dependency-provenance.json`: cost, chronology and dependency evidence.
- Experiment implementation and run instructions: `experiments/forecast_only/` at the worktree root.

Validation: 165 existing replay/forecast tests, five new core tests, and one real-cookbook/fake-service integration test passed (171 total). Four live optimizer updates and both complete 80-prediction evaluations finished successfully. Source hashes were rechecked after completion.

Commands executed from the experiment worktree included:

```sh
.venv/bin/python -m unittest experiments.forecast_only.test_core -v
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python -m unittest experiments.forecast_only.test_adapter -v
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python -m unittest discover -s tests/trade_env -q
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python -m experiments.forecast_only.pilot
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python -u -m experiments.forecast_only.pilot --live
```

Code and results are preserved in the dedicated `experiment/forecast-only` worktree. This PR publishes the negative result and supporting evidence; it does not promote the checkpoint.

## Publication notes

The PR includes compact native metrics and rollout summaries, predictions, frozen tasks and cohort manifests. Verbose HTML/logtree exports, console logs and downloadable source archives remain local. `SHA256SUMS.json` records the original full local run; `PUBLISHED_SHA256SUMS.json` covers the files actually published in this PR.

The original protocol preserves historical machine-local paths and code hashes as provenance. The offline fake-service adapter test was subsequently made independent of the local ledger; training, parsing, reward and execution code are unchanged. A new live run requires a separately prepared output directory and an initialized local exclusion ledger, including development-ID reservation through the governance API. Do not edit the published protocol or bypass the existing-run guard to start another run.
