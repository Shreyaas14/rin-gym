# Exploratory conservative execution comparison

A configurable conservative policy is implemented in `experiments/forecast_only/risk_policy.py`. It uses saved final-checkpoint forecasts from the 16-match development pilot; it makes no Tinker requests and leaves the original experiment and protected holdout unchanged.

Defaults: 0.25 shares per position rather than one; predicted probability must exceed fee/spread-adjusted displayed cost by more than 5 cents rather than 2 cents; maximum all-in entry cost of 65 cents per share, enforced again at the delayed simulated fill. One entry per match, held to settlement, with no averaging down. Under these simulator assumptions, maximum loss is 16.25 cents per match. Fractional shares and limit-order fill approximations require validation before deployment; this is not a portfolio-wide exposure cap.

| Policy | Trades | Total P&L, 16 matches | Worst match P&L |
|---|---:|---:|---:|
| Original | 15 | −$0.3588 | −$0.2847 |
| Quarter size only | 15 | −$0.0897 | −$0.0712 |
| Stricter entries only, original size | 14 | −$0.0946 | −$0.2847 |
| Conservative combined | 14 | −$0.0236 | −$0.0712 |

This is post-hoc development exploration, not an independently validated improvement. Smaller sizing mechanically reduces both profits and losses by 75%; it cannot turn negative expected returns positive. The stricter edge threshold skipped one losing trade on this sample. The 65-cent cap did not bind because all baseline filled costs were below 31 cents. The combined policy still lost money.

The original pilot won only 2 of its 15 traded matches. These numbers are not a re-evaluation of the earlier high-win-rate, expensive-entry strategy. That strategy needs its own execution replay before claiming these controls fix its particular loss asymmetry. A high win rate alone does not establish an edge when losing stakes exceed winning payoffs.

Four unit tests passed, covering expensive-entry rejection, cancellation when delayed cost exceeds the cap, bounded loss and stake scaling for wins and losses, and abstention on invalid forecasts. The comparison additionally verifies that its original-policy P&L exactly reproduces every match in the frozen pilot and that the holdout ledger is unchanged.

```sh
.venv/bin/python -m unittest experiments.forecast_only.test_risk_policy -v
.venv/bin/python -m experiments.forecast_only.risk_policy
```

No retraining, minute-by-minute replay, deployment, commit or push was performed for this comparison. The thresholds are configurable exploratory defaults, not a selected optimum.
