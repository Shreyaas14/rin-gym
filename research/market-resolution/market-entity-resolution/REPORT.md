# Jev prediction-market matching smoke test

Run date: October 13, 2026. Returned model: `jev-1.13.0` through `jev-latest`.

Jev correctly classified 23 of 24 hand-authored synthetic contract pairs. At the predeclared 0.99 Choice equivalence-probability threshold, it accepted five of six equivalents and none of the 18 other cases. Four subsequent diagnostic calls exposed sensitivity to contract representation and swapping the two inputs. UTC normalization resolved the tested timestamp issue. A deterministic payout-table check correctly ruled out both equivalence and complementarity for the cancellation mismatch.

## Scope and limitations

These are simplified synthetic contracts, not Kalshi/Polymarket rule snapshots. The fixtures and gold labels were authored by the experiment designer, without independent adjudication. Labels were excluded from model input. The stress set deliberately contains near-misses with identical titles. There is no candidate-retrieval evaluation, LLM comparator, calibrated production threshold, independent holdout, or evidence of safe automated trading. The four follow-ups were selected after inspecting the initial outputs; do not pool them with the original 24-case accuracy.

## Initial results

| Measure | Observed value |
|---|---|
| Correct relationship labels | 23/24 (95.8%) |
| Accepted equivalent pairs at Choice P(equivalent) >= 0.99 | 5 |
| False equivalent accepts at that threshold | 0/5 accepted pairs |
| Recall among six synthetic equivalents | 5/6 (83.3%) |
| Median successful-call end-to-end latency | 0.944 seconds |
| Maximum recorded latency, including retries | 7.016 seconds |
| Initial successful-response token usage | 20,230 input; 2,455 output |
| Token usage across all 28 successful inference responses | 23,797 input; 2,861 output |

Latency includes client/network time and does not measure model-only execution or concurrent throughput. One inference connection failed before the resumed run; retries and saved-case resumption completed the experiment. Usage above is reported by successful responses; it is not the account billing ledger and may omit failed-request usage. Model-discovery requests were also made.

The naive title-only SequenceMatcher baseline at a fixed 0.8 threshold accepted 14 pairs, 13 incorrectly. This is an intentionally weak diagnostic baseline on an adversarial set, not a comparison against a tuned retrieval/matching system or an estimate of real-world accuracy.

## Material findings

1. **Settlement details matter.** The model recognized mismatches in thresholds, observation times, price feeds, averages, CPI series, revision policies, election certification, taking office, and overtime.
2. **Normalize dates and units in code.** Equivalent `16:00 UTC` and `08:00 UTC-08:00` timestamps received an equivalence probability of 0.81. Swapping the two inputs produced the wrong related label with P(equivalent)=0.28. A datetime parser converted both to UTC, after which Jev returned P(equivalent)=1.00. These are single observations; repeated trials are needed to separate pair-order effects from stochastic variation.
3. **Exception payouts need explicit checks.** Two sports contracts agree on completed games but differ on cancellation: one pays each side $0.50, the other pays YES $0 and NO $1. The initial model called them complementary (0.51), although both YES payouts are $1 when Boston wins a completed game. Swapping inputs yielded the correct related label (0.85). Adding an exhaustive code-derived payout table yielded related (0.56), still with substantial complementary probability (0.39). The deterministic table rejects both equivalence and complementarity without model uncertainty.
4. **Missing information differs from a conflict.** The relationship Choice correctly returned insufficient for both incomplete contracts. The supporting explicit-divergence Noul nevertheless returned 0.74 and 0.76 despite being instructed that missing terms do not establish an explicit difference.
5. **Primitives require separate calibration.** Identical rules received Choice P(equivalent)=1.00 and Noul P(same YES payout)=0.91. Those outputs come from different questions and cannot share an assumed numerical threshold. Multiple questions are not independent evidence and their probabilities must not be multiplied as if they were.

## Recommended hybrid

