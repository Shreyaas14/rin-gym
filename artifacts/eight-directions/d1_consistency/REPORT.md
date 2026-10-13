# Direction 1: Forecast–action consistency

**Classification: inconclusive for P&L improvement, with negative later-period point estimates.** The audit establishes frequent negative terminal expected-value actions under the model’s own stated probabilities. The fixed consistency executor loses money and does not improve on original execution in the predeclared later 33 games. Do not promote it as a profitable policy.

## Hypothesis and design

Hypothesis: original actions often reduce terminal portfolio value under contemporaneous stated probabilities; full-position consistency may improve net P&L. Protocol was saved before computing new results. Schema/sample receipts and prior configurations were inspected first. This is a post-hoc audit of repeatedly inspected development games, followed by one predeclared offline intervention. The two saved tapes cover the same 72 matches, each with three repeats; they are two generations of receipts, not independent game cohorts. A timing-only partition freezes 39 earlier games and 33 later games, with all boundary-date games together. No parameter was fit or selected; the earlier period is diagnostic and the later period is the assessment.

Pinned sources: `inputs/closed-loop-risk/predictions.jsonl`, `inputs/closed-loop-risk/matches.jsonl`, `inputs/earlier-run-risk/tasks.jsonl.gz`, and orchestrator-copied `inputs/earlier-run-risk/original-matches.jsonl`. SHA-256 receipts are in `results.json`; the additional original tape is pinned through `setup.json` and `inputs/MANIFEST.json`. The active quarter-size original run was excluded. Every original trajectory was reproduced step by step and its total checked against independently calculated execution cash plus settlement.

## Exact intervention and audit quantities

For a flat portfolio, value change is `p_new − buy_cost(shown_new)` for a buy and zero for no trade. For a held contract, keep is zero, close is `sell_proceeds(shown_held) − p_held`, and switch is `sell_proceeds(shown_held) − p_held + p_new − buy_cost(shown_new)`. Historic acquisition costs are sunk and cancel. The one-share executor selects the largest complete action value only when it exceeds zero by strictly more than $0.02; otherwise it holds. Ties use existing legal action order. Invalid forecasts hold. This includes exits and switches, changes no size/cost cap, and performs no threshold search.

Selections receive only frozen probabilities, contemporaneous shown quotes, current position and pricing config. The simulator alone then uses recorded delayed fills and scoring evidence. Feed delay 60s, fill delay 60s, quote-age limit 90s, 1¢ half-spread and legacy 5% `p × (1−p)` fee are preserved. A negative chosen full action value is a value-decreasing action under a terminal objective. EV regret is the best legal action value minus chosen value; holds can have regret without lowering value. A delayed execution flip means a nonnegative shown full value becomes negative using recorded delayed quotes. Forecast residual is realized terminal action change minus delayed own-probability value. This action-relative decomposition compares each decision with holding its then-current assets to terminal settlement; summing it across overlapping decisions is **not** a causal attribution of total P&L.

## Evidence

All uncertainty intervals are 95% percentile intervals from 5,000 paired match-cluster bootstrap samples (seed 202610131), after averaging repeated trajectories/decisions within each match. Positive forecast-score differences mean the model scores worse than market.

| Tape, all 72 matches | Valid rate | Value-decreasing actions, match mean [95% CI] | EV regret >2¢ | Brier model − market [95% CI] | Log loss model − market [95% CI] |
|---|---:|---|---:|---|---|
| closed_loop_original | 100.0% | 0.2238 [0.2000, 0.2493] | 80.6% | 0.0550 [0.0097, 0.0982] | 0.1125 [0.0411, 0.1784] |
| earlier_original | 100.0% | 0.2100 [0.1914, 0.2322] | 80.5% | 0.0547 [0.0096, 0.0979] | 0.1107 [0.0379, 0.1772] |

| Tape, all 72 | Negative buys / all buys | Negative closes / closes | Negative switches / switches | Positive bought edge but negative full switch | Delayed EV flips |
|---|---:|---:|---:|---:|---:|
| closed_loop_original | 202/231 | 31/40 | 8/15 | 1 | 4 |
| earlier_original | 192/228 | 21/32 | 13/19 | 2 | 5 |

The fee-adjusted purchased-side edge alone misses some harmful switches. Holds also account for much of the 80% decision regret, because a forecast-based executor frequently prefers selling a favorite and buying a lower-price contract. Model forecast scores are worse than contemporaneous normalized market probabilities on both tapes. Negative terminal-EV trades cannot be explained by delayed quotes alone: only four/five actions flip sign at execution; displayed negative values are already visible at decision time. This establishes three separate diagnostics, not a clean causal P&L decomposition.

| Later 33 games, 99 trajectories per tape | Original net P&L | Consistency net P&L | No-trade net P&L | Paired consistency − original per match-repeat [95% CI] | Consistency − no-trade per match-repeat [95% CI] |
|---|---:|---:|---:|---|---|
| closed_loop_original | $-1.7769 | $-6.4185 | $0 | -0.0469 [-0.2069, 0.1252] | -0.0648 [-0.1537, 0.0442] |
| earlier_original | $1.2009 | $-7.0222 | $0 | -0.0831 [-0.2432, 0.0951] | -0.0709 [-0.1525, 0.0220] |

