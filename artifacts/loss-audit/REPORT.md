# Losing-position audit — exploratory development results

No tested overlay reaches positive net P&L. The 10¢ take-profit / 25¢ stop-loss combination improves aggregate win rate and P&L in both saved runs, but is a follow-up hypothesis selected after inspecting the first sweep; it is not validated out of sample.

## Largest losing positions

Examples below are one-share positions from the original run, not independent matches across repeats. All positions and complete checkpoint paths are in `original-positions.json`.

| Match | Entry | Loss | Diagnosis |
|---|---:|---:|---|
| Qatar–Switzerland, away | $0.9576 | −$0.9576 | Still quoted $0.945 at h2+30, then zero settlement; checkpoint stops cannot capture the late reversal. |
| Portugal–Congo DR, home | $0.9289 | −$0.9289 | Quote declined to $0.665 at halftime and $0.455 at h2+30; model kept holding. |
| Uruguay–Cabo Verde, home | $0.9001 | −$0.9001 | At h2+15 the displayed quote was $0.875 but delayed fill was $0.515; idealized stop fills would overstate protection. |
| Spain–Cabo Verde, home | $0.8712 | −$0.8712 | Gradual decline to $0.645 at the final checkpoint; held to zero. |

67 original positions held to zero settlement lost $37.08 in total. 209 of 246 entries had negative edge according to the model’s own result probabilities versus displayed all-in cost. This inconsistency motivates forecast calibration and action consistency checks, but filtering on those probabilities performs poorly here. Expensive entries (all-in displayed cost ≥75¢) alone account for only −$5.48: they are not the whole problem.

## All tested policies

Win rate means positive-P&L match/repeat cases divided by traded cases. There are 72 distinct games × 3 repeats per run; the two runs use the same games, so the second is only a trajectory robustness check. Prices, thresholds and gains are per share. Original totals use one share; quarter-run totals use 0.25 shares.

| Policy | Original P&L | Original win rate | Traded | Quarter fresh P&L | Quarter win rate |
|---|---:|---:|---:|---:|---:|
| original | $-20.62 | 57.7% | 208 | $-7.34 | 47.7% |
| entry_cost_max_0.75 | $-15.14 | 35.9% | 117 | $-7.11 | 28.6% |
| entry_cost_max_0.85 | $-15.52 | 47.3% | 150 | $-6.60 | 38.2% |
| entry_cost_max_0.95 | $-18.08 | 53.3% | 182 | $-6.87 | 45.9% |
| entry_edge_min_0 | $-4.57 | 19.4% | 36 | $-2.57 | 14.7% |
| entry_edge_min_0.02 | $-5.08 | 13.8% | 29 | $-2.33 | 11.2% |
| stop_loss_0.15 | $-19.35 | 49.0% | 208 | $-6.75 | 46.8% |
| stop_loss_0.25 | $-17.95 | 55.3% | 208 | $-7.10 | 47.7% |
| stop_loss_0.35 | $-18.81 | 56.2% | 208 | $-7.27 | 47.7% |
| take_profit_0.1 | $-19.84 | 63.5% | 208 | $-6.82 | 50.5% |
| take_profit_0.2 | $-21.39 | 58.7% | 208 | $-7.19 | 49.5% |
| exit_below_liquidation_value | $-13.70 | 31.7% | 208 | $-4.83 | 30.6% |
| skip_switch_entries | $-20.61 | 56.2% | 208 | $-4.17 | 45.8% |
| followup_take_0.10_stop_0.25 | $-17.17 | 61.1% | 208 | $-6.58 | 50.5% |
| followup_exit_h2_30 | $-12.87 | 51.9% | 208 | $-6.07 | 44.0% |

## Interpretation and next experiment

The combined bracket changes original results from 120/208 wins to 127/208. It loses 5 previously winning cases while rescuing 12 losing cases. Its total P&L improvement is $3.45; the 95% paired game-cluster bootstrap interval is [-4.9966626703125, 11.798278637187503]. This interval is descriptive and unadjusted for trying multiple rules. The total stays negative after removing any one game: [-18.769533225000004, -14.298783225000005].

The next candidate to preregister on additional development games is the combined bracket, keeping original entry choices. It is a loss-reduction hypothesis, not a demonstrated route to profitability. Avoid adding tighter entry filters based on this sweep: they sacrifice many winners. A forced exit of existing positions at h2+30 reduces late settlement losses but also reduces win rate; positions newly entered at that checkpoint have no later exit opportunity in these traces.

The fresh quarter run also made 441 entries versus 246 in the original, including 172 switch entries versus 15. Quarter-run switch entries netted −$3.18; skipping them improves P&L but lowers win rate. Size-only prompt changes therefore altered turnover substantially, not just exposure.

## Method and limitations

Frozen original entry/exit tape; filters skip positions, early exits stay flat until the next original entry. Switches close the old position even when the new entry is filtered. Signals at recorded checkpoints only; delayed fills and fees applied. No new model responses. All policy comparisons are exploratory on previously inspected development games.

Take-profit/stop signals use displayed net liquidation proceeds minus actual entry cost; execution uses the recorded delayed quote and existing spread/fee functions. There is no intraminute monitoring or guaranteed stop price. Each original position is handled independently; an early-exited position stays flat until the next originally recorded entry, and a blocked switch still closes the previous position. Saved beliefs may be inappropriate after counterfactual position changes. These simulations cannot establish fresh closed-loop performance.

All 432 match/repeat P&L totals were independently reconciled from entry/exit cash flows. Five targeted tests check quote-only triggers, delayed fills, entry exclusions and original-policy identity. Inputs are SHA-256 pinned in `audit.json`; complete outcomes, trade paths, clustered uncertainty and counts are saved beside this report.

Commands:
```sh
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python -m experiments.forecast_only.loss_audit
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python -m unittest experiments.forecast_only.test_loss_audit
```

Cost: $0. No inference requests. Holdout ledger verified read-only before and after, unchanged at 2f6051410929bd283e984574ecce2a944e82aa12911a13d24c194f48dc5a995d. Enrollment remains pending; no holdout scoring or enrollment.
