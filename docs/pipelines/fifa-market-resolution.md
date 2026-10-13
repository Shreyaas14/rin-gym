# FIFA → Polymarket entity resolution

The pipeline resolves a FIFA fixture to Polymarket events, their contracts, and the
contracts' outcome tokens. It does not assume that the two providers share IDs.
It preserves the complete FIFA snapshot and all downloaded market responses.
Only validated mappings and available prices enter the derived datasets.

## Run

From the repository root, after FIFA ingestion:

```sh
.venv/bin/python -m pipelines.fifa.markets
.venv/bin/python -m pipelines.fifa.market_history
```

Both commands support `--offline`, `--refresh`, `--cache`, `--dataset`, `--output`,
and `--workers`. The first accepts `--max-age` (90 seconds by default). Omit
`--refresh` to resume from verified cached responses. `--offline` reproduces a run
without network access. Each successful run publishes an immutable snapshot
through `current.json`; previous snapshots remain intact. Transport failures
retain the candidate snapshot and cache but do not replace the current pointer.

## Contract provenance and quarantine

Fixture matching alone is insufficient. Before a contract enters either final
view, require the expected Polymarket sports series (`10188` for EPL, `11433`
for World Cup), `approved=true`, `cyom=false` on the market/event, an unarchived
market, a deployed condition/question ID, two distinct decimal outcome tokens,
and an enabled order book. Any supplied resolution-source URL must use HTTPS on
the configured FIFA or Premier League hostname; a lookalike hostname fails.
Missing policy fields fail closed rather than implicitly granting approval.
Historical inactivity/closure alone is not evidence of fraud.

Cross-check both tokens using Polymarket's [token registry](https://docs.polymarket.com/api-reference/markets/look-up-tokens).
Require matching condition ID, event ID/slug, contract question/slug, outcome name,
CLOB index, opposite token, v1 CTF module, and negative-risk identity. The CLOB
index is distinct from the on-chain outcome index. Unknown or inconsistent
identities are quarantined. Store the policy version and registry-response hashes
in the Parquet rows; offline verification rechecks them against raw responses.
The history command refuses an uncertified or hash-mismatched catalog.

Quarantine **all** conflicting owners of a condition, token, or identical fixture
proposition. Never select one based on volume, response order, liquidity, or
settlement price. Multiple distinct props for the same match are retained with
separate IDs; duplicate H/D/A outcomes cannot become an arbitrary triplet.
Conflicting copies of the same market ID stop publication. Failed registry
requests also leave the current snapshot untouched.

For H/D/A, require the primary fixture event, the question's date, the exact
regulation clause, and the rule text's named winner/draw resolving to Yes.
Explicit extra-time inclusion or an inconsistent subject is rejected.

These controls verify a consistent provider-approved listing, not that a contract
is fraud-free or immune to oracle disputes. Both APIs belong to Polymarket. No
creator allowlist is inferred from popularity, and no market is labeled a scam
solely for failing these checks. Provider-specific stat definitions and player
identity still need separate validation before creating prop training labels.

## How identity is established

1. **Competition and catalog scope.** Fetch all pages of the EPL (`306`) and World
   Cup (`102232`) Gamma tags within the FIFA fixture date span plus a seven-day
   margin. Include open and closed events and auxiliary market groups. Restrict
   event slugs to the corresponding league prefix, since a league tag can contain
   incorrectly tagged events. Season-wide awards, entertainment markets, and
   titles without two recognized fixture teams are not fixture mappings.
2. **Team identities.** Case-fold, normalize accents, remove punctuation and whole
   `FC`/`AFC` words, then apply explicit aliases: for example `West Ham` ↔ `West
   Ham United`, `Wolves` ↔ `Wolverhampton Wanderers`, `DR Congo` ↔ `Congo DR`.
   Assert that each normalized name identifies only one FIFA team ID in its
   competition. No fuzzy or substring match is accepted. FIFA home/away roles are
   authoritative even if the market's display order is reversed.
3. **Fixture identity.** Both teams, competition, and UTC kickoff must agree.
   The usual tolerance is 15 minutes. A Polymarket event must resolve to exactly
   one FIFA match, and a market cannot belong to multiple matches. Each contract
   must independently pass its start-time check. A date embedded in a slug is not
   used as a UTC timestamp; North American local dates can be the previous day.
4. **Contract identity.** Store the Gamma event ID, market ID, complete question
   and rules, provider market type, source hash, and FIFA match ID. Separate
   auxiliary events such as player props, corners, and exact scores can correctly
   belong to the same fixture. Duplicate H/D/A outcomes are rejected rather than
   choosing one by volume, settlement, or response order.
5. **Outcome/token identity.** Zip the provider's ordered `outcomes` and
   `clobTokenIds` arrays. A `Yes` token is not interchangeable with `No`, nor `Over`
   with `Under`. Fetch both sides independently; do not manufacture one price as
   `1 - other_price`. Batch responses are joined by token ID, never array order.

The resolver records every catalog event's disposition in
`catalog-resolution.json` and every FIFA match's inclusion/exclusion in
`match-resolution.json`. Explicit time exceptions preserve their evidence and
signed offset in the manifest:

