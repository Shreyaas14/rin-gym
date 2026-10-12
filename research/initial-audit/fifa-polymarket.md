# FIFA event positions joined to Polymarket

Audited October 12, 2026. **The endpoints in the screenshot work. We can join located match events to preceding Polymarket prices using UTC. They do not expose continuous player/ball trajectories.**

## Acquired data

The screenshot's [calendar endpoint](https://api.fifa.com/api/v3/calendar/matches?language=en&count=500&idSeason=285023) returns 104 World Cup 2026 matches. All 104 timelines downloaded successfully: 9,675 event records, of which 6,905 have both `PositionX` and `PositionY`. Every event has a UTC timestamp in a plausible match window, and there are no timestamp reversals in the returned sequences. This is a structural quality check, not independent verification of live publication time.

The match catalog connects its `IdMatch` to the stats service via `Properties.IdIFES`. For example, FIFA match `400021504` maps to stats match `151667`; these are different namespaces, not interchangeable IDs.

| Endpoint family | Observed contents | Use |
|---|---|---|
| `/api/v3/calendar/matches` | Teams, scheduled UTC, scores, IDs | Match discovery and mapping; final scores are labels |
| `/api/v3/timelines/{IdMatch}` | `Timestamp`, period, match minute, event type, score, team/player IDs; coordinates on selected events | Event-level spatial and game-state replay |
| `/v1/stats/match/{IdIFES}/teams.json` | Team aggregate metric arrays | Descriptive match summaries; current historical response is not an as-of series |
| `/v1/stats/match/{IdIFES}/players.json` | Player aggregate metric arrays | Player summaries; not per-frame coordinates |

The two screenshot timelines contain shots, fouls, corners, substitutions, cards, goals, stoppages and period boundaries. They do not contain a complete pass/carry stream. Player IDs associated with events are not locations for every player. The two sample matches have raw coordinate values within 0–100 on each axis, but coordinate units, orientation and attacking-direction conventions have not been independently established. Preserve the raw fields rather than labeling them meters. `GoalGatePositionX/Y` is a separate field and must not be substituted for pitch position.

## Actual market joins

| Match | FIFA events | Located events | Polymarket contracts fetched | Price samples per contract |
|---|---:|---:|---:|---:|
| Uzbekistan–Colombia | 89 | 63 | Home win, draw, away win | 139 |
| Czechia–South Africa | 94 | 63 | Home win, draw, away win | 131 |

All six histories were retrieved with `fidelity=1`. The join contains **549 event/contract rows**. Every event has a strictly preceding sample for all three outcomes, and the maximum age of a matched price is **63.2 seconds**. Gaps between consecutive samples can reach 76 seconds, so one-minute fidelity is not an exact one-minute delivery guarantee. Only these two matches have completed Polymarket joins; the 104-match count applies to FIFA data, not verified market overlap.

The FIFA scheduled UTC agrees with Polymarket's `gameStartTime` for both matches. Uzbekistan–Colombia is June 18 at 02:00 UTC, while the market slug uses June 17 in US time. Do not join on a date string alone. The contracts cover regulation plus stoppage time.

Example from the saved data:

- Event: Patrik Schick shot, Czechia–South Africa, `2026-06-18T16:00:53.365Z`.
- Raw location: `x=92.94972`, `y=39.62576`.
- Latest preceding Czechia Yes price: `0.495` at `16:00:05Z`, 48.365 seconds earlier.
- Draw Yes price: `0.285` at `16:00:04Z`.
- South Africa Yes price: `0.215` at `16:00:04Z`.

These are sampled market prices at their own timestamps, not exact quotes at the shot timestamp. They also need not sum to exactly one, and the join does not normalize them.

## What “at any timestamp” can mean

For each recorded event, we have its location and UTC time. To look up market state at time `t`, use the latest sample preceding `t`, retaining its timestamp and age. The saved join uses a 90-second freshness flag; the threshold is explicit and adjustable. It never uses nearest-neighbor matching or interpolation that could pull a future price backward.

Between events, the observed position is **unknown**. A replay may show the last recorded event with its age, but it should not claim that the ball or player remained there. A full pitch state at arbitrary time requires tracking data or video-based reconstruction. Neither was found in the inspected endpoint responses. Uninspected FIFA services could have different capabilities; this audit does not establish their availability.

FIFA's UTC period starts are a substantial improvement over relying on scheduled kickoff plus match minute. In Uzbekistan–Colombia, the first half begins at `02:00:02.222Z`, ends at `02:50:09.541Z`, and the second half starts at `03:05:54.744Z`. A fixed 15-minute halftime assumption would misalign the second half.

For retrospective event-versus-price analysis, this is enough to begin. For live betting-policy evaluation, event occurrence time still does not prove when the feed first published the event or when an agent received it. Keep receipt times when collecting live data and test observation delays. Post-match aggregate stats must not become in-game features. Sampled price histories also cannot prove hypothetical trade fills or establish causal effects of individual events.

## Product implication

FIFA + Polymarket is now a concrete option for a **spatial event replay and market-response benchmark**: place an observed shot/corner/foul on a pitch and compare home/draw/away prices before and after it. It has better direct clock alignment than the StatsBomb Euro setup audited earlier. This new evidence strengthens the soccer option; the earlier NBA recommendation concerned richer game-state transitions in the sources examined then.

It is insufficient by itself for a possession-level soccer simulator with every pass or an environment that controls all players. Such a model requires additional event or tracking data and separate simulator validation.

## Saved artifacts and reproduction

- `fifa-events.csv`: all 9,675 FIFA events, with 6,905 coordinate pairs, across 104 matches.
- `fifa-polymarket-joined.csv`: the two verified match joins, with token IDs, price timestamps, ages and freshness flags.
- `fifa-timeline-census.json`: per-match FIFA coverage and timestamp checks.
- `fifa-polymarket-summary.json`: six-contract history and join metrics.
- `data/raw/sports/fifa_*`: raw responses; adjacent metadata files retain request URLs, retrieval times and SHA-256 hashes.

```sh
python3 -m pipelines.sports_data.fifa_census
python3 -m pipelines.sports_data.fifa_join
```

Existing public downloads are cached. Scripts require only Python's standard library.

Primary endpoints and market pages:

- [Uzbekistan–Colombia FIFA timeline](https://api.fifa.com/api/v3/timelines/400021504?language=en)
- [Czechia–South Africa FIFA timeline](https://api.fifa.com/api/v3/timelines/400021440?language=en)
- [Example team aggregate stats](https://fdh-api.fifa.com/v1/stats/match/151667/teams.json)
- [Example player aggregate stats](https://fdh-api.fifa.com/v1/stats/match/151667/players.json)
- [Uzbekistan–Colombia Polymarket](https://polymarket.com/sports/world-cup/fifwc-uzb-col-2026-06-17)
- [Czechia–South Africa Polymarket](https://polymarket.com/sports/world-cup/fifwc-cze-rsa-2026-06-18)
- [Polymarket price-history endpoint documentation](https://docs.polymarket.com/api-reference/markets/get-prices-history)


Repository layout: generated tables and summaries are in `data/processed/sports/`; downloaded source payloads are cached in the ignored `data/raw/sports/` directory. Pipeline commands above run from the repository root.
