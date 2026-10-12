# Data sources

| Source | Endpoint / repository | What it provides |
| --- | --- | --- |
| FIFA calendar | `https://api.fifa.com/api/v3/calendar/matches?language=en&count=500&idSeason=285023` | Match identity, scheduled UTC, teams and final scores |
| FIFA timeline | `https://api.fifa.com/api/v3/timelines/{IdMatch}?language=en` | UTC event times, event types, score fields and selected pitch coordinates |
| FIFA stats | `https://fdh-api.fifa.com/v1/stats/match/{IdIFES}/teams.json` | Aggregate team statistics, not historical as-of states |
| ESPN | `https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/summary?event={id}` | NBA play-by-play and soccer commentary |
| StatsBomb | [Open data](https://github.com/hudl/open-data) | Detailed soccer event sequences with period-relative timestamps |
| Kalshi | [Historical candles](https://docs.kalshi.com/api-reference/historical/get-historical-market-candlesticks) | Minute trade-price and bid/ask summaries |
| Polymarket | [Price history](https://docs.polymarket.com/api-reference/markets/get-prices-history) | Sampled token prices; Gamma event metadata identifies contracts and rules |

FIFA `IdMatch` and stats `IdIFES` are different namespaces; the calendar's `Properties.IdIFES` maps between them. Use UTC start times and team identities to join events to markets, not date strings alone. Contracts can settle on regulation time rather than the eventual knockout winner.

Preserve raw coordinate values. No continuous trajectories or calibrated stadium geometry have been established. Post-match aggregate stats and final scores are labels or summaries, not historical features available during play.

`data/manifests/sports/source-index.json` preserves URLs and hashes from the original audit session. Newly downloaded raw files have adjacent metadata. The source index includes exploratory requests and is provenance, not a claim that every response became a modeling input. Public access does not imply unrestricted redistribution rights.

See [sports overlap](../../research/initial-audit/sports-overlap.md) and [FIFA findings](../../research/initial-audit/fifa-polymarket.md) for measured coverage and known timestamp issues.
