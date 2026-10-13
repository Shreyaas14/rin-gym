# Fresh closed-loop Tinker replay: conservative vs original

**Result: substantially smaller losses, but still unprofitable.** Total P&L improved from −$20.62 to −$1.12, while traded-game win rate fell from 57.7% to 12.3%. The improvement beyond arithmetic quarter-sizing remains uncertain: that paired confidence interval includes zero. This is a risk-reduction result, not evidence that the forecasts have a profitable edge.

Both arms used fresh inference from the earlier high-win-rate run’s final checkpoint on the same 72 historical development matches, with three repeats per arm. Each successive prompt reflected the arm’s executed position; the conservative arm showed its actual quarter-share quantity. No saved model predictions were reused.

## Results

Totals below sum 216 game-replays per arm, not 216 independent games. Win rate is positive net P&L among game-replays with a trade. All P&L includes the frozen simulator’s fees, spread and delayed fills.

| Policy | Traded game-replays | Win rate | Total net P&L | Gross losses | Average loss | Worst loss |
|---|---:|---:|---:|---:|---:|---:|
| Original, fresh inference | 208 | 57.7% | $-20.62 | $-45.68 | $-0.519 | $-1.193 |
| Original, quarter size (arithmetic) | 208 | 57.7% | $-5.15 | $-11.42 | $-0.130 | $-0.298 |
| Conservative, fresh inference | 212 | 12.3% | $-1.12 | $-6.02 | $-0.032 | $-0.114 |

The quarter-size original row scales the fresh original outcomes by 0.25. It is a sizing control, not a third inference arm; it cannot establish how the original model would respond to a quarter-share prompt.

## Paired uncertainty

Repeated trajectories are averaged within each game, then 72 games are resampled together across policies for 4,000 bootstrap draws. Shared-team dependence is not modeled.
- Conservative minus original: +9.03 cents per game-repeat; 95% interval [+0.05, +17.91] cents.
- Conservative minus original_quarter_size_arithmetic: +1.87 cents per game-repeat; 95% interval [-1.04, +4.77] cents.

## Design and interpretation

The conservative executor uses 0.25 shares, requires more than 5 cents of probability edge over displayed all-in cost, caps all-in cost at 65 cents per share at both selection and delayed fill, and holds a single accepted purchase to settlement. It ignores advisory model actions. The original arm follows the model’s actions with the original one-share execution rules. This comparison changes entry selection, sizing, exit behavior, and policy awareness in the prompt; it is not a sizing-only ablation.

The original prompt is unchanged. The conservative prompt explicitly describes the executor and per-share costs, and reports its actual position quantity. Both retain the original complete forecast/action JSON schema and validity rules. No dialogue history was added, matching the original environment’s fresh-state prompt design. Seeds match by game, repeat and decision; temperature is 1 and completions are capped at 256 tokens. Both arms use the original sparse decision schedule, not minute-by-minute streaming.

The conservative policy’s worst possible loss is 16.25 cents per match under its simulated fill limit. Fractional shares and limit-order fills require market-specific validation before deployment. This is not a portfolio-wide bankroll or simultaneous-exposure simulation. Historical game knowledge, repeated development-set inspection and post-hoc policy design remain limitations. Do not treat these results as a protected generalization test.

## Cost, holdout and verification

Logged requests: 2,154. Logged input tokens: 3,710,586; output tokens: 215,026. Full-rate token-cost estimate: **$0.853**, below the authorized $5. The maximum-token preflight estimate was $1.058; the request reservation ceiling was $2.50. These are token-based estimates, not a provider invoice; cached-prefill discounts are excluded. [Pricing source](https://tinker-docs.thinkingmachines.ai/tinker/models/models_and_pricing/).

Output validity: original 100.0%; conservative 100.0%.

The protected holdout remains pending enrollment. No protected predictions, resolutions or scores were created. The ledger head matched before and after the live run.

Eight offline tests passed before the run. The post-run verifier checked 2,154 unique request/response pairs and 432 complete game-replays: prompt hashes, executed position/quantity feedback, reconstructed P&L, risk limits, code/input hashes and unchanged ledger.

## Artifacts

- `protocol.json`: checkpoint, cohort, policy, sampling seeds, source hashes and budget.
- `requests.jsonl` and `predictions.jsonl`: pre-request receipts and fresh completions with execution decisions.
- `matches.jsonl`: per-arm, per-repeat P&L, costs and replay steps.
- `summary.json`: primary metrics, paired uncertainty and usage.
- `verification.json`: post-run invariant checks.

Checkpoint: `tinker://d2426bc1-ec71-559d-bb81-e17da9372e58:train:0/sampler_weights/final`.

```sh
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python -m unittest experiments.forecast_only.test_closed_loop experiments.forecast_only.test_risk_policy -v
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python -m experiments.forecast_only.closed_loop
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python -u -m experiments.forecast_only.closed_loop --live
.venv/bin/python -m experiments.forecast_only.verify_closed_loop
.venv/bin/python -m experiments.forecast_only.report_closed_loop
```

The live runner refuses to overwrite an existing protocol. Do not delete that guard to restart an interrupted paid run without reconciling completed requests. No retraining, commit or push was performed.
