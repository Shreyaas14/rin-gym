# Published research and execution evidence

No tested direction establishes a profitable, executable advantage over the market. This publication adds the completed eight-direction development study and maker/taker-aware execution implementation to the existing forecasting/trading experiment PR. It does not run another experiment or incur paid-service spend.

The bookmaker experiment was cancelled before launch. The free-feed complete-set basket experiment is an **unrun proposal** retained for reference. Its daily follow-up was deleted at the user's request; no collector, orders or basket returns exist.

## Reports

| Direction | Evidence and conclusion |
|---|---|
| [Synthesis](REPORT.md) | Ranked findings, study limits, fee/fill assumptions and spending reconciliation |
| [1. Action consistency](../../artifacts/eight-directions/d1_consistency/REPORT.md) | Diagnostic disagreement; the changed executor did not establish improved P&L |
| [2. Market prior](../../artifacts/eight-directions/d2_market_prior/REPORT.md) | Tested blend and prompt variants still trailed market forecasts |
| [3. Calibration](../../artifacts/eight-directions/d3_calibration/REPORT.md) | Improved model Brier score on later development games, still worse than market |
| [4. Position context](../../artifacts/eight-directions/d4_position_bias/REPORT.md) | Held-side probability shift; overall forecast harm and a profitable fix remain unproven |
| [5. Timing and memory](../../artifacts/eight-directions/d5_timing_memory/REPORT.md) | Inconclusive, concentrated effects and substantial template echo |
| [6. Training scale](../../artifacts/eight-directions/d6_training_scale/REPORT.md) | Learning efficacy unresolved; offline probability-loss/serialization checks only |
| [7. Execution realism](../../artifacts/eight-directions/d7_execution_realism/REPORT.md) | Exact accounting reproduction and explicitly synthetic execution sensitivities |
| [8. Specialization](../../artifacts/eight-directions/d8_specialization/REPORT.md) | No registered regime qualified on the earlier development screen |

[Execution report](../../artifacts/execution-aware/REPORT.md), [fee/fill engine](../../app/trade_env/execution.py), [offline harness](../../experiments/execution_aware/evaluate.py), [focused tests](../../tests/trade_env/test_execution.py) and [evaluation tests](../../tests/trade_env/test_execution_evaluation.py) provide implementation details and numerical evidence. Maker/hybrid sensitivities are not verified maker returns; historical depth, queue and fee-effective intervals were unavailable. Recorded minima also invalidate the old nominal one-share assumptions under the inspected current metadata.

Completed study token-cost estimate: **$0.74839119**; retained conservative maximum reservations: **$1.92314358**, below the superseding $5 total task cap. Invoice billing is unavailable. Execution implementation/evaluation and publication made zero paid calls. These are separate from the previously published studies' individually authorized budgets; see [spending](spending-total.json) and [verification](verification/final.json).

## Unrun designs and holdout

- [Cancelled bookmaker design](NEXT_EXPERIMENT.md): cancelled before any collection or paid requests.
- [Free-feed basket design](FREE_FEED_BASKET_PROTOCOL.md): proposed only, with realistic non-atomic execution and fixed success/stop rules; not launched and no scheduled continuation remains.

Only development data and guard receipts are published. The actual protected ledger, protected predictions/outcomes/scores, credential files and unrelated active experiments are excluded. Its recorded head is `2f6051410929bd283e984574ecce2a944e82aa12911a13d24c194f48dc5a995d`, with pending enrollment. A pending policy is not an empirically tested protected cohort.

## Offline validation and provenance

The experiment code and numerical artifacts are copied byte-for-byte from the verified research and execution checkouts. Historical machine-local paths and hashes remain as provenance. This index supplies repository-relative report links for GitHub. Frozen development inputs are included under `inputs/`; the original release archive and initialized protected ledger are not distributed.

Three frozen input console logs contain original trailing spaces. Their explicit `.gitattributes` entries preserve those bytes while excluding the raw logs from whitespace lint; code and document checks remain enabled.

Run the following from the repository root with Python 3.12 and the project's installed dependencies. They do not call a paid service:

```sh
PYTHONPATH=. HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python -m unittest discover -s tests -v
PYTHONPATH=. HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python -m unittest experiments.eight_directions.test_budget experiments.eight_directions.d1_consistency.test_analysis experiments.eight_directions.d6_training_scale.test_integration experiments.eight_directions.d8_specialization.test_analysis -v
PYTHONPATH=. HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python -m experiments.execution_aware.evaluate --source . --out artifacts/execution-aware --verify
git diff --cached --check
```

The saved execution audit and direction receipt verifiers require the original initialized ledger at the recorded location and reject a changed seal. Do not create a substitute ledger and call that verification of the original holdout. The saved numerical evidence can be inspected without that ledger; offline unit tests use their own controlled fixtures. Do not run the research setup script or paid `--live` commands: immutable snapshots, start markers and retained reservations prevent accidental paid restarts.