Normalize timestamps, units, entities, and numeric comparators before matching. Retrieve candidates with structured keys plus lexical/embedding search. Compare verified structured terms in code; missing fields remain unknown. Use Jev Choice for remaining semantic relationship judgments. Keep exact outcome mappings and exception payouts separate. For templated contracts, evaluate payout functions or representative boundary/fallback scenarios directly. Route low-confidence, contradictory, or incomplete outputs to a cited rule comparison by an LLM or human. Preserve rules and versions with every match.

The next useful benchmark is frozen real exchange contracts with independently reviewed labels, natural unmatched cases, family/time holdouts, repeated swapped-input trials, calibrated risk-versus-coverage curves, and identical candidate sets for heuristic, Jev, LLM, and hybrid systems.

## Per-case evidence

| Case | Gold | Jev relationship | P(equivalent) | Correct |
|---|---|---|---|---|
| `identical_rules` | equivalent | equivalent | 1.00 | yes |
| `paraphrased_rules` | equivalent | equivalent | 1.00 | yes |
| `timezone_conversion` | equivalent | equivalent | 0.81 | yes |
| `percentage_units` | equivalent | equivalent | 1.00 | yes |
| `participant_alias` | equivalent | equivalent | 0.99 | yes |
| `price_threshold_units` | equivalent | equivalent | 1.00 | yes |
| `opposite_sides` | complementary | complementary | 0.07 | yes |
| `election_negation` | complementary | complementary | 0.04 | yes |
| `strict_boundary` | related | related | 0.12 | yes |
| `different_threshold` | related | related | 0.00 | yes |
| `different_instant` | related | related | 0.02 | yes |
| `different_source` | related | related | 0.14 | yes |
| `spot_vs_average` | related | related | 0.01 | yes |
| `first_vs_revised` | related | related | 0.04 | yes |
| `headline_vs_core` | related | related | 0.00 | yes |
| `annual_vs_monthly` | related | related | 0.01 | yes |
| `projection_vs_certification` | related | related | 0.01 | yes |
| `winning_vs_taking_office` | related | related | 0.05 | yes |
| `overtime_exclusion` | related | related | 0.02 | yes |
| `cancellation_fallback` | related | complementary | 0.07 | no |
| `missing_source` | insufficient | insufficient | 0.01 | yes |
| `missing_overtime` | insufficient | insufficient | 0.02 | yes |
| `different_topic` | unrelated | unrelated | 0.01 | yes |
| `different_competition` | unrelated | unrelated | 0.02 | yes |

## Post-hoc diagnostic calls

| Diagnostic | Gold | Jev relationship | P(equivalent) |
|---|---|---|---|
| `timezone_swapped` | equivalent | related | 0.28 |
| `timezone_normalized` | equivalent | equivalent | 1.00 |
| `cancellation_swapped` | related | related | 0.03 |
| `cancellation_payout_table` | related | related | 0.04 |

## Reproduction

The following commands were run. The first live command completed eight cases before its connection dropped; the resume command completed the remaining sixteen. Secrets were never included in command arguments, printed, or stored in experiment artifacts.

```sh
python3 /Users/alexkan/Desktop/frontiers/outputs/market-entity-resolution/run.py --offline
python3 /Users/alexkan/Desktop/frontiers/outputs/market-entity-resolution/run.py --env-file /Users/alexkan/projects/.env
python3 /Users/alexkan/Desktop/frontiers/outputs/market-entity-resolution/run.py --env-file /Users/alexkan/projects/.env --resume
python3 /Users/alexkan/Desktop/frontiers/outputs/market-entity-resolution/followups.py --env-file /Users/alexkan/projects/.env
```

API references: [TypeSafe quick start](https://docs.typesafe.ai/introduction/quickstart), [Choice](https://docs.typesafe.ai/primitives/choice), [Noul](https://docs.typesafe.ai/primitives/noul). No LLM endpoint was used. No real exchange market feed was queried in this smoke test.
