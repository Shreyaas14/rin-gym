# Fresh original policy with quarter-sized positions

**Negative result: neither the similar-win-rate goal nor positive P&L was achieved.** The fresh arm underperformed the arithmetic quarter-size control on this sample; smaller position disclosure did not preserve the model’s decisions. No further variants were tried to chase a positive result.

Fresh quarter-share replay produced **$-7.34 total P&L** and a **47.7% win rate** among traded game-replays. The original one-share baseline was $-20.62 and 57.7%. No variant or checkpoint was selected after seeing these results.

## Comparison

72 historical development games, three repeats each. Totals sum 216 game-replays, not 216 independent games. Win rate means positive net P&L among game-replays with at least one trade; it is not a per-position hit rate. Fees, spread and delayed fills are included.

| Arm | Traded game-replays | Win rate | Total P&L | Average winning game | Average losing game | Worst game |
|---|---:|---:|---:|---:|---:|---:|
| Original one share, previous fresh run | 208 | 57.7% | $-20.62 | $0.209 | $-0.519 | $-1.193 |
| Original outcomes × 0.25, arithmetic control | 208 | 57.7% | $-5.15 | $0.052 | $-0.130 | $-0.298 |
| Original quarter share, fresh inference | 216 | 47.7% | $-7.34 | $0.046 | $-0.107 | $-0.329 |

## Interpretation and uncertainty

Scaling an unchanged sequence of trades by 0.25 cannot change the sign of P&L or its win rate. The fresh arm can differ from the arithmetic control because the model sees a quarter-share position and generates new decisions. The entry/exit executor itself is unchanged.

The fresh arm changed 519 of 1077 executed actions relative to the matched baseline decisions. This includes downstream changes after positions diverge; it is not a count of independent policy improvements.

Fresh quarter-share mean P&L: -3.40 cents per game-repeat; 95% match-cluster bootstrap interval [-5.30, -1.49] cents.

Win-rate difference versus the original: -10.01 percentage points; 95% clustered interval [-18.63, -1.43] points.

Fresh quarter minus original_one_share_previous_fresh: +6.15 cents per game-repeat; 95% interval [-1.21, +13.43] cents.

Fresh quarter minus original_quarter_arithmetic: -1.01 cents per game-repeat; 95% interval [-2.67, +0.55] cents.

Bootstrap resamples 72 games with all their repeats paired across arms (4,000 draws). Shared-team dependence is not modeled. These frequently inspected historical games remain development data; they cannot establish protected out-of-sample profitability. There was no budget-driven search for a profitable variant.

## What stayed fixed

- Same final checkpoint from the earlier high-win-rate run, same 72 games, same three repeats and sampling seeds as the verified 57.7% baseline.
- Same original legal actions and model-selected buys, closes, switches, keeps, no-ops and re-entry. No conservative edge threshold, price cap or mandatory hold rule.
- Same forecast schema, parsing, fee/spread model, recorded delayed fills and sparse decision times. No minute-by-minute change.
- Actual positions carry forward after each freshly generated decision. Prompts show 0.25 shares and specify that quotes/costs are per share. Original sample dialogue memory behavior is unchanged.
- Net P&L and costs are quarter-scaled from the original per-share execution engine. Individual sizing is smaller, but multiple losing entries can still lose more than $0.25 in one game; there is no new per-game or portfolio-wide loss cap.

The original one-share arm was reused from the immediately preceding verified fresh comparison. Only the quarter-share arm was newly sampled for this request. The comparison is paired by game/repeat/decision, but identical seeds do not guarantee identical generation across different prompts or service executions. Fractional execution is a simulator assumption, not a guarantee of venue fillability.

## Cost and verification

1,077 fresh requests; 1,872,068 input tokens and 113,397 output tokens. Full-rate token-cost estimate: **$0.433**, below $5. Preflight maximum-token estimate: $0.532; request-reservation ceiling: $2.50. Rates: [Tinker pricing](https://tinker-docs.thinkingmachines.ai/tinker/models/models_and_pricing/). The estimate is not a provider invoice and excludes cached-input discounts.

Fresh output validity: 100.0%.

Three targeted offline tests passed: close/switch/re-entry remain executable; expensive purchases are not blocked by the removed conservative filters; legal actions and the future-data boundary remain unchanged. Post-run verification checked every prompt hash, position and quantity, original-engine execution, quarter-scaled P&L/cost, baseline seed, and input/code hash.

**Holdout untouched:** the exclusion ledger was unchanged before/after, and remains pending enrollment. No holdout enrollment, inference, labels or scoring occurred.

## Artifacts

`protocol.json` freezes this experiment and pins the previous baseline hashes. `requests.jsonl` and `predictions.jsonl` record new inference. `matches.jsonl`, `summary.json` and `verification.json` preserve results and checks.

Checkpoint: `tinker://d2426bc1-ec71-559d-bb81-e17da9372e58:train:0/sampler_weights/final`.

```sh
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python -m unittest experiments.forecast_only.test_quarter_original -v
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python -m experiments.forecast_only.quarter_original
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python -u -m experiments.forecast_only.quarter_original --live
.venv/bin/python -m experiments.forecast_only.verify_quarter_original
.venv/bin/python -m experiments.forecast_only.report_quarter_original
```

Existing protocol files prevent automatically restarting a paid run. No training, commit or push was performed.
