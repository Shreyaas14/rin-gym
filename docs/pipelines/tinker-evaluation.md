# Tinker evaluation integration

Tinker runs fine-tuning and sampling. This harness owns game identity, chronological
splits, prospective receipts and final scoring. MLflow is an optional local view;
it is not imported or needed by the Tinker adapters.

Install the pinned SDK/cookbook with `uv sync --extra tinker`. Add `--extra tracking`
only for MLflow. The lockfile records the tested environment. Tinker API usage is
paid; installing the integration and running offline tests makes no paid requests.
Use Tinker's normal authentication setup; never put credentials in dataset files,
checkpoint manifests, CLI arguments, or the ledger.

## Development data contract

Run `rin-gym eval init` in the workspace first. Use the same workspace for every
training job and holdout command. The checks require an existing ledger and fail
closed if it is absent. They cannot protect jobs that bypass these adapters or
use another ledger.

Training and validation files are JSON arrays. Each row has exactly:

```json
{
  "match_id": "provider-match-123",
  "case_id": "provider-match-123-minute-10",
  "kickoff_utc": "2024-01-01T12:00:00Z",
  "label_available_utc": "2024-01-01T14:00:00Z",
  "observation": {
    "source_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "received_utc": "2024-01-01T12:10:00Z",
    "observation": {
      "cutoff": 1704111000,
      "home_goals": 0,
      "away_goals": 0,
      "phase": "first_half",
      "elapsed_period_minutes": 10,
      "observed_events": 1,
      "latest_event_timestamp": 1704110999
    }
  },
  "target": 0
}
```

`target` is the adjudicated regulation result, 0=home, 1=draw, 2=away. Replace the
illustrative hash with the actual source SHA-256. Canonical provider match IDs must
be consistent across all datasets and enrollment. The harness does not infer
aliases across providers or establish that caller-submitted provenance is true.

For the optional SFT probability-distillation builder, training rows also require
`teacher_probabilities`, a normalized array such as `[0.6, 0.2, 0.2]`. Supply
out-of-fold or otherwise leakage-reviewed teacher forecasts. These are training
answers, never prompt features. The harness checks their numerical validity but
cannot verify how a teacher was trained. Do not convert outcomes to one-hot
probabilities and mistake that for calibrated forecast supervision. Validation
rows have no teacher answer. The evaluator can also evaluate an independently
trained/RL checkpoint without using this SFT builder.

```sh
rin-gym --workspace /path/to/workspace eval tinker-check-data \
  --train train.json --validation validation.json
```

Checks reject protected matches in either split, overlapping game/case IDs,
duplicate cases, inconsistent within-game labels, nonchronological splits,
missing metadata, future observation receipts, and extra observation fields.
All training labels must be available before the first validation kickoff. Split
by whole games, never by individual replay snapshots. The audit reserves both
splits against subsequent holdout enrollment, even when enrollment is pending.
Use this check for reward-development/RL datasets too, converting their game
metadata to the validation row schema and providing a chronological training
reference; arbitrary external RL environments are not automatically inspected.

## Cookbook training and periodic validation

The integration uses the cookbook's `SupervisedDatasetBuilder` and
`SamplingClientEvaluator` interfaces, model-specific renderers, assistant-only
SFT loss masks, and example-normalized loss weights. It does not apply a random
row split. Partial final batches are retained. Data is checked when configuring
the adapters and again on use; changed files fail. Every training batch is checked
against the ledger. Keep the dataset files fixed throughout a run and resume.

```python
import asyncio
from tinker_cookbook.supervised import train
from app.forecast.tinker_bridge import make_dataset_builder, make_evaluator

workspace = "/path/to/workspace"
# Select a currently supported model and its recommended cookbook renderer.
model_name = "YOUR_SUPPORTED_MODEL"
renderer_name = "ITS_RECOMMENDED_RENDERER"
common = dict(
    train_path="train.json", validation_path="validation.json",
    model_name=model_name, renderer_name=renderer_name, workspace=workspace,
)
builder = make_dataset_builder(**common, batch_size=8)
evaluator = make_evaluator(**common, max_tokens=128)
config = train.Config(
    log_path=f"{workspace}/artifacts/tinker/run-001",
    recipe_name="soccer-probability-distillation",
    model_name=model_name, renderer_name=renderer_name,
    dataset_builder=builder, evaluator_builders=[evaluator], eval_every=20,
    wandb_project=None,
)
# Explicitly running this starts paid training.
asyncio.run(train.main(config))
```

