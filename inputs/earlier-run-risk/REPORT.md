# Earlier high-win-rate run: conservative execution replay

The conservative policy reduced simulated losses but did not make the strategy profitable or preserve its high win rate. No paid API calls were made ($0 of the $5 cap). The holdout ledger was unchanged and remains pending enrollment.

## Comparison

72 historical development matches, three recorded repeats each (216 game-replays). Totals sum the repeats; they are not results from 216 independent games. Win rate counts positive-net-P&L game-replays among those with a trade, not individual positions. All figures include the original simulated fees and spread.

| Policy | Traded game-replays | Win rate | Total P&L | Gross losses | Average losing game-replay | Worst game-replay |
|---|---:|---:|---:|---:|---:|---:|
| Original recorded agent | 211 | 60.7% | $-15.85 | $-45.21 | $-0.545 | $-0.993 |
| Original agent, quarter size | 211 | 60.7% | $-3.96 | $-11.30 | $-0.136 | $-0.248 |
| Frozen forecast rule, quarter size | 216 | 13.0% | $-1.65 | $-6.85 | $-0.036 | $-0.129 |
| Conservative policy | 215 | 14.0% | $-0.27 | $-5.93 | $-0.032 | $-0.104 |

## Interpretation

The original agent won 128 of 211 traded game-replays. Average wins were $0.229, versus average losses of $0.545. Gross wins of $29.35 were outweighed by $45.21 of losses, confirming the asymmetry the user identified.

Quarter-sizing the original strategy retains exactly the same decisions and win rate; both wins and losses shrink by 75%. It remains a losing strategy on this sample.

The tested conservative policy uses 0.25 shares, more than 5 cents of forecast edge after costs, a 65-cent all-in entry-cost cap enforced at the delayed fill, and one buy held to settlement. It chooses trades from saved probability forecasts instead of following the original model’s buy/switch/close recommendations. Thus it changes trade selection and exit behavior as well as risk. It won 30 of 215 traded game-replays, with average wins of $0.189 and average losses of $0.032. The lower win rate is a substantive change, not a reporting error.

The quarter-sized frozen-forecast rule (2-cent edge, no price cap) isolates more of the selection-rule comparison. Tightening its entries reduced total losses from $1.65 to $0.27. This remains exploratory development tuning, not independent evidence of a profitable edge.

The conservative-minus-original mean P&L difference is +7.21 cents per game-replay, with a 95% match-cluster bootstrap interval of [−2.22, +16.56] cents. Against the quarter-sized original agent, it is +1.71 cents with interval [−1.37, +4.98]. Neither establishes reliable superiority across new games. Repeats are averaged within game before bootstrapping 72 games; shared-team dependence remains unmodeled.

## Scope and verification

- Reconstructed original tasks from the checksummed frozen release and the original run configuration. Every recorded decision P&L (1,077 total) and all 216 match totals reproduced to numerical tolerance.
- Used only the 72 historical validation matches; no protected games were enrolled, forecast or scored. Before/after ledger integrity and overlap checks matched.
- No retraining or new model inference. Saved probabilities are the frozen input signal.
- Saved probabilities were conditioned on the original positions. A different policy changes those positions; these results are a frozen-signal counterfactual, not fresh closed-loop responses to the altered portfolio.
- Fractional-share execution, delayed midpoint-based fills and limit-order acceptance are simulator assumptions, not proof of live order-book fills.
- This comparison still uses the original sparse decision schedule; it does not test minute-by-minute streaming.

The lower-risk change that demonstrably preserves the original agent’s win rate is quarter-sizing. The full conservative rule materially reduces loss size in this replay but should not be described as the same high-win-rate strategy with safer sizing.

## Reproduction

```sh
.venv/bin/python -m experiments.forecast_only.earlier_run_risk
.venv/bin/python -m unittest experiments.forecast_only.test_risk_policy -v
```

`comparison.json` contains aggregate metrics, policies, source hashes and paired intervals. `matches.json` contains per-repeat outcomes and counterfactual orders. `tasks.jsonl.gz` contains the reconstructed development replay tasks. Source paths and ledger paths in the script refer to this local workspace. No commit, push or new Tinker run was made.
