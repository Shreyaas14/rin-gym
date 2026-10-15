# Prediction-market entity resolution: Jev smoke test

The live test is complete. See [REPORT.md](REPORT.md) for results, limitations, and reproduction commands. Initial result: 23/24 correct relationship labels, with 5/6 equivalents accepted and no false equivalents at the predeclared 0.99 Choice probability threshold. Four post-hoc diagnostics examined pair swapping, UTC normalization, and an exhaustive cancellation payout table.

This experiment tests whether Jev distinguishes equivalent YES payouts, complementary outcomes, related contracts with material differences, unrelated events, and missing rules.

The 24 hand-authored cases are synthetic simplified contracts. They are not Kalshi or Polymarket snapshots and are not independently adjudicated. They cover paraphrases, timezone conversion, unit conversion, outcome reversal, strict boundaries, thresholds, price feeds, averaging windows, data revisions, statistical series, certification, taking office, overtime, cancellation, and missing information.

Model inputs exclude the expected label and its rationale. Each pair receives one Choice relationship question and two supporting Noul questions. Live acceptance uses a predeclared 0.99 equivalence probability threshold, whose calibration has not been validated. The baseline accepts title similarity of at least 0.8. No heuristic vetoes are benchmarked yet; the baseline only illustrates the limitations of titles.

Run fixture validation and the offline baseline:

```sh
python3 /Users/alexkan/Desktop/frontiers/outputs/market-entity-resolution/run.py --offline
```

The user explicitly approved reading and using the TypeSafe key for these experiments. Run:

```sh
python3 /Users/alexkan/Desktop/frontiers/outputs/market-entity-resolution/run.py --env-file /Users/alexkan/projects/.env
```

This makes one model-discovery request and 24 inference requests to `https://api.typesafe.ai`. Use `--resume` to reuse completed cases after a transient failure. Transient requests have up to three attempts. Only the named TypeSafe key is loaded; it is never stored in outputs or displayed. API failure bodies are suppressed. The script uses the Python standard library and writes only experiment fixtures, public model metadata, model answers, timings, and summaries here. Network permission is required in the current sandbox. Rerunning the experiment can incur additional API usage.

The user supplied explicit approval after the original credential-file review rejection. The successful run returned `jev-1.13.0`. One connection dropped after eight cases; the resumed run completed the remaining sixteen. `followups.py` made four additional successful inference calls. Those diagnostics were selected after inspecting the initial outputs and are not held-out evaluation.

Before claiming production readiness, test frozen real exchange rules with independently reviewed labels, natural near-misses, event-family/time holdouts, candidate-retrieval recall, and false-match rates with confidence intervals. Compare heuristic checks, Jev, an LLM, and a hybrid on the same candidate set.

API references: [Choice](https://docs.typesafe.ai/primitives/choice), [Noul](https://docs.typesafe.ai/primitives/noul), [Quick start](https://docs.typesafe.ai/introduction/quickstart).