The periodic evaluator returns match-weighted Brier, log loss, accuracy, and
coverage counts as `dict[str, float]` for Tinker's normal metrics logging. It
uses generation rather than treating token NLL as a forecasting quality score.
A malformed response aborts that validation invocation; no successful-only
aggregate is reported. Select checkpoints on this development validation set.
The sampling evaluator factory also fits the RL cookbook's
`evaluator_builders`; it must never receive protected holdout games.

## Frozen checkpoint and prospective logging

After selecting a checkpoint, save sampler weights and retain them through the
holdout period. Use a saved `tinker://.../sampler_weights/...` path, not a live
training client. Consult Tinker's checkpoint retention settings before enrollment.
Create a manifest:

```json
{
  "checkpoint": "tinker://RUN_ID/sampler_weights/SELECTED_CHECKPOINT",
  "model_name": "YOUR_SUPPORTED_MODEL",
  "renderer": "ITS_RECOMMENDED_RENDERER",
  "max_tokens": 128,
  "temperature": 0
}
```

Register this manifest plus relevant provenance artifacts (training configuration,
lockfile, dataset audit/teacher provenance) with the existing `eval register`
command before enrollment. Register the baseline as a separate candidate too.
Enrollment still remains pending until the next eligible soccer fixtures are
selected. Existing candidates registered before an integration/code update must
be versioned and re-registered before enrollment.

Once the cohort is enrolled and a case's prediction window opens:

```sh
rin-gym --workspace /path/to/workspace eval tinker-predict \
  --candidate tinker-v1 --case ENROLLED_CASE_ID \
  --input observation.json --manifest tinker-candidate.json
```

The observation file uses the strict envelope from the existing holdout protocol.
Only its observation fields reach the renderer; labels and arbitrary source text
are never injected into the prompt. One completion is requested at temperature
zero using renderer stop sequences. Only a cleanly terminated JSON probability
array is accepted. There is no output repair, renormalization, or resampling.

An append-only `prediction_started` event claims the candidate/case before client
creation or network I/O. It stores the manifest and observation hash. Completion
records the actual receipt time and links to that attempt. API/parse failures
consume the slot; late responses fail. API exception messages are not persisted
because they can contain sensitive request details. A process crash leaves a
claimed, missing prediction that blocks complete coverage; it cannot be rerun
through this adapter. Local clock/provenance trust limits still apply.

The SDK may retry transport delivery internally; this adapter never retries a
completed model output or starts a second application-level attempt for the case.
Run both frozen candidates on the predeclared cases, then resolve and score using
the existing holdout commands. MLflow export remains an explicit optional step.

## Validation and upstream references

Offline tests use the installed Tinker types and cookbook interfaces with fake
transport/rendering, checking masks, sampling parameters, incomplete outputs,
late receipts, claims, dataset exclusions and chronology. They do not establish
model quality or prove remote checkpoint access. A live smoke prediction must be
performed separately on development data before freezing candidates.

- [Tinker evaluation interfaces](https://tinker-docs.thinkingmachines.ai/tutorials/core-concepts/evaluations/)
- [Cookbook supervised data conversion](https://github.com/thinking-machines-lab/tinker-cookbook/blob/main/tinker_cookbook/supervised/data.py)
- [Cookbook supervised training](https://github.com/thinking-machines-lab/tinker-cookbook/blob/main/tinker_cookbook/supervised/train.py)
- [Tinker checkpoint management](https://tinker-docs.thinkingmachines.ai/tinker/howto/checkpoints/)
