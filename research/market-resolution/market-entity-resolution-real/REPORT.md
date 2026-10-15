# Real Kalshi–Polymarket comparison

October 13, 2026. Model: `jev-1.13.0`. Primary policy and references frozen before inference.

**Result:** At the predeclared 0.99 model-probability threshold, the hybrid preserves the heuristic baseline’s 19 accepted identity matches, cuts reviews from 21 to 10, and uses 27 rather than 60 inference calls. Raw Jev accepts six identity matches. None of the methods falsely accepts a nonmatching advertised identity at that threshold. This is a pilot about a bounded, deliberately selected set; it does not establish production precision or superiority over a mature matcher.

## What was compared

An advertised identity match means the same event, period/instant, selected participant and advertised outcome/threshold bucket. It is a candidate pairing, not a claim of identical payouts. Different feeds, aggregation, rounding or exception payouts can exist inside an advertised identity pair. CPI’s explicit one-decimal precision permits >3.9% to map to >=4.0%. Sports winners do not map to spreads or totals. Polymarket outcome names and token IDs are recorded explicitly, including team-name outcomes rather than YES/NO.

Strict settlement compatibility is scored separately. A documented counterexample yields `different`; unresolved material terms yield `unknown`. General oracle/venue governance differences alone are not used to reject all pairs.

## Data and label provenance

- Official public API catalogs: 1,072 distinct Kalshi markets across 13 selected series and 600 distinct Poly markets from six volume-ranked pages. The extra initial unfiltered catalog was not the evaluation pool.
- Kalshi series source agencies and 13 distinct linked contract PDFs were collected and text-extracted. All 32 targeted catalog/PDF downloads completed and have URL, retrieval-time and SHA-256 entries in `manifest.json`.
- Kalshi KXBTCD, KXFED and KXFEDFUNDSYEAR downloads reached the 200-market per-series cap and returned another cursor; these catalogs are explicitly incomplete. Poly pages were captured from a moving volume-ranked listing, so despite zero duplicate IDs, moving-page omissions cannot be excluded.
- Sixty actual pairs were selected: 26 advertised identity matches and 34 actual near-misses/unrelated controls. No text was rewritten to manufacture negatives. Their source records and PDF terms are frozen in `reference.json`.
- The investigating assistant authored labels from the sources before inference. The labeler and implementation author are the same; there was no independent human adjudication. Generic contract PDFs may change and the fetched versions are not independently certified as the historical rules at original market issuance.
- Most pairs share a small number of rule templates. The 60 cases are correlated and selected for coverage; they are not a random deployment sample or an independent family/time holdout.

## Methods

Heuristics use supported-family identity keys, calendar and timezone normalization, exact selected-side mapping, rate-change buckets and CPI one-decimal cutoffs. Missing or unsupported fields cause review. Explicit known source/fallback conflicts are checked deterministically. This is a limited prototype, not an optimized commercial baseline.

Raw Jev reads frozen rules, full linked Kalshi contract text, source metadata and selected outcomes. It gets one Choice question for nominal identity and one for settlement compatibility. It never receives a reference label, rationale or heuristic decision. Accepted positive and negative model decisions require the relevant option probability to be at least 0.99; the rest go to review/unknown.

The hybrid auto-decides exact supported identity keys and explicit exclusions, then sends 27 unresolved identity/settlement cases to Jev with normalized source facts alongside raw rules. Deterministic settlement conflicts veto compatibility. For supported exact identity keys it does not require model confirmation. Evaluator/prompt details are in `evaluation-metadata.json` and `evaluate.py`.

## Primary comparison: 0.99 threshold

| Method | Accepted true identities / 26 | False identity accepts | Reviews / 60 | Decision coverage | Jev calls |
|---|---:|---:|---:|---:|---:|
| Heuristics | 19/26 (73.1%) | 0 | 21/60 | 65.0% | 0 |
| Raw Jev | 6/26 (23.1%) | 0 | 29/60 | 51.7% | 60 |
| Hybrid | 19/26 (73.1%) | 0 | 10/60 | 83.3% | 27 |

Each method’s accepted identities had zero observed false positives, but the denominators are only 19, 6 and 19. Do not interpret these observations as proven 100% precision. Coverage includes correctly rejected nonmatches as well as accepted matches.

