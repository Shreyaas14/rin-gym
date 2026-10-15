# Real-market pilot protocol

Frozen before inference. No Jev responses or probabilities may be used to select reference labels, tune parsers, or adjust thresholds in this run.

## Questions

1. **Identity:** Do Kalshi YES and the explicitly selected Polymarket outcome address the same advertised event, period, participant, and outcome/threshold bucket? Measurement-source, averaging, rounding and fallback differences are tracked separately. Sports moneyline versus spread/total, different meetings, different thresholds, and different observation instants are not identity matches. CPI cutoffs may be normalized using the explicitly stated one-decimal publication precision.
2. **Settlement compatibility:** Is there an explicit counterexample in the supplied rules that permits different payouts, or are material terms insufficiently comparable? `different` requires a documented counterexample; `unknown` represents an unresolved comparison. `compatible` would require establishing equivalent payouts under the supplied rules, beyond mere identity. Venue-wide dispute/governance differences alone do not decide this field.

## Data and sampling

Use frozen official public API snapshots, Kalshi series metadata and linked contract-term PDFs. The Poly selected outcome and token are recorded explicitly, avoiding an assumption that the first token is YES. Sample real overlaps and actual near-misses across NFL/NBA/NHL, Fed decisions, Bitcoin, CPI and alien-confirmation contracts. Include unrelated controls. Do not rewrite contracts to manufacture negatives. Catalogs are a bounded convenience sample, not complete exchange inventories. Retain pagination/truncation information.

The source catalogs contain 600 Poly markets ranked by 24-hour volume (six pages retrieved sequentially in a moving live catalog) and up to 200 Kalshi markets per selected series. Deduplicate IDs and report any pagination gaps or duplicates. Reference pairs are selected for diagnostic coverage, not to estimate production prevalence. Labels are authored by the investigating assistant from source evidence before model inference; they are not independently adjudicated or human-validated. Include uncertainty rather than force strict matches.

## Methods

- **Heuristics:** Category-specific deterministic parsing of event IDs, calendar dates, selected outcomes, advertised thresholds, bet types and CPI reporting precision. Return `review` for unsupported cases. Settlement checks use explicit source, averaging, delay and rounding clauses; unknown remains unknown.
- **Jev:** The same frozen raw rules and outcome mapping for every pair. A Choice question for identity and one for settlement compatibility. No gold label, rationale, or heuristic result is supplied. Use `jev-latest`, retain returned model version. Identity accepts at probability >= 0.99. Compatibility accepts at probability >= 0.99.
- **Hybrid:** Run the same deterministic identity exclusions first. Supply normalized facts and raw rules to Jev on supported/ambiguous candidate pairs; skip inference on deterministic nonmatches. Retain unknown if the model does not establish compatibility. Explicit deterministic settlement conflicts veto compatible predictions. Heuristic auto-acceptance is allowed only where an exact identity key is established; such an identity match is not automatic settlement acceptance.

Use the same reference pairs for all methods. Keep output decisions fixed after first evaluation. If prompts or heuristics are revised after results are visible, report the revision as a new development diagnostic, not a held-out improvement.

## Measurements

Report identity precision/recall, false matches, review rate, and per-family results. Score strict settlement conflict detection separately: compatible claims on `different` references are false accepts; compatible claims on `unknown` references are unsupported claims, not proven false outcomes. If the reference set contains no established compatible contracts, strict-equivalence recall is undefined and must not be invented.

For retrieval, measure whether each labeled positive's Poly market appears in the top five candidates across the frozen 600-market Poly pool. This is reference-positive retrieval recall only; catalog-wide or production recall is unknown. Report pair decisions separately from candidate generation. Measure model-response tokens, end-to-end successful latency and inference-call counts; do not equate sequential client latency with model-only latency or throughput. Retry transient failures up to three attempts and cache completed responses.

No trades, orders, or account modifications. TypeSafe credential use is authorized in this chat; never print or persist the key or request authorization headers.
