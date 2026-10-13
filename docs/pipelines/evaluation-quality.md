# Free local evaluation quality and protected holdout workflow

The canonical evaluator runs locally with Python, NumPy and SQLite. Optional **open-source MLflow** adds a local experiment UI; no subscription, remote account, model-provider calls, or paid judge is required. The tracking adapter explicitly uses a workspace-local SQLite URI and local artifact directory, ignoring remote tracking URI defaults.

## What is implemented

- Independent scorer reference tests, whole-match weighting tests, strict probability/label validation, future-event/price mutation tests, and validation-selection isolation tests.
- A pending holdout policy for the **next eligible soccer cohort**. No fixtures have been selected, and there are no real prospective predictions yet.
- Pre-kickoff enrollment of explicit matches and prediction windows, one frozen candidate/baseline comparison, frozen artifacts/code, minimum match count and a Brier noninferiority margin.
- An append-only SQLite event ledger with hash chaining, transactional writes, no prediction replacement, locally generated receipt times, and immutable outcomes.
- No score release until every enrolled match has resolved. The first release is persisted; subsequent calls return the identical report without recomputing it.
- Enrolled matches are blocked from the existing preparation, fitting, temperature-selection and retrospective-run paths, including after score release.
- Per-candidate failed/missing counts, paired match-bootstrap Brier differences, classwise calibration and leave-one-match-out sensitivity. Incomplete coverage blocks promotion; scores on the common successful subset are explicitly descriptive.

The existing World Cup results are now a **development/regression benchmark** for further changes. They cannot be made into a fresh holdout by changing split labels.

## Install and inspect

Use a recent `uv` (the first executable on your PATH may be an older version):

```sh
uv sync --locked --extra tracking
uv run rin-gym eval init
uv run rin-gym eval status
uv run rin-gym eval quality --mlflow
```

Without MLflow, omit `--extra tracking` and `--mlflow`. The quality command writes a uniquely named JSON receipt under `artifacts/eval-quality/` even if a test fails, and exits nonzero on failure. It tests the harness; it does not report a forecasting improvement.

If a machine-local `uv` configuration is malformed, use a recent executable with `--no-config` rather than editing global settings. All commands also accept the global `rin-gym --workspace /absolute/path ...` option.

The ledger is `data/evals/ledger.sqlite3`. Keep it and the registered artifacts backed up; these local files are ignored by Git. The database is deliberately not reset by `eval init`.

To view MLflow, start it from the same workspace:

```sh
uv run mlflow server --host 127.0.0.1 --port 5000 \
  --backend-store-uri "sqlite:///$(pwd)/artifacts/mlflow/tracking.db"
```

Then open `http://127.0.0.1:5000`. Quality checks and holdout results occupy separate experiments. Exporting a report twice creates two tracking runs with the same report hash; it does not rerun the evaluation. Canonical evidence is the local ledger/report, not MLflow's display averages.

## Freeze a real future cohort

First finalize and version the baseline and candidate. Register every artifact required to reproduce predictions: model parameters, configuration, prompt/tool specifications, external adapter code and the dependency lockfile. The forecast package source is fingerprinted automatically; external code must be supplied explicitly.

```sh
uv run rin-gym eval register --name poisson-v1 \
  --artifact data/processed/forecast/model.json \
  --artifact app/forecast/config.json --artifact uv.lock
uv run rin-gym eval register --name candidate-v1 \
  --artifact candidate-model.json --artifact candidate-config.json \
  --artifact candidate-adapter.py --artifact uv.lock
```

Candidate registration closes at enrollment. Changes to registered artifacts or forecast source invalidate later submissions. Keep the frozen environment available through scoring. A newly developed candidate needs a new **future** cohort, not another attempt at this one.

Create `cohort.json` using actual audited future fixtures. This example is a **schema illustration with placeholders**, not an enrollable dataset:

```json
{
  "name": "soccer-cohort-v1",
  "target_definition": "regulation_home_draw_away",
  "baseline": "poisson-v1",
  "candidate": "candidate-v1",
  "minimum_games": 30,
  "noninferiority_margin": 0.01,
  "matches": [
    {
      "match_id": "provider-actual-match-id",
      "kickoff_utc": "REPLACE_WITH_FUTURE_ISO_TIMESTAMP",
      "resolution_not_before_utc": "REPLACE_WITH_LATER_ISO_TIMESTAMP",
      "cases": [
        {
          "case_id": "provider-actual-match-id-checkpoint-1",
          "observation_cutoff_utc": "REPLACE_WITH_CHECKPOINT_ISO_TIMESTAMP",
          "deadline_utc": "REPLACE_WITH_LATER_PREDICTION_DEADLINE"
        }
      ]
    }
  ]
}
```

Supply at least `minimum_games` distinct matches, not the single illustrative row. Choose the count based on development variance and a minimum useful effect; 30 and 0.01 are examples, not justified defaults. The implementation requires at least two matches so the paired analysis is defined, which is not a claim that two matches provide adequate evidence.

Each case has an explicit cutoff and later output deadline. A prediction can be recorded only after its cutoff; it must be received before its deadline. Enrollment must precede every kickoff. All case deadlines must precede their match's resolution boundary. Use timezone-aware timestamps. Declare enough time after regulation for outcome adjudication. Regulation results exclude extra time and penalties.

```sh
uv run rin-gym eval enroll --cohort cohort.json
```

Only one cohort can be enrolled in a workspace. This is intentional: the CLI cannot silently reset a holdout or replace its membership. For a later study, use a separate workspace and retain the old ledger. Copy or centralize the historical match catalog when establishing a new workspace; the tool cannot discover previously inspected games in unrelated directories.

