# Kalshi histories, replay and simulation

Run from the repository root with Python 3.11+. This pipeline joins official
Kalshi contracts to the existing FIFA Premier League 2025/26 and World Cup 2026
matches. It writes the same core `markets.parquet` and `prices.parquet` schemas
used for Polymarket, with additive venue and price-provenance fields.

## Acquire and verify

```sh
python3 -m pip install -r app/replay/requirements.txt
python3 -m pipelines.kalshi.ingest --dataset data/processed/fifa
python3 -m pipelines.kalshi.verify \
  --dataset data/processed/fifa-kalshi \
  --fifa data/processed/fifa --cache data/raw/kalshi
```

The FIFA path can be a snapshot directory or a directory containing `current.json`.
Obtain it using the [FIFA pipeline](fifa-parquet.md) or the existing dataset release.
There is no required absolute producer path: the market manifest identifies both
FIFA Parquet files by SHA-256, and the importer checks the supplied local files.

By default, the `KXEPLGAME` and `KXWCGAME` series are searched in both the live
and historical market endpoints. The pipeline follows every page, then downloads
one-minute histories for safely linked regulation home/draw/away contracts.
This is the scope used by the current replay and simulator.

Broader prop ingestion is explicit and can take hours under public API rate
limits. It searches every `KXEPL*` and `KXWC*` series in the official Sports
catalog, excluding futures and contracts without an unambiguous fixture:

```sh
python3 -m pipelines.kalshi.ingest --dataset data/processed/fifa \
  --all-market-types --output data/processed/fifa-kalshi/all-markets
```

Alternatively, repeat `--series` to select particular families. It cannot be
combined with `--all-market-types`. Using a separate output directory preserves
the verified regulation dataset while an optional broad backfill is underway.

Requests share a rate limit and retry budget. Successful JSON responses are
immutable, compressed and content-addressed in `data/raw/kalshi`. Rerunning
resumes from that cache; `--offline` requires all requested responses to be
cached, and `--refresh` deliberately obtains new responses. A failed acquisition
leaves the previous `current.json` unchanged. Candidate snapshot directories from
failed runs can remain for diagnosis; only the version named by `current.json`
is published. Use `--catalog-only` to inspect mapping without downloading prices.

Each published snapshot contains:

- `markets.parquet` and `prices.parquet`: mapped contracts with observed prices.
- `manifest.json`: checksums, row counts, source hashes, policy version and coverage.
- `catalog-resolution.json`: an inclusion/exclusion decision for every distinct ticker.
- `catalog-summary.json`: counts at the fixture-resolution stage, before prices.
- `match-resolution.json`: which FIFA fixtures have all three priced regulation outcomes.

Excluded catalog responses stay in the raw cache. There is no price forward fill
and no fabricated liquidity. Contracts with no observed prices in the selected
window are absent from the final tables. Partial priced triplets may remain as
individual valid contracts, but cannot be used as a complete replay/simulation
triplet.

## Becker source and finer timestamps

