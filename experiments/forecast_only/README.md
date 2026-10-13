# Forecast-only development pilot

This experiment audits the existing halftime labels, then trains only regulation-result probabilities and evaluates them under a fixed execution policy. It does not enroll or evaluate a protected holdout.

## Halftime audit

The goal target is the first scoring side in `(decision time, decision time + 300 wall-clock seconds]`. `ht` is the recorded end of the first half. It does **not** mean the first five playing minutes of the second half. Raw source timelines and the frozen release agree on all 72 halftime and restart boundaries. All 216 saved responses' goal Brier scores reproduce using the `none` label. Breaks range from 900.199 to 7,912.041 seconds; the longest is France–Iraq (`400021492`), which needs separate source-quality review. Its five-minute window still lies entirely before the recorded restart.

The average predicted probability of any goal was 54.86%. This establishes a forecast/target mismatch, not a mislabeled halftime cohort. The audit uses final feeds; it cannot establish what the provider published at each historical instant or rule out corrections.

No new acquisition dataset is required. The pilot builds a separate, versioned view from the checksummed frozen release. A future target of “next five playing minutes” would need a new target definition, recomputed labels, explicit prompts, and a separate experiment; silently relabeling this task would change its meaning.

## Frozen protocol

`artifacts/forecast-only/protocol.json` fixes:

- 16 train and 16 validation games, chosen with a deterministic seed without outcome-based selection; all five replay decision points must be available.
- Premier League training and World Cup group-stage validation, matching the existing project split. Test, knockout and demo games are excluded.
- Qwen3-8B from base, LoRA rank 32, learning rate `1e-5`, four optimizer updates, four matches per batch, four trajectories per match, one epoch.
- Strict JSON regulation home/draw/away probabilities; no action, goal or future-price output. Reward is negative multiclass Brier averaged within each match; invalid outputs receive Brier 2.
- Native Tinker cookbook group-relative RL, importance-sampling loss, training temperature 1, output cap 128. No P&L reward, extra shaping, KL term, early-stopping selection or hyperparameter sweep.
- Base and final checkpoint evaluated once at temperature 0 on identical validation observations.
- One fixed execution rule: if flat, buy one share only when predicted probability minus displayed buy cost exceeds 2 cents; hold until regulation settlement. No shorts, switching, or re-entry. Decisions see only displayed prices; the existing replay applies recorded delayed fills, 1-cent half-spread and 5% `p(1-p)` fee. These are the frozen simulator assumptions, not claims about real executable liquidity.
- Compare base, final, market-probability and flat-policy baselines. Equal match weighting, paired Brier/P&L differences and 4,000 match bootstrap replicates. Shared-team dependence is not modeled.

The protected-cohort ledger is checked read-only, including hash-chain integrity and overlap rejection. Development IDs are separately reserved through the existing PR6 `Ledger.audit_dataset` API before paid use. Enrollment remains pending. Prediction receipts explicitly say **retrospective development**; they are not live prospective evidence.

## Reproduction

Use Python 3.12 and the repository's pinned training extra. The exact input files, protocol, prompt/code hashes, native training logs, checkpoint path and per-decision outputs are kept with the results. A live run refuses to restart if `STARTED.json` exists; inspect billing and checkpoints before manually recovering any interrupted run.

```sh
uv sync --locked --extra train
python -m unittest experiments.forecast_only.test_core -v
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python -m unittest experiments.forecast_only.test_adapter -v
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python -m experiments.forecast_only.pilot
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python -m experiments.forecast_only.pilot --live
```

Offline tokenizer flags require the Qwen3-8B tokenizer already cached. Preflight uses exact rendered prompt token counts, output caps and full, uncached published rates. It requires a 2x estimated-cost reserve below the authorized $5 budget. It is not a provider-enforced dollar limit. No MLflow or other tracking service is required.

This small pilot checks execution and learning direction on historical development data. It cannot establish generalization, remove base-model memorization, or justify trading deployment. The previously inspected validation games remain development data regardless of how the pilot performs.
