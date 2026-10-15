# Held-out real-market benchmark, version 2

Approved scope: improve the existing heuristic matcher, collect 300–500 new real pairs, compare heuristics, Jev, an LLM, and the hybrid. Total new paid model spend must remain below $5. Original pilot artifacts are preserved.

## Data and development boundary

The original 60 pairs are development/regression data. All 400 new pairs are assessment data; no new model outputs or correctness labels are used to revise the baseline or prompts. Selection excludes previously evaluated Kalshi event groups and Polymarket IDs/event groups. Previously fetched but unevaluated catalog records are not automatically disqualified. Shared rule templates are not held out, so this is an event holdout, not a rule-template or prospective-time generalization claim.

Public sources comprise complete-at-acquisition selected Kalshi series, complete-at-acquisition Polymarket league moneylines, and bounded general/mixed Polymarket catalogs. Source hashes and retrieval timestamps are preserved. Catalog completeness is reported per source. The market snapshots are not atomic across venues.

Exactly 400 outcome-specific pairs are selected before inference: NFL 88, NBA 88, NHL 144, MLB 40, Bitcoin point-price 40. Anchors are shuffled with seed 20261013, then the four highest-ranked candidates per anchor are selected under fixed quotas. Both real Polymarket outcomes/token IDs are explicit candidates. This is a stratified retrieval-stream benchmark, not deployment prevalence and not an exhaustive retrieval-recall evaluation. Repeated selected outcomes and shared games are correlated. Group uncertainty by event and report family/template sensitivity.

## Tasks and methods

Advertised identity and full settlement compatibility are separate questions, using the original pilot definitions. A same-event selected outcome can have different exception payouts. Unknown material terms never establish compatibility. Do not reject all pairs merely because their platforms have different governance.

- Heuristics: source-field parsing plus deterministic identity/rule-conflict checks. Abbreviated month ends, word dates, specified daily observation hours, team aliases/order, timed MLB tickers, and sports-type exclusions are fixed before assessment.
- Jev: pinned `jev-1.13.0`, one request with two questions per pair. Full raw rules and linked Kalshi terms, no reference labels or heuristic decisions.
- LLM: one JSON classification request per pair with the same state and questions. Direct route, if a credential is available: pinned `gpt-4.1-mini-2025-04-14`, temperature 0, maximum 512 output tokens. If unavailable, a separately logged, priced gateway may be evaluated under a locked route/model configuration before its first assessment call. Never silently pool different LLM models/routes.
- Hybrid: exact supported identity/rule exclusions are automatic; unresolved identity or settlement goes to Jev with normalized source facts. Deterministic conflicts veto compatibility. Known identity decisions are preserved; compatible acceptance requires established identity.

The primary acceptance threshold is 0.99 for positive and negative model decisions, inherited from the pilot rather than tuned on this set. The remainder is review/unknown. LLM self-reported probabilities are not a calibrated equivalent of Jev's probabilities. Report raw top-label accuracy and risk/coverage curves separately after labels arrive; exploratory thresholds do not become independently validated thresholds.

## Budget before inference

A persistent SQLite ledger enforces a shared $5 cap across all methods and resumptions. Every request reserves its maximum billable cost before dispatch. Reservations are transactional across threads/processes. Failed/time-out requests retain their full reservation unless billing is independently established. Retries require a new reservation; there are no automatic POST retries. Returned usage settles token-priced calls. Gateway requests have an explicit USD payment ceiling and a ledger reservation before the tool can pay. Uncertain receipts remain fully reserved. Zero free requests or metadata checks can increase paid spend.

Jev pricing verified from https://docs.typesafe.ai/models: $0.042 per million input tokens, output free, 64k combined context. Reserve the full context plus 25% cushion rather than estimated English token counts. Direct GPT-4.1-mini pricing verified from https://developers.openai.com/api/docs/models/gpt-4.1-mini: $0.40 input/$1.60 output per million tokens; reserve its full input context and the configured 512-token output cap plus 25% cushion. A model/usage outside the declared bounds halts future calls. Report both usage-derived cost and uncertain reservations. Provider invoices or unilateral tariff changes are outside the local ledger; never present usage estimates as verified invoices.

## Ground truth and blinding

No heuristic-generated labels or LLM-as-judge labels are treated as gold. The user will arrange independent review. `review-blind.csv` contains identifiers, titles, selected outcomes, token IDs, and source URLs, with blank label/evidence fields. The static review page shows frozen rule snapshots and enables CSV export while hiding predictions, scores, ranks, and normalized heuristic keys. A second reviewer should label independently; disagreements require explicit adjudication.

Identity labels: `match`, `nonmatch`, or `unknown`. Settlement labels: `compatible`, `different`, or `unknown`. Every known label needs a cited source clause; `different` should state a payout counterexample; `compatible` should cover observation source/time, comparator/rounding, participant/outcome, and material exceptions. No confirmed compatible positives may be assumed or manufactured. If reviewers establish none, strict-equivalence recall remains unidentifiable.

## Analysis and claims

Until independent labels are returned, publish only decision coverage, abstentions, source/split provenance, latency, and cost. Do not publish accuracy, precision, recall, false-positive counts, or a winner based on agreement with the same heuristics being evaluated.

Once labels are independently adjudicated, compare methods on identical known-label pairs: positive precision/recall, false accepts, unknown-reference accepts separately, decision coverage, paired correct-automation gains, per-family results, clustered uncertainty, and settlement metrics separately. Retain unknowns and unavailable responses in coverage/missingness denominators. Report compatible-positive support explicitly. False accepts and recall are more relevant than accuracy on numerous natural negatives.

The first milestone provides comparative evidence under bounded catalogs and shared templates. It cannot prove universal equivalence, production precision, cross-template generalization, or safe arbitrage. Review time and actual provider billing must be measured before claiming total operating cost savings.
