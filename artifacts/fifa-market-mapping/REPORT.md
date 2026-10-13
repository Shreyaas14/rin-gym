# Full FIFA → Polymarket mapping audit

Completed 2026-10-13 UTC against the local Premier League 2025/26 and World Cup
2026 FIFA snapshot. All 484 fixtures have a complete regulation home/draw/away
mapping and usable historical odds.

| Competition | FIFA matches mapped | Linked contracts | Final priced contracts |
| --- | ---: | ---: | ---: |
| Premier League 2025/26 | 380 / 380 | 6,992 | 6,657 |
| World Cup 2026 | 104 / 104 | 36,243 | 36,089 |
| Total | 484 / 484 | 43,235 | 42,746 |

The final all-family dataset contains 31 market families, 85,492 independently
fetched outcome histories, and 16,035,880 price samples. Both outcome histories
are available for every retained contract. Eight token histories contain only
pregame/postgame samples within the requested window; these are explicitly
identified by zero `in_game_points`.

489 candidate contracts are excluded from the final priced tables. The provenance
policy quarantines 333 before price ingestion: 298 missing deployed condition/token
identities, 5 archived listings, 22 duplicate exact-score propositions around the
rescheduled Aston Villa–Liverpool fixture, and 8 incomplete registry identities.
Among the 42,902 certified contracts, another 156 have no history (312 tokens
confirmed empty through individual requests). These are audit findings, not
claims that the listings are fraudulent. All source records and reasons remain
in the raw cache and audit ledgers.

The catalog also excludes 281 non-fixture events, including season awards,
relegation, announcer mentions, and entertainment markets.

The two-table replay view retains all 484 matches and 40,848 original events.
39,718 events have a strictly preceding price no older than 90 seconds for all
three H/D/A outcomes. All original FIFA columns are unchanged. Extra-time and
shootout events remain in the replay but do not receive regulation-market odds.
All 484 market settlement triplets agree with FIFA regulation results; this is a
diagnostic, not the identity-matching criterion.

## Method

See [the entity-resolution guide](../../docs/pipelines/fifa-market-resolution.md)
for aliases, uniqueness checks, contract semantics, exact schedule exceptions,
source provenance, schemas, and exclusion policy. Every retained contract now requires the approved league series, provider approval, non-custom origin, validated resolution-source hosts, and a matching token-registry record for both outcomes. Duplicate condition/token/proposition identities are quarantined. See [contract quarantine](contract-quarantine.json).

The original seed join manually specified two World Cup slugs. The subsequent
forecast mapper expanded to World Cup group-stage fixtures using team names,
kickoff times, three moneyline questions, and backward price joins. This extension
adds the whole EPL season and knockout fixtures, recognizes legacy EPL contracts
with missing market types, handles explicit kickoff discrepancies, preserves
FIFA's composite event IDs, and catalogs all fixture-linked market families.

Player props are resolved to fixtures and outcome tokens. Their player IDs and
statistical settlement definitions are not yet resolved to FIFA event entities,
and no unsupported supervised labels are manufactured.

## Local files

- Replay view: [`matches.parquet`](../../data/processed/fifa-markets/snapshots/20261013T030206-a098517b/matches.parquet), [`events.parquet`](../../data/processed/fifa-markets/snapshots/20261013T030206-a098517b/events.parquet).
- All-family data: [`markets.parquet`](../../data/processed/fifa-market-history/snapshots/20261013T030305-50201b7d/markets.parquet), [`prices.parquet`](../../data/processed/fifa-market-history/snapshots/20261013T030305-50201b7d/prices.parquet).
- Exclusion ledger: [`market-resolution.json`](../../data/processed/fifa-market-history/snapshots/20261013T030305-50201b7d/market-resolution.json).
- [Machine-readable summary](summary.json) and [verification result](verification.json).

These Parquet snapshots and raw payloads are local, Git-ignored artifacts. The
market and price tables together occupy 59,887,023 bytes. Histories cover one hour
before kickoff through five minutes after the final FIFA timeline event, at
one-minute fidelity; they do not represent the entire market trading lifetime.

## Validation

57 unit/integration tests passed. Ruff and `git diff --check` passed. The full
snapshot verifier checked all 16,035,880 rows for token identity, timestamp order,
price range, sample counts, and provenance; it also verified the distinct raw
market response hashes recorded in `verification.json` and every H/D/A backward join. Separately, FIFA raw
verification passed for all 486 source responses.

```sh
.venv/bin/python -m pipelines.fifa.markets --workers 8
.venv/bin/python -m pipelines.fifa.markets --offline --workers 8
.venv/bin/python -m pipelines.fifa.market_history
.venv/bin/python -m pipelines.fifa.verify_markets --report artifacts/fifa-market-mapping/verification.json
.venv/bin/python -m pipelines.fifa verify --cache data/raw/fifa
.venv/bin/python -m unittest discover -s tests/fifa -q
ruff check pipelines/fifa tests/fifa
git diff --check
```
