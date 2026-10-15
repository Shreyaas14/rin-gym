# Held-out Kalshi–Polymarket benchmark: current results

October 13, 2026, Pacific time. **400 new real pairs are frozen; heuristics, Jev, and the hybrid are complete. A valid LLM arm and independent reference labels remain pending.** No accuracy winner is established yet.

## What changed

The original 60-pair pilot remains intact as development/regression data. The new heuristic version fixes abbreviated month-end dates, word-based Bitcoin dates and explicitly specified observation hours, team aliases and ordering, timed MLB tickers, and early winner-versus-spread/total exclusions. The original sports matches/near-misses and monthly Bitcoin examples pass regression checks. No assessment labels were used to revise the matcher.

New source collection contains **1,003 Kalshi markets** across 12 selected series and **6,945 distinct Polymarket markets**. All selected Kalshi catalogs and the four Polymarket moneyline league catalogs exhausted their cursors at acquisition. General/mixed Poly catalogs are explicitly capped. After discovering the offset-depth limit, collection moved to the documented cursor endpoint with actual league-specific primary tags. Earlier exploratory pages are preserved in the manifest but do not silently substitute for the final catalog.

The assessment set contains **400 unique outcome-specific pairs**, from 100 Kalshi market anchors and 201 distinct selected Polymarket outcomes, grouped into **67 Kalshi events**:

| Family | Pairs |
|---|---:|
| NFL | 88 |
| NBA | 88 |
| NHL | 144 |
| MLB | 40 |
| Bitcoin point-price | 40 |

Previously evaluated Kalshi events and Polymarket IDs/event groups are excluded. Each shuffled anchor contributes its four highest-ranked natural candidates under frozen quotas. Both actual Poly outcome/token mappings are eligible; no contract text was manufactured. All 400 new pairs are held out from the original development set. Shared rule templates are not held out, and this is not a future-time or unseen-template test. The 400 pairs are correlated, not 400 independent events.

## Observable decisions at the frozen 0.99 threshold

These are output counts, **not true/false-match counts**. Independent reviewers have not established correctness.

| Method | Identity accepts | Identity rejects | Identity reviews | Valid pairs completed | Model calls |
|---|---:|---:|---:|---:|---:|
| Improved heuristics | 90 | 310 | 0 | 400 | 0 |
| Jev alone | 28 | 188 | 184 | 400 | 400 |
| Hybrid | 90 | 310 | 0 | 400 | 10 |
| LLM | — | — | — | 0 | No valid assessment responses |

**The hybrid produced the same identity and settlement decisions as the improved heuristics on every pair.** Its ten Jev calls addressed unresolved settlement cases; all remained unknown. Both methods marked 390 pairs different and ten unknown. Raw Jev marked 193 different and 207 unknown. No method accepted settlement compatibility. Those counts include rejected identities; they must not be mistaken for 390 independently proven rule conflicts.

The narrow conclusion is that the previous hybrid's reduction in identity reviews **did not persist after baseline repairs on this supported-family candidate set**. This says nothing yet about correctness, long-tail families, or whether a stronger LLM could help. The dataset/ranker intentionally emphasizes supported families, which favors structured parsing. An independent gold set is necessary to discover confident heuristic errors.

Pinned Jev `jev-1.13.0` returned all 410 planned responses. Raw Jev used 1,670,574 input tokens; hybrid used 58,211. Median end-to-end request latency was 0.184s and 0.202s respectively. These are client/network observations and not model-only latency or production throughput. The raw and hybrid arms use the same frozen raw contract rules; the hybrid additionally receives deterministic normalized source facts.

## $5 budget and LLM status

The shared persistent ledger was created and tested before model requests. Each request reserves its maximum billable amount; uncertain failures retain funds; retries need new reservations. Concurrency cannot exceed the cap. Token routes use full documented context limits plus a 25% cushion, rather than optimistic input estimates. The cap is unchanged at **$5 total** across methods/resumptions.

