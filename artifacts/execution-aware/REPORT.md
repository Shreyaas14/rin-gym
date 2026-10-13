# Execution awareness: offline development report

Classification: **inconclusive** for real maker/hybrid returns. **Negative feasibility** under recorded minimum value 5 with fixed one-share sizing. Synthetic results are sensitivity envelopes, not verified maker returns and not mathematically guaranteed P&L bounds.

The exact legacy baseline lost $20.6192 over 216 match-repeats. Under the explicitly assumed recorded coefficient 0.03 and central fractional scenario, controlled taker net P&L was $-3.3547, maker $-0.5669, and hybrid $-0.5669. Maker's smaller loss coincided with reduced fills; hybrid also lost money. Every corresponding paired match-cluster interval includes zero. These negative and inconclusive findings do not establish an execution advantage. The recorded-constraint feasibility arms all have zero fills.
## Frozen design and accounting

72 previously inspected development matches, three repeats, original final checkpoint `tinker://d2426bc1-ec71-559d-bb81-e17da9372e58:train:0/sampler_weights/final`. Forecasts, original action tape and nominal one-share sizing stay frozen. A common taker all-in cost/uncertainty gate filters source intents; therefore controlled arms differ from the ungated exact legacy baseline. Partial/unfilled inventory is carried; a switch cannot buy the new outcome while residual old inventory remains. Later assessment uses the predeclared 33 later matches; no tuning or selection occurred.

Legacy replay uses unchanged `Replay`, precomputed original action rewards and costs; maximum P&L error 0.0. The separate cash ledger reproduces the same P&L. Legacy fees and spread use the original fixed coefficient and allowance. Controlled execution uses the shared execution engine, per-fill liquidity roles and executable synthetic quote prices; it never debits spread a second time. Net = gross - fees + rebates. Midpoint P&L = gross + spread/slippage. Settlement carries no exit fee.

## Fee and data provenance