Raw Jev’s top-choice identity label, without abstention, agrees with 53/60 references (88.3%). At a 0.5 acceptance threshold it accepts 22 true identities and three false identities. The 0.99 policy prevents those false accepts but misses many actual overlaps.

## Strict settlement findings

Of the 26 advertised overlaps, 18 have documented payout divergences and eight remain unresolved. There are no independently established fully compatible positive references. Therefore strict-equivalence precision/recall cannot be validated by this set, and no method establishes a safe interchangeable contract pair.

| Method | Documented conflicts detected among 18 advertised overlaps | Conflicts detected across all 52 different-payout references | Compatible accepts |
|---|---:|---:|---:|
| Heuristics | 13/18 | 33/52 | 0 |
| Raw Jev | 1/18 | 3/52 | 0 |
| Hybrid | 13/18 | 33/52 | 0 |

Raw Jev’s top settlement option is `different` on all 60 pairs, including the eight unresolved references, but only three predictions clear 0.99. Its high-threshold conflict-detection count must not be confused with its top-label count. Since there are no confirmed compatible positives, an always-different classifier cannot be meaningfully penalized for rejecting such positives here. Predictions of `different` on unknown references remain unadjudicated.

Concrete source-backed divergences:

1. Sports: Kalshi can settle at a fair price if a game does not start within 48 hours; Poly waits for a postponed game to finish and specifies 50-50 for outright cancellation. These nominal game matches are not payout-equivalent under those exceptions.
2. Bitcoin monthly targets: Kalshi uses a cumulative trimmed mean of CF BRTI from issuance, while Poly uses Binance BTC/USDT one-minute high prices over the calendar month. Strict/inclusive strike boundaries also differ.
3. September CPI: Kalshi >3.9% and Poly >=4.0% align at one-decimal publication precision, but Kalshi extends its wait after a shutdown whereas Poly can resolve from a previous month if the relevant release is absent by its fallback deadline.
4. A 25bps Fed action: Poly explicitly rounds a 12.5bps change into the 25bps bracket; Kalshi’s exact 25bps outcome can pay differently.

## Error inspection and baseline gaps

Raw Jev mistakes include a hockey rematch on the next day, >4.0% versus >=4.0%, and December 1 versus January 1. It also labels four same-advertised-target Bitcoin pairs as nonmatches, apparently mixing the requested nominal identity task with their genuinely different measurement definitions. That last category depends on the deliberately broad identity definition; it would not be an error in strict payout matching.

The heuristic baseline misses five Bitcoin overlaps because its full-month parser does not handle Kalshi’s abbreviated `Oct 31, 2026` title format. Poly daily Bitcoin slugs use word dates rather than the parser’s supported ISO form. It also abstains on six moneyline/spread-total negatives because it requires a complete selected-side key before applying all exclusions. These are routine engineering gaps, not evidence that AI is necessary for those cases. The baseline and thresholds were not silently fixed after results became visible. A stronger baseline could reduce the hybrid’s incremental benefit.

## Post-hoc threshold sensitivity

The following cutoffs were explored after inspecting results. They are descriptive on the same pilot, not calibrated or held-out operating thresholds. The heuristic decisions stay fixed.

| Model threshold | Method | True identities accepted / 26 | False accepts | Reviews / 60 |
|---|---|---:|---:|---:|
| 0.50 | jev | 22/26 | 3 | 0/60 |
| 0.50 | hybrid | 24/26 | 1 | 0/60 |
| 0.80 | jev | 13/26 | 1 | 15/60 |
| 0.80 | hybrid | 20/26 | 0 | 7/60 |
| 0.90 | jev | 9/26 | 0 | 20/60 |
| 0.90 | hybrid | 20/26 | 0 | 7/60 |
| 0.95 | jev | 9/26 | 0 | 22/60 |
| 0.95 | hybrid | 20/26 | 0 | 8/60 |
| 0.99 | jev | 6/26 | 0 | 29/60 |
| 0.99 | hybrid | 19/26 | 0 | 10/60 |

At 0.90 the hybrid accepts 20 true identities, zero observed false identities, and leaves seven reviews; raw Jev accepts nine true identities and leaves twenty reviews. At 0.50 the hybrid admits one false identity. Lower thresholds therefore cannot simply be declared safe based on this selected set.

