# Offline execution comparison

This harness compares the same 72 frozen, previously inspected development matches and three archived repetitions of the original final checkpoint. It does not initialize Tinker, MLflow, live trading or any paid client.

From the implementation checkout:

```sh
PYTHONPATH=. HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 /Users/alexkan/Desktop/frontiers/rin-gym-forecast-only/.venv/bin/python -m experiments.execution_aware.evaluate
PYTHONPATH=. /Users/alexkan/Desktop/frontiers/rin-gym-forecast-only/.venv/bin/python -m experiments.execution_aware.evaluate --verify
PYTHONPATH=. /Users/alexkan/Desktop/frontiers/rin-gym-forecast-only/.venv/bin/python -m unittest tests.trade_env.test_execution_evaluation tests.trade_env.test_execution
ruff check experiments/execution_aware/evaluate.py tests/trade_env/test_execution_evaluation.py
```

The saved `artifacts/execution-aware/protocol.json` is required. `--prepare-only` verifies the pinned source hashes and ledger, inventories schemas and reads only filtered contract metadata, then records the metadata refinement before loading simulated returns. `--source` selects the frozen research checkout; `--out` requires a copy of the frozen protocol in the intended owned output directory. Neither option permits arbitrary cohorts: task IDs must equal the pinned development IDs and be within the allowed ledger cohort.

The exact legacy baseline calls the unchanged `Replay` and checks each archived match-repeat P&L. Its cash ledger independently separates fees and spread. The controlled comparison uses `app.trade_env.execution` with a common initial taker-cost gate, the same archived action/forecast tape, fixed nominal one-share sizing, partial inventory and a guard against buying a new outcome while an old outcome remains. A switch considers combined sale/purchase fair-value edge, while the execution policy may reject an individually unfavorable exit. This frozen action comparison has different selection from the ungated exact legacy baseline, and different states were never sent back to the model.

Available data contain historical prices and a market-metadata snapshot, without historical executable bid/ask depth, aggressor trade flow or queue evidence. Every generated quote, depth and trade is labeled synthetic sensitivity. The three passive fill assumptions are 0%, 25% and 75%, with explicit queue assumptions, 60-second latency, 120-second expiry, 180-second signal validity, immediate local cancellation and strict as-of price availability with a 90-second maximum age. Touched prices do not produce fills by themselves. Hybrid crossing after normal expiry arrives at the 180-second signal deadline and is rejected; the deadline stays fixed. Earlier fallback after a post-only rejection can still cross before expiry. These scenarios are sensitivity envelopes, not validated maker returns or guaranteed extrema of profitability.

The saved 216 contract fee schedules all record taker coefficient 0.03, while current official sports documentation lists 0.05. June historical applicability is unknown. Reported assumptions keep these schedules separate, use zero rebates and never mark a current snapshot as a verified historical schedule. The raw `orderMinSize` value is 5; current Gamma documentation calls it USD notional, while order-book documentation describes the similarly named book minimum as shares. One share is below both interpretations. The recorded-constraint arm deliberately interprets Gamma's field as notional; fractional sensitivity arms explicitly relax minimum and tick constraints without changing one-share risk.

Artifacts include the protocol, filtered metadata/schema audit, per-intent and per-fill records, per-match results, paired match-cluster uncertainty (averaging repeats before resampling), later assessment metrics, exact reproduction and guard verification, spend and report. Verified post-fill adverse selection is unavailable because actual fill/role evidence is absent. A separately labeled 60-second historical-price proxy after synthetic fills is reported with coverage and timestamps. The historical price reader filters both match and exact YES-token IDs, reads prices only, preserves sample timestamps, and never exposes future prices to order selection. `REPORT.md` explains all metric denominators and limitations.

The holdout check reads only the ledger metadata and integrity chain. It does not enroll or read protected outcomes. The current ledger is pending enrollment with zero enrolled protected matches. Active experiments and their outputs are not inputs to this harness. Total new service invocation spend is $0; the latest entire-chat paid-service cap is $5.