[Jon Becker's prediction-market-analysis](https://github.com/jon-becker/prediction-market-analysis)
provides Kalshi trade Parquet files and an indexer for the same official API.
Its bulk download includes both exchanges and is approximately 36 GiB compressed.
The default pipeline uses the official API directly to avoid acquiring that
entire archive for this soccer subset. The bulk archive was not downloaded for
this implementation.

If you already have Becker's extracted `data/kalshi/trades` directory:

```sh
python3 -m pipelines.kalshi.ingest --dataset data/processed/fifa \
  --becker-trades /path/to/prediction-market-analysis/data/kalshi/trades \
  --output data/processed/fifa-kalshi/becker
```

The adapter filters its Parquet files by resolved tickers, converts the archive's
integer cents to probabilities and preserves hashes of the source files. Official
market rules are still acquired for identity resolution; a trade ticker alone
does not establish a trustworthy FIFA join. Coverage depends on the archive's
collection dates. An archive with no matching observed trades fails without
publishing an empty dataset.

For original trade timestamps from the official API instead of minute closes:

```sh
python3 -m pipelines.kalshi.ingest --dataset data/processed/fifa \
  --history trades --series KXWCGAME --match-id 400021513 \
  --output data/processed/fifa-kalshi/trades
```

Omit `--match-id` to acquire the selected series for every fixture; replace the
series filter with `--all-market-types` to include all mapped props. Trade
histories can be much larger. The API's current historical cutoff selects the
live and/or historical trade endpoint; pagination must terminate successfully.
Fractional timestamps survive in Parquet. Repeated trade IDs are deduplicated;
conflicting IDs fail validation. Different prices with exactly the same timestamp
are omitted because their order cannot be established. Their raw rows remain.

## Identity and schema

Resolution requires a binary official-exchange ticker under an approved soccer
series, consistent event/market prefixes, and explicit rules containing the
two teams, competition and original fixture date. Team matching uses exact
normalized names and a small explicit alias table, never fuzzy similarity.
The date must agree with the event ticker and FIFA's UTC or New York calendar
date. This is date-level matching, not a claim of exact kickoff-time agreement.
Rescheduled fixtures with conflicting dates are excluded for review.

Home/draw/away also requires the rule explicitly covering 90 minutes plus
stoppage time and excluding extra time and penalties. The rule subject must
agree with the Yes outcome. Exactly one home, one draw and one away contract
must belong to the same event. Duplicate propositions, conflicting copies and
competing triplets are quarantined; liquidity does not choose the winner.

Props retain the full source rules and their binary Yes/No identity. Known
families get the existing family names; unfamiliar series retain a `kalshi_*`
family name. Player identities, handicaps and settlement rules are not silently
treated as interchangeable with Polymarket's props.

| Field | Kalshi meaning |
| --- | --- |
| `match_id`, `competition_id` | Same FIFA identifiers as Polymarket |
| `market_id` | `kalshi:<official ticker>` |
| `event_id`, `series_id` | Official Kalshi event and series tickers |
| `tokens[].token_id`, `prices.token_id` | Logical `kalshi:<ticker>:yes` or `:no` identifiers; not blockchain tokens |
| `venue` | `kalshi`, in both tables |
| `condition_id`, `polymarket_event_id` | Null; these are Polymarket-specific |
| `price`, `timestamp` | Probability in [0,1], UTC Unix seconds |
| `price_type` | `trade_close_1m` or `trade` |
| `bid`, `ask`, `volume` | Closing quotes and interval volume when provided; trade rows have no quote |
| `source_response_sha256` | Hash of the raw API response or Becker Parquet source |

The acquisition window is one hour before the earlier of actual/scheduled kickoff
through five minutes after the last FIFA event. A minute candle becomes available
at its period-end timestamp, and only when it reports a trade and positive volume.
`previous` is never used. No is the complement of Yes in the same binary contract;
it is not an independent extra trade observation. Saved bid/ask fields are not
historical order-book depth or a guarantee of an executable fill.
The three separately traded regulation contracts need not sum to one. Their
observed prices are stored and displayed unchanged; the simulator applies its
existing sum-consistency checks rather than silently renormalizing fills.

## DuckDB and visual replay

```sh
python3 -m app.replay import --dataset data/processed/fifa \
  --markets data/processed/fifa-kalshi --database data/kalshi-replay.duckdb
python3 -m app.replay verify --database data/kalshi-replay.duckdb
python3 -m app.replay list-matches --database data/kalshi-replay.duckdb --search Liverpool
python3 -m app.replay render --database data/kalshi-replay.duckdb \
  --match zhs8gg1hvcuqvhkk2itb54pg --output artifacts/fifa-replay/kalshi --preview
```

Remove `--preview` to generate video (requires `ffmpeg`), or use `--max-seconds 8`
for a short smoke test. The existing renderer displays regulation home/draw/away,
labels Kalshi as the source, uses strictly preceding prices no older than 90
seconds, and stops regulation odds at the second-half whistle. All other market
families remain queryable in DuckDB, but do not yet have prop-specific visuals
or trading tasks. Use separate DuckDB files for the two venues. `--replace`
explicitly rebuilds an existing database.

## Trading simulation

```sh
python3 -m app.replay export-records --database data/kalshi-replay.duckdb \
  --output data/processed/trade_env-kalshi/records.jsonl.gz
python3 -m app.trade_env build-tasks \
  --records data/processed/trade_env-kalshi/records.jsonl.gz \
  --output data/processed/trade_env-kalshi/tasks.jsonl.gz
python3 -m app.trade_env baselines \
  --tasks data/processed/trade_env-kalshi/tasks.jsonl.gz
```

The exporter produces existing `match_record/v1` records plus venue metadata and
an exclusion audit. It cuts prices at regulation end. The record format uses
integer seconds, so fractional trade times round up, never to an earlier time.
The latest observation in each resulting second is retained.

Kalshi tasks have a `kalshi:` ID prefix. Task building and match evaluation reject
mixed-venue inputs. Existing Polymarket records and task IDs remain compatible.
The existing [simulator](../../app/trade_env/README.md) applies its freshness,
probability-sum and delayed-fill checks, so not every mapped match/decision
necessarily becomes a task. It keeps the existing whole-match train/validation/
test splits.

Simulation uses the configurable generic fee, spread and delay assumptions.
These are **not a calibrated Kalshi fee schedule or evidence of actual fills**.
Use the same assumptions when comparing venues, or provide explicit `--set`
overrides and retain the generated configuration metadata.

## Validation

```sh
python3 -m unittest discover -s tests/kalshi -v
python3 -m unittest discover -s tests/replay -v
python3 -m unittest discover -s tests/trade_env -v
python3 scripts/check_tests.py
```

The independent dataset verifier checks Parquet hashes/schema/counts, both FIFA
source hashes, market and outcome identities, price foreign keys, unique
timestamps, acquisition windows, source-response hashes and token point counts.
It reports settlement disagreements against FIFA separately; settlement is never
used to select favorable contracts or determine the mapping.

Official API references: [historical markets](https://docs.kalshi.com/api-reference/historical/get-historical-markets),
[historical candles](https://docs.kalshi.com/api-reference/historical/get-historical-market-candlesticks),
[historical trades](https://docs.kalshi.com/api-reference/historical/get-historical-trades).