- FIFA `400021520`, Mexico–Ecuador, maps to
  `fifwc-mex-ecu-2026-06-30`: FIFA completed-match time is 02:00 UTC; the market
  schedule is 01:00 UTC. The [FIFA preview](https://www.fifa.com/en/tournaments/mens/worldcup/canadamexicousa2026/articles/mexico-ecuador-live-stream-team-news-tickets)
  identifies the same Round of 32 fixture and originally scheduled 19:00 Mexico
  City kickoff. This is an explicitly recorded schedule discrepancy, not a
  general widening of the time tolerance.
- FIFA `400021531`, Mexico–England, maps to
  `fifwc-mex-eng-2026-07-05`: FIFA is one hour later than the market schedule.
  [England's match report](https://www.englandfootball.com/england/mens-senior-team/fixtures-results/2025-26/World-Cup/mexico-england-fifa-world-cup-round-of-16-monday-6-july-2026-match-centre)
  documents the one-hour weather delay.

The exceptions require the exact FIFA match ID, market fixture prefix, and
signed offset. They never shift either provider's event/price timestamps.

## Home/draw/away replay view

`pipelines.fifa.markets` retains the original two-table replay interface:

| File | Grain and additions |
| --- | --- |
| `matches.parquet` | One retained FIFA match; original fields plus `prediction_markets`, a three-element list identifying home/draw/away contracts and source hashes. |
| `events.parquet` | Every original timeline event of each retained match; original fields plus `market_odds` and `odds_usable`. |

Select exact `Will TEAM win on YYYY-MM-DD?` or fixture draw questions with binary
`Yes`/`No` outcomes. Accept either `moneyline` or a missing legacy type, and verify
regulation-time rules. Legacy EPL draw contracts may omit the duration clause;
these are accepted only alongside two explicit regulation win contracts from the
same event. World Cup advancement contracts include different settlement rules
and are never treated as regulation wins.

Use the first-half start and second-half end markers, not the last event or final
knockout score. Extra time and penalties therefore do not change the H/D/A target.
Published market settlements are compared to FIFA regulation scores as an audit
only; they are not identity features or a criterion for selecting winners.

For an event at time `t`, choose the latest price with timestamp **strictly less
than `t`**. No same-time/future sample, nearest-neighbor match, interpolation, or
renormalization is allowed. Store each sample's timestamp and age. `odds_usable`
is true only for a regulation event within the game window where all three
prices are at most 90 seconds old. Keep other replay events with an explicit
false flag; do not damage the timeline by dropping them. Matches without a
unique complete triplet, valid timeline, or any simultaneously fresh in-game
observation are omitted from this final view, with the reason in the audit.

The existing `pipelines.fifa.replay.load_match` and `load_timeline` functions can
read the derived two-table directory directly. `load_match` exposes the new odds
fields. `load_timeline` intentionally returns the original FIFA event interface.

## All market types

`pipelines.fifa.market_history` reads the broader `fixture-contracts.parquet`
certified fixture-contract catalog, independently of whether a fixture qualifies for the H/D/A view.
It produces two additional normalized sidecar tables; these do not replace or
multiply the soccer replay tables:

| File | Grain and contents |
| --- | --- |
| `markets.parquet` | One priced fixture-linked contract. Complete original market JSON, question, family, FIFA match ID, provider IDs, and per-outcome token/history status. |
| `prices.parquet` | One `(token_id, timestamp)` sample with market/match IDs, outcome index/name, price, in-game flag, and raw response hash. |

Fetch at one-minute fidelity from one hour before kickoff through five minutes
after the last FIFA timeline event, including extra time and shootouts. This is
a game-window history, not the contract's entire trading lifetime. Use the
[public batch-history endpoint](https://docs.polymarket.com/api-reference/markets/get-batch-prices-history)
with at most 20 tokens per request. Responses and POST request bodies are cached
with content hashes. If a token is omitted by the batch endpoint, check its individual-history endpoint before exclusion. Validate timestamps, unique ordering, finite prices in
`[0, 1]`, requested time bounds, and token ownership.

A contract with no token mapping or no usable history on either side is omitted
from the final priced table. Partial-outcome histories remain explicit via
`all_outcomes_have_history` and each token's status. Empty responses and excluded
contracts are retained in the raw cache and `market-resolution.json`.

**Fixture linkage is not a supervised label for a prop.** Player IDs are not yet
resolved to FIFA entities; corners, shots, assists, and other statistics can have
provider-specific definitions. The full contract rules are preserved, but the
pipeline does not claim that the FIFA event feed can label every prop. Use the
market family, rules, period, line, participant, and token outcome before deriving
such labels. A regulation goal total and an extra-time-inclusive winner remain
distinct contracts even when they share a match.

FIFA event occurrence times are not measured first-publication times. These
historical joins therefore do not establish a tradable real-time information
advantage. Prices from history endpoints are sampled values, not executable
bid/ask quotes.

## Local outputs and checks

- FIFA source: `data/processed/fifa/current.json`.
- H/D/A replay view and certified fixture-contract catalog:
  `data/processed/fifa-markets/current.json`.
- All-family histories: `data/processed/fifa-market-history/current.json`.
- Immutable source response bodies and URL/request indexes:
  `data/raw/polymarket/`.

```sh
.venv/bin/python -m unittest discover -s tests/fifa -v
ruff check pipelines/fifa tests/fifa
.venv/bin/python -m pipelines.fifa verify --cache data/raw/fifa
.venv/bin/python -m pipelines.fifa.verify_markets
```

Tests cover aliases and collision rejection, reversed display order, UTC versus
slug dates, exact schedule overrides, ambiguous/duplicate mappings, legacy EPL
rules, advancement separation, backward price joins, stale samples, missing
histories, batch outcome ordering, cache request-body identity, raw preservation,
filtered final publication, and retaining the current pointer on HTTP failure.
