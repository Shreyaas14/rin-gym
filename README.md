# rin-gym

Sports data pipelines, event replay, score simulation and prediction-market forecast evaluation.

## Install as a uv tool

Use a current uv release:

```sh
uv tool install .
rin-gym --help
```

From this repository's evaluation/integration branch:

```sh
uv tool install 'git+https://github.com/Shreyaas14/rin-gym.git@feat/eval-quality'
```

The core tool includes NumPy, Arrow, DuckDB and Pillow. Optional evaluation integrations install with `uv tool install 'rin-gym[tracking,tinker] @ git+https://github.com/Shreyaas14/rin-gym.git@feat/eval-quality'`. Video encoding additionally requires FFmpeg with libx264; PNG previews work without it.

Every command uses the current directory as its data/output root. Set `RIN_GYM_WORKSPACE` or pass `--workspace` before the command. Relative pipeline and replay paths resolve inside that workspace; no data is written into the installed package.

## FIFA ingestion, market history and arbitrary-match replay

```sh
rin-gym --workspace ./workspace pipeline fifa ingest
rin-gym --workspace ./workspace pipeline fifa verify --cache data/raw/fifa
rin-gym --workspace ./workspace pipeline markets --workers 8
rin-gym --workspace ./workspace pipeline market-history --workers 8
rin-gym --workspace ./workspace pipeline verify-markets --report artifacts/market-verification.json
rin-gym --workspace ./workspace replay import --dataset data/processed/fifa --markets data/processed/fifa-market-history --database data/replay.duckdb
rin-gym --workspace ./workspace replay verify --database data/replay.duckdb
rin-gym --workspace ./workspace replay list-matches --database data/replay.duckdb
rin-gym --workspace ./workspace replay render --database data/replay.duckdb --match 400021513 --output artifacts/replay/example --preview
```

Use `--offline` for repeat ingestion/mapping/history runs from verified caches; remove `--preview` to encode video. The acquired cohort contains 484 FIFA matches and 40,848 sparse events. [Pipeline schemas and acquisition](docs/pipelines/fifa-parquet.md), [market identity and resolution](docs/pipelines/fifa-market-resolution.md), and [replay details](app/replay/README.md) explain the source contracts. Corrupt acquisition data prevents snapshot publication; the market verifier compares all normalized values against their raw histories.

## Historical audit and forecast simulator

```sh
rin-gym --workspace ./workspace pipeline audit
rin-gym --workspace ./workspace pipeline verify
rin-gym --workspace ./workspace pipeline fifa-census
rin-gym --workspace ./workspace pipeline fifa-join
rin-gym --workspace ./workspace forecast prepare
rin-gym --workspace ./workspace forecast run
rin-gym --workspace ./workspace forecast verify
rin-gym --workspace ./workspace forecast simulate --match 400021444 --utc 2026-06-25T02:30:00Z --runs 100000
```

The first requests public data; later runs reuse cached sources. Frozen NBA/EPL sample selections ship inside the wheel and are copied into a workspace only when missing. [The forecast harness](app/forecast/README.md) compares models against live Polymarket on whole-match chronological splits.

The published 20-game World Cup test result is inconclusive: simulator Brier 0.3231 versus live-market 0.3300, with a paired interval including zero and sensitivity to a two-minute observation delay. Those games are now development/regression data, not a fresh test set.

## Granular soccer play-by-play

```sh
rin-gym --workspace ./workspace play-by-play --match 3943043 --competition 55 --season 282
```

[The detailed viewer](app/play_by_play/README.md) displays StatsBomb passes, carries, possession sequences and observed 360 snapshots. The standalone HTML is generated under `artifacts/play-by-play/<match>/`. Player positions between observations are unknown. Its market chart remains on a separate UTC clock until period alignment is verified.

The newly merged trade environment is also available through `rin-gym trade-env --help`; its record builder uses `rin-gym pipeline trade-records`. Training dependencies remain optional.

## Protected prospective evaluation

```sh
rin-gym --workspace ./workspace eval init
rin-gym --workspace ./workspace eval quality
```

[Protected evaluation](docs/pipelines/evaluation-quality.md) supports candidate freezing, prediction receipts, coverage gates and one-time readout. The forecast harness rejects enrolled games during retrospective preparation, loading, fitting, simulation, selection and scoring. Optional [Tinker integration](docs/pipelines/tinker-evaluation.md) uses development-only training/evaluation data; no prospective performance claim exists until an independent cohort is enrolled and completed.

## Development and CI

```sh
uv sync --frozen --all-extras
uv run python scripts/check_tests.py
uv build
```

GitHub Actions runs regression suites on Python 3.11 and 3.12, builds distributions, installs the wheel as a uv tool, and executes tests outside the checkout. A separate job exercises native Tinker interfaces and local MLflow with optional dependencies. Public APIs and paid inference are not called by CI.

Repository layout: `pipelines/` acquires and validates data; `app/` contains replay and forecasting; `rin_gym/` provides the installed CLI; `docs/` and `research/` preserve methodology; `data/` and `artifacts/` contain reproducible results. Large raw caches, databases, generated videos and local evaluation ledgers remain Git-ignored.
