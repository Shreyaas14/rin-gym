# Fresh closed-loop bracket-exit replay

The new policy returned $-24.14 net P&L with a 57.5% win rate, compared with $-20.62 and 57.7% for the preceding verified original-policy replay.

| Policy | Net P&L | Winning / traded cases | Win rate | Average win | Average loss | Worst case |
|---|---:|---:|---:|---:|---:|---:|
| Original, previous fresh replay | $-20.62 | 120/208 | 57.7% | $0.2088 | $-0.5191 | $-1.1930 |
| Fresh combined bracket exits | $-24.14 | 122/212 | 57.5% | $0.1539 | $-0.4768 | $-0.9928 |

Net P&L change: $-3.52. Win-rate change: -0.15 percentage points. The paired 95% game-cluster bootstrap interval for total P&L change is $-13.69 to $5.96; for win-rate change it is -5.84 to +5.51 percentage points.

## Fixed experiment design

Original one-share prompts and entry selection unchanged. At each held-position checkpoint, close when displayed net liquidation minus actual entry cost reaches +0.10 or -0.25 per share. Bracket close overrides any model action including switches; otherwise honor original buys, closes, switches and re-entry. No same-checkpoint re-entry after forced close; later re-entry allowed, resetting basis. Delayed fills, fees and spread unchanged; stop is not guaranteed. Fresh model responses see actual positions. Reuse verified prior one-share baseline with matching games/checkpoint/seeds. No parameter tuning or holdout use.

Both runs use one-share positions, the same trained checkpoint, the same 72 development games × three repeats, matched sampling seeds, temperature 1 and a 256-token output limit. The original baseline was reused, not resampled in this experiment. All 1,077 responses for the new arm are fresh. Model instructions are unchanged; the bracket is enforced externally, with executed positions passed into the next prompt. No extra dialogue memory or entry filters were introduced.

A gain is displayed net selling proceeds after spread and fees minus the actual all-in entry cost. The +10¢ and −25¢ thresholds are checked only at available checkpoints. Orders fill at recorded delayed quotes, so exit outcomes can differ from the trigger price. Threshold exits override switches as well as holds; later re-entry is permitted and resets the entry cost. Normal model exits remain available when neither threshold triggers.

Triggers: {'take_profit': 69, 'stop_loss': 65}. Model actions overridden: 113. Output validity: 100.0%.

## Cost and safeguards

Logged-token cost estimate: $0.4129. Maximum-token preflight estimate: $0.5161; request reservation ceiling: $2.50, below the authorized $5. These are full-rate token estimates, not a provider invoice, and exclude cache discounts. Rates checked against [Tinker pricing](https://tinker-docs.thinkingmachines.ai/tinker/models/models_and_pricing/).

Five new execution tests and existing replay/budget checks passed before launch. The post-run verifier checked every request receipt, prompt hash, actual position, entry-cost state, bracket action, sampling seed, reconstructed P&L and cost, and pinned baseline/source/code hashes. The holdout ledger was checked read-only and remained unchanged; enrollment is still pending. No training, holdout evaluation, commit or push was performed.

## Interpretation limits

This is a fresh closed-loop execution test on previously inspected development matches, not an unseen-game generalization test. Three repeats are clustered within each of 72 games in uncertainty estimates. Policy thresholds were chosen after earlier development analysis; confidence intervals do not correct that selection. Checkpoint monitoring cannot establish minute-by-minute stop performance or prevent reversals after the final checkpoint.

```sh
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python -m unittest experiments.forecast_only.test_bracket_original -v
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python -m experiments.forecast_only.bracket_original
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python -u -m experiments.forecast_only.bracket_original --live
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python -m experiments.forecast_only.verify_bracket_original
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python -m experiments.forecast_only.report_bracket_original
```