Both paired intervals span zero and both later point estimates favor original execution. The all-72 aggregates appear better for consistency, but that effect reverses in the later partition. Selecting the executor from the full-period result would hide that reversal.

| Tape and policy, all 72 / 216 trajectories | Net P&L | Gross losses | Average win / loss | Worst loss | Win rate traded | Coverage |
|---|---:|---:|---:|---:|---:|---:|
| closed_loop_original, original | $-20.619 | $-45.677 | $0.209 / $-0.519 | $-1.193 | 57.7% | 96.3% |
| closed_loop_original, consistency | $-8.673 | $-26.350 | $0.411 / $-0.152 | $-0.667 | 19.9% | 100.0% |
| earlier_original, original | $-15.851 | $-45.205 | $0.229 / $-0.545 | $-0.993 | 60.7% | 97.7% |
| earlier_original, consistency | $-9.954 | $-25.523 | $0.389 / $-0.145 | $-0.573 | 18.5% | 100.0% |

Closed-loop original wins 57.7% of traded cases but loses $20.62: its $0.209 mean win is outweighed by $0.519 mean loss. At those observed payoff sizes, break-even would require about 71.3% wins. This arithmetic assumes the conditional win/loss payout mix stays fixed; it is not a forecast probability threshold, and another policy changes that mix. Consistency changes that asymmetry but creates many losing cheap-contract positions. Stated probabilities are not ground truth.

| Later 33, tape and policy | Gross purchase capital | Buy+sell cashflow turnover | Transaction legs | Modeled execution cost | Decision exposure | Mean shown held exposure | Maximum funded capital |
|---|---:|---:|---:|---:|---:|---:|---:|
| closed_loop_original, original | $65.783 | $67.789 | 111 | $1.961 | 71.5% | $0.347 | $1.209 |
| closed_loop_original, consistency | $26.000 | $38.581 | 217 | $3.285 | 96.0% | $0.102 | $0.667 |
| earlier_original, original | $70.329 | $74.858 | 131 | $2.415 | 75.4% | $0.378 | $1.209 |
| earlier_original, consistency | $24.941 | $35.859 | 227 | $3.202 | 94.5% | $0.099 | $0.999 |

Exposure is the fraction of decision intervals ending with a position, not time-weighted or a portfolio-wide concurrent capital simulation. Mean shown held exposure samples the existing held position at decisions. One share is the maximum contract quantity. Funded capital is the largest negative per-match cash balance before settlement; a sequence of losing switches can exceed one purchase cost. Consistency nearly doubles transaction legs and raises fees despite lower dollar turnover; it does not reduce the quantity of the held contract.

## Limitations and next discriminating experiment

The intervention reuses probabilities conditioned on original positions while changing the portfolio. It is a frozen-signal counterfactual, not fresh closed-loop inference. Both tapes reuse the same matches and shared teams, which match bootstrapping does not fully account for. The market is a historical quote baseline, not a verified liquid executable order book. No order-book/depth/partial-fill evidence was added. The audit assumes maximizing expected **terminal** portfolio value; original actions may target short-horizon repricing or auxiliary goal/price rewards, so a negative terminal-EV action is not proof that every rationale is internally contradictory. Probability accuracy and trading consistency are separate questions. The sparse replay has 1,077 decisions per tape, preserving its one shorter match across three repeats without imputation.

Next: predeclare a small fresh paired development cohort with forecast input excluding positions, then compare original execution with this exact one-share full-action executor on those shared forecasts. Also record stated near-term price forecasts to distinguish terminal-value conflicts from a coherent repricing objective. Keep fees/delays and quantities fixed and evaluate paired match P&L and proper forecast scores. Spend only on that discriminating comparison after budget coordination; do not tune the threshold or promote the current counterfactual.

## Budget, seal and reproducibility

External requests: **0**. Reserved cost: **$0**. Token-cost estimate: **$0** of the $5 direction cap. No billing API was queried, so no verified invoice is claimed. Historical checkpoint availability was not queried because this study performs no inference/training; source receipt provenance is hashed.

Guard before and after is identical: `2f6051410929bd283e984574ecce2a944e82aa12911a13d24c194f48dc5a995d`; `pending_enrollment`, zero protected match IDs enrolled. The exclusion guard was read-only. No enrollment, protected outcomes, prediction or scoring was performed. Zero enrollment means this is not an empirical protected-holdout test.

Reproduce from `/Users/alexkan/Desktop/frontiers/rin-gym-research-eight`:

```sh
PYTHONPATH=. HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 /Users/alexkan/Desktop/frontiers/rin-gym-forecast-only/.venv/bin/python experiments/eight_directions/d1_consistency/analyze.py
PYTHONPATH=. HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 /Users/alexkan/Desktop/frontiers/rin-gym-forecast-only/.venv/bin/python -m unittest experiments.eight_directions.d1_consistency.test_analysis
PYTHONPATH=. /Users/alexkan/Desktop/frontiers/rin-gym-forecast-only/.venv/bin/python experiments/eight_directions/d1_consistency/report.py
```

Outputs: immutable `protocol.json`, `results.json`, `audit-receipts.jsonl`, `execution-receipts.json`, `spending.json`, `guard_before.json`, `guard_after.json`, and this report. Verification covers all saved original steps, total cash/settlement identity, input checksums, cohort exclusion, unchanged guard, and three independent algebra/selection tests.
