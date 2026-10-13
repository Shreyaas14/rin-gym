# Experiment results: no profitable policy established

These development experiments do **not** establish positive P&L or generalization. The most recent fresh combined-exit replay preserved approximately the original win rate but worsened net P&L. The protected holdout remained sealed and pending enrollment throughout.

## Fresh closed-loop evaluations

All three policy experiments below use the earlier run-3 Qwen3-8B checkpoint on the same 72 historical development games, with three repeats per game. P&L is aggregate simulated dollars after fees/spread, not account returns. Win rate is positive-P&L cases divided by traded cases. Repeats are clustered by game for uncertainty estimates.

| Policy | Position size | Net P&L | Win rate | Evidence |
|---|---:|---:|---:|---|
| Original baseline | 1 share | −$20.62 | 57.7% | [Paired original/conservative replay](../../artifacts/closed-loop-risk/REPORT.md) |
| Conservative forecast-based executor | 0.25 shares | −$1.12 | 12.3% | [Paired original/conservative replay](../../artifacts/closed-loop-risk/REPORT.md) |
| Original with quarter-sized positions | 0.25 shares | −$7.34 | 47.7% | [Fresh quarter-size replay](../../artifacts/quarter-original-live/REPORT.md) |
| Original entries with +10¢ take-profit / −25¢ stop-loss | 1 share | −$24.14 | 57.5% | [Fresh combined-exit replay](../../artifacts/bracket-original-live/REPORT.md) |

The quarter-size and bracket experiments reuse the verified original baseline; their alternative-policy responses are fresh. The conservative experiment changes entry selection as well as size and exits. Quarter sizing changes position feedback and produced different decisions. Bracket exits use displayed net liquidation value relative to actual entry cost, then execute at delayed quotes; they are checkpoint exits, not guaranteed stop prices. Later re-entry remains allowed.

Fresh-run full-rate token estimates were $0.853 (paired original/conservative), $0.433 (quarter-size), and $0.413 (combined exits). Each was below its separately authorized $5 cap; estimates are not billing invoices. Publishing these results does not launch another paid run.

## Offline analyses and the earlier pilot

- [Loss audit](../../artifacts/loss-audit/REPORT.md): reconstructs 432 game/repeat cash-flow totals and tests entry/exit overlays. The frozen-tape combined exits suggested −$17.17 P&L and 61.1% win rate, but the subsequent fresh replay did not reproduce that improvement. No tested overlay reached profitability. Cost $0.
- [Earlier saved-run risk comparison](../../artifacts/earlier-run-risk/REPORT.md): applies alternative execution to saved predictions from the earlier 60.7%-win-rate run. Cost $0; not fresh model inference.
- [Forecast-only risk comparison](../../artifacts/risk-policy/REPORT.md): offline execution analysis of the small forecasting-only pilot. Cost $0.
- [Forecasting-only pilot and halftime audit](../../artifacts/forecast-only/REPORT.md): separate four-update experiment; no convincing forecast-quality or fixed-policy P&L improvement. Halftime labels did not require relabeling.

## Evidence and reproduction

Fresh-run directories contain protocols, request receipts, predictions, per-game results, summaries, verification records and checksums. The reconstructed development task snapshot is in `artifacts/earlier-run-risk/tasks.jsonl.gz`. See each report for exact commands and limitations.

The scripts preserve historical machine-local paths and source hashes for provenance. Verifying the recorded holdout seal requires the original initialized ledger; do not create a substitute ledger and claim the original seal was verified. Other collaborators can inspect the checked-in numerical evidence and checksums without that ledger. New runs require configured Tinker authentication, appropriate input/ledger paths and a new output directory; paid runners refuse to overwrite an existing protocol.

No holdout dataset or credential files are included. These repeatedly inspected historical games are development evidence, not protected out-of-sample validation. No model promotion is recommended from these results.