**Usage-derived Jev cost: $0.07260897. Remaining accounted budget: $4.92739103.** No uncertain reservations remain. This calculation uses published TypeSafe rates and returned usage; it is not a verified provider invoice. `budget-summary.json` and `budget.sqlite` preserve accounting. The ledger contains 415 reservations, including canceled/no-payment transport attempts; that is not a count of 415 paid model calls.

Only the authorized TypeSafe credential was available in the supplied environment. The direct LLM runner is prepared for `gpt-4.1-mini-2025-04-14`, but no OpenAI API credential was supplied. The async credential-path question remains pending.

The installed AgentCash plugin offered a possible credential-free fallback. A free development probe reported `openai/gpt-4o-mini`. The route was frozen separately before two assessment attempts, each limited to $0.01 with a shared-ledger reservation. Both returned the same contract summary rather than the requested two classifications. No valid LLM prediction was ingested; input truncation or message handling is a possible explanation, not a confirmed diagnosis. These attempts remain preserved as transport failures and will not be pooled into another LLM arm.

A second gateway advertised $0.05 but its live payment challenge quoted $0.15. No inference was dispatched under the lower reservation. A separately reserved $0.15 development attempt then failed to construct a payment payload because required domain metadata was absent. It did not yield an LLM response. AgentCash reported no payments for these attempts; its wallet balance stayed unchanged on the checked calls. Full probe receipts are preserved. No wallet funding, bridging, or credential extraction was performed.

## Independent review package

The user will arrange review. Open [review.html](review.html) for frozen rules, linked full Kalshi contract text, label/evidence fields, browser-local saving, and CSV export. Alternatively fill [review-blind.csv](review-blind.csv). Both hide method predictions and proposed labels. Instructions are in [REVIEW.md](REVIEW.md). Two reviewers should use separate copies/profiles and adjudicate disagreements.

Known identity labels require event/date/selected-outcome evidence. Settlement differences require an explicit payout counterexample; compatible labels require material source/time/comparator/exception coverage. Missing terms remain unknown. Do not convert the heuristic outputs into gold labels. Confirmed compatible positives cannot be assumed merely to balance the dataset.

`score.py` accepts externally reviewed CSV labels, requires reviewer/evidence fields, distinguishes unknown references from known errors, preserves missing outputs in coverage denominators, and flags whether the four-way comparison is complete. A partial adjudicated sample requires the explicit `--sample` flag. Anchor-event bootstrap intervals and family results are supported; shared-template/secondary-event dependence remains a limitation. Until review, no precision, recall, accuracy, or false-accept claim is reported.

## Validation and next completion steps

Eight offline tests pass: concurrent budget enforcement, resume/unknown-cost preservation, usage-bound halting, parser regressions, unknown-reference scoring, missing-prediction coverage, and rejection of blank gold labels. `verify.py` checks frozen hashes, all 400 pair/token IDs, event exclusions, blinded review rows, all 410 valid response schemas, and each response's budget reservation/payload hash. Review JavaScript passes syntax checking. The browser's file-protocol restriction prevented an in-browser preview; the HTML remains available as a local review artifact.

To finish the requested milestone: supply a valid LLM credential path/provider, run that arm through the same remaining shared budget, then score independently adjudicated labels. No additional spend is authorized above the existing $5 cap.

```sh
python3 outputs/market-entity-resolution-v2/verify.py
python3 -m unittest discover -s outputs/market-entity-resolution-v2 -p test_benchmark.py -v
python3 outputs/market-entity-resolution-v2/evaluate.py --methods llm --llm-env-file /path/to/approved.env
python3 outputs/market-entity-resolution-v2/score.py --labels /path/to/adjudicated-review.csv
```

Public/API sources: [Kalshi markets](https://docs.kalshi.com/api-reference/market/get-markets), [Polymarket cursor pagination](https://docs.polymarket.com/api-reference/markets/list-markets-keyset-pagination), [TypeSafe pricing](https://docs.typesafe.ai/models), and [direct LLM configuration/pricing](https://developers.openai.com/api/docs/models/gpt-4.1-mini). No exchange accounts or trades were used.