## Retrieval and runtime

The shared deterministic candidate ranker places all 26 labeled positive Poly markets in the top five among the frozen 600 Poly markets. This measures retrieval recall only on known positive examples inside these bounded catalogs. It does not establish coverage of all exchange overlaps or precision of the full candidate stream. The reference-pair benchmark also includes deliberate controls outside that top-five stream.

| Pipeline | Successful inference calls | Reported input tokens | Reported output tokens | Median end-to-end call latency |
|---|---:|---:|---:|---:|
| Raw Jev | 60 | 195,272 | 5,050 | 1.861s |
| Hybrid | 27 | 86,395 | 2,271 | 1.655s |

The hybrid uses 55% fewer inference calls and approximately 56% fewer reported input tokens. Both experiments together produced 87 successful inference responses. Calls ran with four concurrent workers. Latency includes client/network/processing time, not model-only latency. Local heuristic work excludes shared collection, PDF extraction and candidate ranking. Token usage is not a verified billing ledger.

## Recommendation

For this pilot, use deterministic identity keys and explicit rule-conflict checks first. Jev is useful for resolving unsupported candidates and reducing review work; this experiment does not show a recall gain at the predeclared threshold. Keep identity matches and settlement compatibility separate in the product. Normalize dates and evaluate boundary cases in code, especially strict/inclusive CPI thresholds and game rematches.

Before deployment claims: repair baseline parsing gaps in a versioned development run, obtain independent reference adjudication, add confirmed compatible positives, and evaluate on new event families and a prospective time holdout with natural candidate negatives. Test probability calibration and repeated/swapped inputs. Do not tune a threshold on this set and present its result as independent validation.

## Review artifacts

- `pair-review.csv`: all IDs, selected Poly outcome/token, source URLs, reference rationale, model probabilities and method decisions.
- `reference.json`: complete frozen source records and linked contract text for every pair.
- `manifest.json`, `freeze.json`: source hashes, retrieval timing, reference/implementation hashes.
- `responses/jev/` and `responses/hybrid/`: raw answers, usage, model versions, elapsed times and payload hashes. Gold labels and rationales were excluded from payloads.
- `PROTOCOL.md`: pre-inference scope and decision rules.
- `metrics.json`, `threshold-sensitivity.json`: machine-readable primary and exploratory results.

## Commands run

```sh
python3 /Users/alexkan/Desktop/frontiers/outputs/market-entity-resolution-real/collect.py
python3 /Users/alexkan/Desktop/frontiers/outputs/market-entity-resolution-real/prepare.py
python3 /Users/alexkan/Desktop/frontiers/outputs/market-entity-resolution-real/evaluate.py --offline
python3 /Users/alexkan/Desktop/frontiers/outputs/market-entity-resolution-real/evaluate.py --env-file /Users/alexkan/projects/.env
python3 /Users/alexkan/Desktop/frontiers/outputs/market-entity-resolution-real/report.py
```

No exchange credentials were used and no trades were placed. Only the explicitly approved TypeSafe key was loaded for inference; it was not printed or persisted in artifacts.

## Pair-by-pair decisions

