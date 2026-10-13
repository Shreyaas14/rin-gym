# PR risk fixes and installed-tool validation

Validated October 13, 2026. All five review findings are fixed and have regression tests.

| Finding | Protection and regression coverage |
| --- | --- |
| Cache corruption could publish empty odds snapshots | Fatal acquisition/integrity exceptions preserve the current publication; batch, fallback and corrupt payload tests |
| Price verifier trusted writer values | Every normalized timestamp/price is checked against raw token history; token ownership and in-game flags independently checked |
| Simulation could inspect protected games | Simulation and shared loaders reject protected IDs before data reads; four new governance regressions |
| Null score metadata reset displayed score | Prefix score states carry each non-null component, preserve explicit corrections and keep penalties separate |
| Incomplete finals passed verification | Completed matches require terminal evidence and verified final scores; unfinished matches are counted separately |

The full acquisition verifier compares 16,035,880 samples for 85,492 tokens / 42,746 priced contracts, checks 7,692 raw hashes, and preserves 484 matches and 40,848 events. The updated replay verifier reconciles all 484 completed matches, including nine extra-time and four shootout games.

The installed uv tool runs from an unrelated directory. Its deterministic offline workflow tests cover cached FIFA ingestion, verification, lossless export, certified market mapping, all outcome histories, independent price verification, DuckDB import, score audit and PNG rendering. A second workflow verifies a relative StatsBomb play-by-play output stays inside `--workspace`. Frozen sports manifests and the HTML viewer ship in the wheel; generated data does not.

Manual installed-tool checks also reproduce the original 60-game audit, the 72-match / 29,060-row forecast benchmark, and a 3,304-event / 2,882-snapshot StatsBomb viewer. FFmpeg/ffprobe validate a two-second H.264 replay at 1600×900 and 2 fps.

## Commands

```sh
uv sync --frozen --all-extras
HF_HUB_OFFLINE=1 uv run python scripts/check_tests.py
uv build
UV_TOOL_DIR=/tmp/rin-gym-tools UV_TOOL_BIN_DIR=/tmp/rin-gym-bin uv tool install dist/rin_gym-0.1.0-py3-none-any.whl
cd /tmp
HF_HUB_OFFLINE=1 /tmp/rin-gym-tools/rin-gym/bin/python /path/to/repo/scripts/check_tests.py --installed
rin-gym --workspace /path/to/workspace pipeline analyze
rin-gym --workspace /path/to/workspace pipeline verify
rin-gym --workspace /path/to/workspace forecast run
rin-gym --workspace /path/to/workspace forecast verify
rin-gym --workspace /path/to/workspace replay verify --database data/replay.duckdb
rin-gym --workspace /path/to/workspace replay render --database data/replay.duckdb --match zhs8gg1hvcuqvhkk2itb54pg --output artifacts/video-smoke --max-seconds 2 --fps 2
rin-gym --workspace /path/to/workspace play-by-play
```

The original fixes/tool suites contain 177 tests: 66 forecast, 65 FIFA, 21 replay and 25 integration/viewer/CLI tests. All pass with optional SDKs installed, including five native Tinker-interface and two local MLflow tests. Without optional extras, those seven tests skip; the full core workflow remains executable.

The new trade-environment PR was merged into main during implementation. Its 84 core tests are additionally included in CI and pass. Its separate tokenizer-dependent training test module is skipped in offline environments without the model tokenizer. No paid Tinker training or inference was launched. This validation does not establish remote checkpoint availability or prospective forecasting performance.

CI discovers every available suite, tests Python 3.11/3.12 on Linux, builds and installs the wheel outside the checkout, and checks native optional integrations on macOS. Obsolete runs are cancelled automatically. All branch synchronizations use merge commits; no force push or changes to persistent signing/credential configuration were needed.