Official [fees](https://docs.polymarket.com/trading/fees) verified on 2026-10-13 give current sports taker coefficient 0.05, maker coefficient zero and five-decimal rounding. Current [maker rebates](https://docs.polymarket.com/programs/maker-rebates) depend on relative daily pool allocation; all rebates here are zero. [Order documentation](https://docs.polymarket.com/trading/place-orders) supports rejecting post-only crossings; this simulator uses local GTC cancellation rather than claiming an exchange GTD timestamp.

The 216 exact 1X2 market metadata rows record coefficient **0.03**, raw `orderMinSize` **5**, tick 0.001 for 215 contracts and 0.01 for one. Current documentation and metadata disagree; neither supplies a validated June effective interval. Historical fees are **unknown**. Three separate explicit assumptions are reported: original fixed 0.05, current sports 0.05 with current rounding, and recorded market 0.03 with rounding. Gamma [market details](https://docs.polymarket.com/market-data/market-details) describe `orderMinSize` as minimum USD notional, while book order docs describe `min_order_size` as shares. The units are uncertain; a one-share order valued at most $1 fails either interpretation of 5. The feasibility arm deliberately uses minimum USD notional. None is historical verification. `data-audit.json` records the filtered metadata and schemas. No release outcomes/events were loaded.

Only historical price samples and cached decision/fill/end mids exist; no observed order books, depths, aggressor trades or queue state. Fractional scenarios use invented two-sided quotes/depth, min-size zero, tick 0.0001 and invented opposing flow with declared queue/fill fractions (0%,25%,75%). Passive price touched is never treated as evidence of fill. A limit may fill as taker if it crosses; post-only rejects. Hybrid cancels first and rechecks remaining edge before crossing. All simulated fills are labeled synthetic sensitivity. Delay remains 60 seconds; expiry 120 seconds, cancellation latency zero; the original fixed signal validity is 180 seconds. Hybrid fallback submitted after cancellation at +120 arrives at +180, exactly at the deadline, and cannot fill. Earlier fallback after a post-only rejection may still fill before the deadline. This strict deadline was corrected after independent review without extending validity or selecting a new scenario. Quotes/depth are hypothetical around timestamped historical price samples filtered to these 216 YES contracts; each policy observation is the last sample available at its actual timestamp with maximum age 90 seconds. No future fill price is fed into order selection, and missing/stale observations cause misses. These fill, spread, depth and queue assumptions remain unvalidated.

## Results

Totals below sum 216 match-repeats. Profitability rates first average the three repeats per match. Position means/wins use match-repeat-outcome inventory accounts with any buy fill (aggregating lots). Exposure is dollar inventory sampled at available decisions, not time-weighted exposure. Turnover is executed share-price notional, excluding settlement. Fill rate divides filled by policy-requested quantity after common gate/inventory guard; it excludes rejected source intents, whose statuses remain in order logs.

| Arm | Net | Gross | Fees | Spread/slippage | Qty fill rate | All/traded profitable matches | Position win rate | Worst position | Turnover | Peak exposure |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|
| legacy_exact | -20.6192 | -18.0465 | 2.5727 | 2.9640 | 1.0000 | 0.5694/0.5694 | 0.5319 | -1.1930 | 173.6305 | 1.0000 |
| assumed_legacy:optimistic:taker:fractional_sensitivity | -3.4245 | -3.2500 | 0.1745 | 0.1460 | 0.5938 | 0.0278/0.1538 | 0.1053 | -0.4372 | 5.2500 | 1.0000 |
| assumed_legacy:optimistic:maker:fractional_sensitivity | -1.5489 | -1.5489 | 0.0000 | -0.2344 | 0.7500 | 0.0694/0.2500 | 0.1935 | -0.3038 | 6.0489 | 0.7500 |
| assumed_legacy:optimistic:hybrid:fractional_sensitivity | -1.5489 | -1.5489 | 0.0000 | -0.2344 | 0.7500 | 0.0694/0.2500 | 0.1935 | -0.3038 | 6.0489 | 0.7500 |
| assumed_legacy:central:taker:fractional_sensitivity | -3.4245 | -3.2500 | 0.1745 | 0.1460 | 0.5938 | 0.0278/0.1538 | 0.1053 | -0.4372 | 5.2500 | 1.0000 |
| assumed_legacy:central:maker:fractional_sensitivity | -0.5669 | -0.5669 | 0.0000 | -0.0794 | 0.2500 | 0.0694/0.2500 | 0.1935 | -0.1047 | 2.0669 | 0.2500 |
| assumed_legacy:central:hybrid:fractional_sensitivity | -0.5669 | -0.5669 | 0.0000 | -0.0794 | 0.2500 | 0.0694/0.2500 | 0.1935 | -0.1047 | 2.0669 | 0.2500 |
| assumed_legacy:pessimistic:taker:fractional_sensitivity | -3.4245 | -3.2500 | 0.1745 | 0.1460 | 0.5938 | 0.0278/0.1538 | 0.1053 | -0.4372 | 5.2500 | 1.0000 |
| assumed_legacy:pessimistic:maker:fractional_sensitivity | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000/NA | NA | NA | 0.0000 | 0.0000 |
| assumed_legacy:pessimistic:hybrid:fractional_sensitivity | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000/NA | NA | NA | 0.0000 | 0.0000 |
| assumed_current_sports:optimistic:taker:fractional_sensitivity | -3.4246 | -3.2500 | 0.1745 | 0.1460 | 0.5938 | 0.0278/0.1538 | 0.1053 | -0.4372 | 5.2500 | 1.0000 |
| assumed_current_sports:optimistic:maker:fractional_sensitivity | -1.5489 | -1.5489 | 0.0000 | -0.2344 | 0.7500 | 0.0694/0.2500 | 0.1935 | -0.3038 | 6.0489 | 0.7500 |
| assumed_current_sports:optimistic:hybrid:fractional_sensitivity | -1.5489 | -1.5489 | 0.0000 | -0.2344 | 0.7500 | 0.0694/0.2500 | 0.1935 | -0.3038 | 6.0489 | 0.7500 |
| assumed_current_sports:central:taker:fractional_sensitivity | -3.4246 | -3.2500 | 0.1745 | 0.1460 | 0.5938 | 0.0278/0.1538 | 0.1053 | -0.4372 | 5.2500 | 1.0000 |
| assumed_current_sports:central:maker:fractional_sensitivity | -0.5669 | -0.5669 | 0.0000 | -0.0794 | 0.2500 | 0.0694/0.2500 | 0.1935 | -0.1047 | 2.0669 | 0.2500 |
| assumed_current_sports:central:hybrid:fractional_sensitivity | -0.5669 | -0.5669 | 0.0000 | -0.0794 | 0.2500 | 0.0694/0.2500 | 0.1935 | -0.1047 | 2.0669 | 0.2500 |
| assumed_current_sports:pessimistic:taker:fractional_sensitivity | -3.4246 | -3.2500 | 0.1745 | 0.1460 | 0.5938 | 0.0278/0.1538 | 0.1053 | -0.4372 | 5.2500 | 1.0000 |
| assumed_current_sports:pessimistic:maker:fractional_sensitivity | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000/NA | NA | NA | 0.0000 | 0.0000 |
| assumed_current_sports:pessimistic:hybrid:fractional_sensitivity | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000/NA | NA | NA | 0.0000 | 0.0000 |
| assumed_snapshot_market:optimistic:taker:fractional_sensitivity | -3.3547 | -3.2500 | 0.1047 | 0.1460 | 0.5938 | 0.0278/0.1538 | 0.1053 | -0.4323 | 5.2500 | 1.0000 |
| assumed_snapshot_market:optimistic:maker:fractional_sensitivity | -1.5489 | -1.5489 | 0.0000 | -0.2344 | 0.7500 | 0.0694/0.2500 | 0.1935 | -0.3038 | 6.0489 | 0.7500 |
| assumed_snapshot_market:optimistic:hybrid:fractional_sensitivity | -1.5489 | -1.5489 | 0.0000 | -0.2344 | 0.7500 | 0.0694/0.2500 | 0.1935 | -0.3038 | 6.0489 | 0.7500 |
| assumed_snapshot_market:central:taker:fractional_sensitivity | -3.3547 | -3.2500 | 0.1047 | 0.1460 | 0.5938 | 0.0278/0.1538 | 0.1053 | -0.4323 | 5.2500 | 1.0000 |
| assumed_snapshot_market:central:maker:fractional_sensitivity | -0.5669 | -0.5669 | 0.0000 | -0.0794 | 0.2500 | 0.0694/0.2500 | 0.1935 | -0.1047 | 2.0669 | 0.2500 |
| assumed_snapshot_market:central:hybrid:fractional_sensitivity | -0.5669 | -0.5669 | 0.0000 | -0.0794 | 0.2500 | 0.0694/0.2500 | 0.1935 | -0.1047 | 2.0669 | 0.2500 |
| assumed_snapshot_market:pessimistic:taker:fractional_sensitivity | -3.3547 | -3.2500 | 0.1047 | 0.1460 | 0.5938 | 0.0278/0.1538 | 0.1053 | -0.4323 | 5.2500 | 1.0000 |
| assumed_snapshot_market:pessimistic:maker:fractional_sensitivity | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000/NA | NA | NA | 0.0000 | 0.0000 |
| assumed_snapshot_market:pessimistic:hybrid:fractional_sensitivity | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000/NA | NA | NA | 0.0000 | 0.0000 |
| assumed_snapshot_market:central:taker:recorded_constraints | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000/NA | NA | NA | 0.0000 | 0.0000 |
| assumed_snapshot_market:central:maker:recorded_constraints | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000/NA | NA | NA | 0.0000 | 0.0000 |
| assumed_snapshot_market:central:hybrid:recorded_constraints | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000/NA | NA | NA | 0.0000 | 0.0000 |

Average position win/loss, gross losses, worst match-repeat and mean sampled exposure are fully reported in `results.json`; the same metrics for the 33 later assessment games are included there. No-trade baseline has zero P&L and no position win denominator. All recorded-constraint arms produce no fills at fixed sizing.

## Paired uncertainty

All repeats from a match are averaged together before resampling matches. Shared-team dependence and repeated development use remain limitations. Intervals quantify this frozen-cohort accounting sensitivity only.

| Arm versus same-assumption taker | Mean difference per match/repeat | Match-cluster 95% interval |
|---|---:|---|
| assumed_legacy:optimistic:maker:fractional_sensitivity | 0.0087 | [-0.0059, 0.0242] |
| assumed_legacy:optimistic:hybrid:fractional_sensitivity | 0.0087 | [-0.0059, 0.0242] |
| assumed_legacy:central:maker:fractional_sensitivity | 0.0132 | [-0.0007, 0.0274] |
| assumed_legacy:central:hybrid:fractional_sensitivity | 0.0132 | [-0.0007, 0.0274] |
| assumed_legacy:pessimistic:maker:fractional_sensitivity | 0.0159 | [-0.0015, 0.0334] |
| assumed_legacy:pessimistic:hybrid:fractional_sensitivity | 0.0159 | [-0.0015, 0.0334] |
| assumed_current_sports:optimistic:maker:fractional_sensitivity | 0.0087 | [-0.0059, 0.0242] |
| assumed_current_sports:optimistic:hybrid:fractional_sensitivity | 0.0087 | [-0.0059, 0.0242] |
| assumed_current_sports:central:maker:fractional_sensitivity | 0.0132 | [-0.0007, 0.0274] |
| assumed_current_sports:central:hybrid:fractional_sensitivity | 0.0132 | [-0.0007, 0.0274] |
| assumed_current_sports:pessimistic:maker:fractional_sensitivity | 0.0159 | [-0.0015, 0.0334] |
| assumed_current_sports:pessimistic:hybrid:fractional_sensitivity | 0.0159 | [-0.0015, 0.0334] |
| assumed_snapshot_market:optimistic:maker:fractional_sensitivity | 0.0084 | [-0.0062, 0.0239] |
| assumed_snapshot_market:optimistic:hybrid:fractional_sensitivity | 0.0084 | [-0.0062, 0.0239] |
| assumed_snapshot_market:central:maker:fractional_sensitivity | 0.0129 | [-0.0009, 0.0269] |
| assumed_snapshot_market:central:hybrid:fractional_sensitivity | 0.0129 | [-0.0009, 0.0269] |
| assumed_snapshot_market:pessimistic:maker:fractional_sensitivity | 0.0155 | [-0.0017, 0.0329] |
| assumed_snapshot_market:pessimistic:hybrid:fractional_sensitivity | 0.0155 | [-0.0017, 0.0329] |
| assumed_snapshot_market:central:maker:recorded_constraints | 0.0000 | [0.0000, 0.0000] |
| assumed_snapshot_market:central:hybrid:recorded_constraints | 0.0000 | [0.0000, 0.0000] |

## Limitations and next discriminating evidence

Fees-only accounting counterfactuals and synthetic execution sensitivity are not approximate replay from observed books. The archived original was previously collected with state feedback; this new comparison uses its frozen outputs, never fresh closed-loop inference. Changed maker/partial positions were not sent back to the model. Apparent savings may reflect opportunity selection, missed exits, adverse selection or changed fee-aware gating. Zero-fee accounting while preserving every fill is not used as maker returns.

Verified post-fill adverse selection is **NA** because actual order/fill/queue evidence is absent. A separately labeled **60-second signed historical-price proxy after synthetic fills** is reported, quantity-weighted with covered fill counts and timestamps in the logs; it uses the last price observation <= simulated fill time + 60 seconds (age <=90), requires a new sample after the fill, and never interpolates. Positive signed adverse change means the price moved against the fill side. This is not verified adverse selection or actual execution. Future forecast labels and varying next-decision marks are not used for this metric. Obtain historical market fee intervals, L2 snapshots/deltas, aggressor trade sizes/sequences, order/fill/cancel acknowledgments, clock/latency and queue calibration, minimum-size/tick changes, post-fill midpoints and rebate eligibility/pool data. Validate on development only with unchanged signals before any sealed evaluation.

## Reproduction and spend

```sh
PYTHONPATH=. HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 /Users/alexkan/Desktop/frontiers/rin-gym-forecast-only/.venv/bin/python -m experiments.execution_aware.evaluate
PYTHONPATH=. /Users/alexkan/Desktop/frontiers/rin-gym-forecast-only/.venv/bin/python -m unittest tests.trade_env.test_execution_evaluation tests.trade_env.test_execution
```

Source checksums/configuration/seed and execution logs are saved. `protocol.json` was saved before results; metadata-only refinement was saved before simulation. `verification.json` proves exact baseline reproduction and both guards. `price-provenance.json` pins the filtered timestamped historical price projection. The saved correctness amendment replaced a draft cached-fill quote assumption and corrected residual-inventory targeting before the reported rerun; no hypothesis, cohort, seed or scenario selection changed. MLflow is optional and unused. Incremental paid-service requests **0**; actual incremental invocation spend **$0**, service billing not queried. This harness contains no paid client or retries. The latest entire-chat cap is $5; prior research authorizations do not increase it.

Protected ledger before/after: `2f6051410929bd283e984574ecce2a944e82aa12911a13d24c194f48dc5a995d`, state **pending_enrollment**, **0 enrolled protected matches**. No enrollment, protected predictions or protected labels. Active quarter-original-live inputs were excluded.