| Pair | Family | Identity reference | Heuristics | Raw Jev | Hybrid | Settlement reference |
|---|---|---|---|---|---|---|
| pair_001 | sports_nfl | match | match | review | match | different |
| pair_002 | sports_nfl | match | match | match | match | different |
| pair_003 | sports_nfl | match | match | match | match | different |
| pair_004 | sports_nba | match | match | match | match | different |
| pair_005 | sports_nba | match | match | match | match | different |
| pair_006 | sports_nba | match | match | review | match | different |
| pair_007 | sports_nhl | match | match | match | match | different |
| pair_008 | sports_nhl | match | match | review | match | different |
| pair_009 | sports_near_miss | nonmatch | review | nonmatch | nonmatch | different |
| pair_010 | sports_near_miss | nonmatch | review | nonmatch | nonmatch | different |
| pair_011 | sports_near_miss | nonmatch | review | nonmatch | nonmatch | different |
| pair_012 | sports_near_miss | nonmatch | review | nonmatch | nonmatch | different |
| pair_013 | sports_near_miss | nonmatch | review | nonmatch | nonmatch | different |
| pair_014 | sports_near_miss | nonmatch | review | nonmatch | nonmatch | different |
| pair_015 | sports_near_miss | nonmatch | nonmatch | nonmatch | nonmatch | different |
| pair_016 | sports_near_miss | nonmatch | nonmatch | review | nonmatch | different |
| pair_017 | sports_near_miss | nonmatch | nonmatch | review | nonmatch | different |
| pair_018 | sports_near_miss | nonmatch | nonmatch | review | nonmatch | different |
| pair_019 | sports_near_miss | nonmatch | nonmatch | review | nonmatch | different |
| pair_020 | sports_near_miss | nonmatch | nonmatch | nonmatch | nonmatch | different |
| pair_021 | fed_26OCT | match | match | review | match | unknown |
| pair_022 | fed_26OCT | match | match | review | match | different |
| pair_023 | fed_26OCT | match | match | review | match | unknown |
| pair_024 | fed_26OCT | match | match | review | match | different |
| pair_025 | fed_26OCT | match | match | review | match | unknown |
| pair_026 | fed_26DEC | match | match | review | match | unknown |
| pair_027 | fed_26DEC | match | match | review | match | different |
| pair_028 | fed_26DEC | match | match | review | match | unknown |
| pair_029 | fed_26DEC | match | match | review | match | different |
| pair_030 | fed_26DEC | match | match | review | match | unknown |
| pair_031 | fed_near_miss | nonmatch | nonmatch | nonmatch | nonmatch | different |
| pair_032 | fed_near_miss | nonmatch | nonmatch | nonmatch | nonmatch | different |
| pair_033 | fed_near_miss | nonmatch | nonmatch | nonmatch | nonmatch | different |
| pair_034 | fed_near_miss | nonmatch | nonmatch | nonmatch | nonmatch | different |
| pair_035 | fed_near_miss | nonmatch | nonmatch | review | nonmatch | different |
| pair_036 | fed_near_miss | nonmatch | nonmatch | nonmatch | nonmatch | different |
| pair_037 | bitcoin_monthly | match | review | review | review | different |
| pair_038 | bitcoin_monthly | match | review | review | review | different |
| pair_039 | bitcoin_monthly | match | review | review | review | different |
| pair_040 | bitcoin_monthly | match | review | review | review | different |
| pair_041 | bitcoin_monthly | match | review | review | review | different |
| pair_042 | bitcoin_near_miss | nonmatch | review | nonmatch | nonmatch | different |
| pair_043 | bitcoin_near_miss | nonmatch | review | nonmatch | nonmatch | different |
| pair_044 | bitcoin_near_miss | nonmatch | review | nonmatch | nonmatch | different |
| pair_045 | bitcoin_near_miss | nonmatch | review | review | review | different |
| pair_046 | bitcoin_near_miss | nonmatch | review | nonmatch | nonmatch | different |
| pair_047 | cpi | match | match | match | match | different |
| pair_048 | cpi_near_miss | nonmatch | nonmatch | review | nonmatch | different |
| pair_049 | cpi_near_miss | nonmatch | nonmatch | nonmatch | nonmatch | different |
| pair_050 | cpi_near_miss | nonmatch | nonmatch | nonmatch | nonmatch | different |
| pair_051 | aliens | match | review | review | review | unknown |
| pair_052 | aliens | match | review | review | review | unknown |
| pair_053 | aliens_near_miss | nonmatch | review | review | review | different |
| pair_054 | aliens_near_miss | nonmatch | review | review | review | different |
| pair_055 | unrelated_control | nonmatch | nonmatch | nonmatch | nonmatch | different |
| pair_056 | unrelated_control | nonmatch | nonmatch | nonmatch | nonmatch | different |
| pair_057 | unrelated_control | nonmatch | nonmatch | nonmatch | nonmatch | different |
| pair_058 | unrelated_control | nonmatch | nonmatch | nonmatch | nonmatch | different |
| pair_059 | unrelated_control | nonmatch | nonmatch | nonmatch | nonmatch | different |
| pair_060 | unrelated_control | nonmatch | review | nonmatch | nonmatch | different |