## Log prospective predictions

The current logging interface accepts a forecast from your inference code. It does **not** fetch a live provider feed, schedule a daemon, or claim that the existing historical FIFA archive is a live input. Wire the API into the live inference path before enrolling fixtures.

The input JSON is a strict envelope. `cutoff` and `latest_event_timestamp` use Unix seconds; they must correspond to real feed evidence and the enrolled cutoff:

```json
{
  "source_sha256": "LOWERCASE_SHA256_OF_THE_CAPTURED_SOURCE",
  "received_utc": "ACTUAL_LAST_INPUT_RECEIPT_TIME_WITH_TIMEZONE",
  "observation": {
    "cutoff": 0,
    "home_goals": 0,
    "away_goals": 0,
    "phase": "first_half",
    "elapsed_period_minutes": 12.0,
    "observed_events": 45,
    "latest_event_timestamp": 0
  }
}
```

The zero timestamps and placeholder strings above must be replaced. Required ordering is `latest_event_timestamp <= received_utc <= cutoff <= locally_recorded_time <= deadline`. Hash the captured, cutoff-limited source and retain it separately; the logger records its hash but does not independently certify the feed. No labels, final-score fields, future boundaries or retrospective replay fields are accepted by this schema.

```sh
uv run rin-gym eval record --candidate candidate-v1 \
  --case provider-actual-match-id-checkpoint-1 --input observation.json \
  --probabilities 0.45 0.30 0.25
```

Direct Python integration uses the same validation and transaction:

```python
from app.forecast.governance import Ledger

receipt = Ledger().record(
    candidate="candidate-v1",
    case_id="provider-actual-match-id-checkpoint-1",
    payload=observation_envelope,
    probabilities=predicted_home_draw_away,
)
```

There is no issue-time argument: the receipt time is generated locally after inference. The logger does not prove when inference started or that an external predictor actually used the registered artifacts; that is the inference adapter's responsibility. Use the supplied observation boundary in that adapter, keep label files and future events outside its process/container, and retain tool traces for future agents.

Record failures explicitly:

```sh
uv run rin-gym eval record --candidate candidate-v1 \
  --case provider-actual-match-id-checkpoint-1 --input observation.json \
  --error "model timeout"
```

Each candidate/case has **one** slot. An accepted failure, invalid probability vector, changed artifact or late submission consumes that slot and cannot be replaced by a successful retry. Calls for unknown cases or unopened windows are rejected without creating a slot. If the inference process dies before logging, the case is counted as missing during scoring. There is no fallback policy in this first implementation; incomplete coverage blocks promotion instead of pretending missing forecasts were successful.

## Resolve and release once

After the match's resolution boundary and verified end of regulation, append its outcome and hash an adjudication evidence file:

```sh
uv run rin-gym eval resolve --match provider-actual-match-id \
  --target 0 --ended-utc ACTUAL_END_OF_REGULATION_WITH_TIMEZONE \
  --source adjudicated-result.json
uv run rin-gym eval score
uv run rin-gym eval export-mlflow
```

Targets are `0=home`, `1=draw`, `2=away`. `resolve` does not infer a target from the file: the operator supplies the adjudicated class and the source is hashed for audit. Outcomes cannot be replaced. A correction, cancellation, shifted schedule or premature match ending may invalidate the protocol; do not alter it to rescue a favorable result. The strict initial implementation has no cancellation/exclusion amendment workflow. All matches must resolve before release, otherwise the cohort remains unscored.

A successful prediction recorded after the declared match end is rejected during resolution, preventing it from being treated as prospective evidence. Reports include confidence intervals, but a local whole-match bootstrap does not model all shared-team/tournament dependence. The `improved` flag requires complete coverage, the declared minimum games, and an upper 95% paired Brier interval below zero; `noninferior` uses the predeclared margin. These are mechanical checks, not automatic deployment or proof of broad generalization.

The CLI will return the same released report on future `score` calls. It does not prevent a human from inspecting locally stored labels or calculating additional scores manually. Do not use those additional looks to claim a new confirmatory result.

## Threat model and evidence limits

SQLite triggers, transactions, frozen fingerprints and chained hashes protect against accidental mutation, concurrency races and ordinary workflow bypasses. They do not protect against someone who controls the filesystem, changes the clock, removes the ledger or rewrites all hashes. For stronger guarantees, run prediction generation and evaluation under separate OS identities and externally anchor receipt hashes before outcomes resolve. No cloud service is required for that separation, but it is not configured here.

Historical LLM memorization is still possible. Future, actually recorded forecasts are the main evidence for generalization; historical and synthetic tests are useful for regression and harness correctness. Provider publication times must be collected rather than inferred from event times.

## Validation

```sh
uv run python -m unittest discover -s app/forecast/tests -v
uv run python -m unittest discover -s tests -v
uv run rin-gym eval quality
uv build
```

The quality suite uses temporary ledgers, controlled clocks and synthetic fixtures. It includes future data, contaminated split membership, invalid output, duplicate submission, late prediction, changed model/scorer, early result release, incomplete coverage, and ledger corruption cases. These fixtures never enroll synthetic games in the real workspace holdout.

## Tinker integration

See [Tinker evaluation](tinker-evaluation.md) for dataset exclusion checks, the cookbook training/evaluator adapters, and `eval tinker-predict`. MLflow remains optional. A durable Tinker attempt claim prevents resampling after interruption; incomplete attempts remain missing predictions and block promotion.
