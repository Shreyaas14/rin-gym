# Basketball versus soccer: data overlap audit

Audit date: October 12, 2026. Public data downloaded and inspected; no paid data or trading credentials used.

**Recommendation: NBA + ESPN play-by-play + Kalshi historical candles for the two-day game-state / betting-policy prototype.** Both sports have real overlap. NBA offers substantially more frequent state transitions than EPL commentary, with UTC timestamps and historical bid/ask summaries. **EPL has the cleanest timestamp ordering and slightly better price coverage in this sample.** Choose StatsBomb soccer if spatial simulation is the main product, accepting additional clock-alignment work.

This is a 60-game feasibility audit, not an exhaustive estimate of coverage across either sport or the Becker trade archive. It establishes downloadable overlap and identifies replay hazards; it does not establish profitable trading or an accurate learned simulator.

## Measured results

| Cohort | Matched games with event data and historical prices | Event records | Price coverage | Clock alignment |
|---|---:|---:|---|---|
| NBA playoffs, May 12–June 13, 2026 (US dates) | 20/20 | 10,127; about 506/game | 3,126 / 3,160 minute endpoints in observed event spans, **98.9%** | All records have plausible UTC times; 85 adjacent timestamp reversals |
| EPL, May 15–24, 2026 | 20/20, including one explicitly recovered reschedule | 1,896; about 95/game | 2,342 / 2,344 minute endpoints, **99.9%** | All records have plausible UTC times; one reversal of one second |
| Euro 2024, June 25–July 14 | 20/20 | 75,997; about 3,800/game | 2,400 price samples in the 20 scheduled two-hour windows; 4,800 in four-hour windows | StatsBomb events have period-relative clocks, not per-event UTC |

These event counts measure different things: NBA includes shots, rebounds, fouls and substitutions; EPL is a commentary stream; StatsBomb includes passes, receipts, carries, pressure and other on-ball events. Counts are not independent training examples or a direct completeness score.

The Kalshi denominator counts minute endpoints between the earliest and latest plausible event wallclock, inclusive. It includes game breaks and may include postgame corrections. Missing candles remain missing. Euro's two-hour window is only a coverage screen based on scheduled kickoff, **not proof of precise in-game alignment**, and is not directly comparable to the Kalshi percentage. The largest observed gap between consecutive Euro price samples was 67 seconds.

## What was actually checked

- Downloaded the first 1,000 archived contracts from each Kalshi NBA and EPL series, then selected the first 20 distinct game events in each returned list, retaining one contract per game. The catalog pages contain 500 NBA and 334 EPL event IDs, respectively, and both have further pages. Only the selected 20 per sport received event/history validation.
- Selected the latest 20 matches in the StatsBomb Euro 2024 match catalog and matched them to Polymarket event titles, normalizing team names. This includes all 15 knockout matches and five group-stage games.
- Downloaded event streams and prices for all 60 games. Retained source market metadata, rules, IDs, selected outcomes, raw responses, source URLs and SHA-256 hashes.
- Checked timestamp presence, plausibility within scheduled start minus 10 minutes to start plus six hours, timestamp reversals, minute endpoint coverage, bid/ask availability, positive-volume bars and settlement agreement.
- All 40 selected Kalshi contracts settle consistently with ESPN final results. Every returned in-window candle has closing bid and ask values; none has a crossed closing quote. Positive volume appears in 3,074 NBA and 2,234 EPL candles.
- NBA's final play score agrees with ESPN's final scoreboard in 20/20 games. Reconstructing Euro goals, including own goals and excluding shootout goals, agrees with StatsBomb's final score in 20/20. All 20 selected Polymarket token outcomes agree when regulation and match-winner rules are handled separately.

One selected contract per game proves that a historical market exists; it does not verify every winner/draw/total/handicap contract. Several selected EPL contracts are draw contracts. A full three-way probability model needs the other outcomes too.

## Findings that change implementation

### 1. NBA has useful clocks, but they are not clean publication timestamps

All 20 NBA streams contain some UTC reversals relative to the supplied play sequence: 85 total, of which nine exceed 60 seconds. The largest is 449 seconds: an end-of-game review is timestamped after the following end-of-quarter record. This is consistent with revisions or delayed annotation, but the audit does not identify the cause.

Do not sort solely by wallclock and assume that reconstructs what an agent knew. Preserve sequence order, flag affected intervals, and exclude them from the clean benchmark until resolved. A monotonic adjustment is an explicit modeling assumption, not recovery of true availability time.

One game, ESPN `401859966`, has 22 missing minute endpoints, including a ten-minute consecutive gap. Another game's six apparent missing endpoints are after market close because a late review extends the event span. Build missingness and market-closed flags; do not assume forward-filled prices are tradable.

### 2. Reschedules defeat ticker-date joins

`KXEPLGAME-26MAY17AVLLFC` refers to Aston Villa–Liverpool originally scheduled for May 17, but ESPN event `740957` occurred May 15 at 19:00 UTC. The market closed May 15 at 21:00 UTC, and its rules explicitly retain the original scheduled date. The audit uses a documented override. Without it, EPL would appear to have 19/20 overlap.

Join on teams, competition, actual schedule and contract rules; keep original and actual dates separately.

### 3. Soccer's rich event data and its clean UTC commentary are different options

StatsBomb provides locations on 75,274 of the 75,997 sampled records and detailed passing/carry sequences. This is the strongest input here for a pitch-grid demonstration. It is event data, not continuous player/ball tracking, and one historical trajectory cannot establish the truth of simulated counterfactual actions.

Its timestamps reset by period. Scheduled kickoff plus match minute cannot account for actual kickoff delay, halftime duration, stoppage time or extra-time breaks. A reliable market replay needs independently verified UTC anchors for each period, with uncertainty recorded.

The schedule itself also needed validation: StatsBomb lists Czech Republic–Turkey on June 26 with kickoff `22:00`, while ESPN and the market description agree on 19:00 UTC. Treating the StatsBomb field as UTC initially returned only postgame prices for this match. All 20 schedules were therefore cross-checked against ESPN, this window was corrected and re-downloaded, and the original response was retained. Every corrected Euro series has changing prices. This illustrates why row counts alone are insufficient evidence of overlap.

Recent EPL ESPN commentary has plausible UTC timestamps and selected field positions, but does not contain a complete passing sequence. An older ESPN Euro final spot-check is actively unsafe for UTC alignment: its kickoff record says `2024-07-15T16:19:05Z`, while the game was scheduled for July 14 at 19:00 UTC. Do not generalize the recent EPL timestamp result to all historical soccer feeds.

### 4. A match winner is not always the contract winner

Most sampled Euro knockout contracts settle on regulation time plus stoppage time. Spain beat Germany after extra time, but the sampled “Will Spain win?” regulation contract settled No. England–Slovakia and England–Switzerland have the same distinction. The final uses Spain/England match-winner outcomes rather than Yes/No regulation outcomes.

Store contract semantics and token outcome explicitly. Final-score-only labels would produce incorrect training targets.

### 5. Price history supports evaluation, not guaranteed fills

Kalshi returns one-minute trade-price and bid/ask OHLC summaries. Polymarket's sampled history provides timestamped token prices. Neither source used here provides a full historical order book with queue position or an actual fill for a hypothetical order.

At decision time, use only already completed candles. Separate forecasting scores from simulated trading results; model fees, delayed execution, spreads, stale prices and unavailable markets. Report P&L as assumption-dependent simulation. Capturing a live feed with receipt timestamps and order-book updates would allow stronger validation later.

## Recommended two-day scope and eval

Build a **minute-level NBA replay benchmark**: score, quarter, game clock, recent events, selected market price/quote, bankroll and position. The first demo can visualize one match; the audit sample supplies 20 candidate episodes, subject to excluding timing anomalies and missing intervals. This is a seed benchmark, not enough evidence for robust RL profitability.

1. Expand to earlier games before training a policy. Split chronologically by whole game; never put possessions from the same game in both training and test sets. Keep the final test games untouched during model selection.
2. Test probability forecasts with Brier score, log loss and calibration against a market-only baseline and a simple score/time model. Compute uncertainty by resampling whole games, not treating thousands of plays as independent outcomes.
3. Run timing sensitivity at, for example, 30/60/120-second assumed observation delays, and publish a separate result excluding timestamp-reversal intervals. Such stress tests do not prove the original live availability time.
4. For a simulator, evaluate held-out next-event likelihood and score/time transition distributions separately from the betting policy. A replay environment cannot validate alternative on-court actions that never occurred.
5. Evaluate policy value only under explicit fill assumptions, with no-trade and simple fixed-rule baselines. Show results with fees and conservative delays. Favor a reproducible, honest eval over claiming an edge from 20 games.

If minimizing cleaning is more important than transition richness, use **EPL + ESPN + Kalshi**. If a spatial soccer simulator is the central idea, use **Euro 2024 + StatsBomb + Polymarket**, and make clock alignment an explicit task before evaluating in-play decisions.

## Access, scope and reproducibility

- Public ESPN endpoints worked for this audit; they are not a versioned access guarantee. A direct NBA CDN play-by-play probe returned HTTP 403 in this environment.
- The Becker compressed trade archive was not downloaded or inspected row by row. Direct venue history endpoints supplied the price evidence here. Its raw trades may add granularity, but they still would not establish hypothetical order-book fills.
- This comparison confounds league, season and source, and deliberately samples prominent games. It is not a randomized population coverage estimate. Other soccer leagues, basketball leagues and all archive pages remain untested.
- Downloadable access is not a conclusion about redistribution rights. Retain source attribution and check applicable data terms before publishing a dataset.

Run offline metrics from the saved evidence:

```sh
python3 -m pipelines.sports_data.analyze
```

Run the cached acquisition pipeline and metrics:

```sh
python3 -m pipelines.sports_data.run_audit
```

Python standard library only. The existing sample manifests freeze this cohort. Deleting them and refreshing catalogs can change the selected cohort. Exploratory one-off probes are omitted from this initial import; `pipelines/sports_data/run_audit.py` is the focused entry point. Raw files have adjacent `.meta.json` records with URLs and hashes; later downloads also have retrieval timestamps. All downloads belong to this audit session.

Artifacts: `audit-results.csv` contains one row per game; `audit-results.json` contains the same metrics; `data/raw/sports/audit_manifest.json` contains the cross-source joins and full selected market metadata.

Primary source documentation:

- [Kalshi historical candlesticks](https://docs.kalshi.com/api-reference/historical/get-historical-market-candlesticks)
- [Kalshi historical markets](https://docs.kalshi.com/api-reference/historical/get-historical-markets)
- [Polymarket price history](https://docs.polymarket.com/api-reference/markets/get-prices-history)
- [StatsBomb open data](https://github.com/hudl/open-data)
- [Sample ESPN NBA event](https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event=401859967)
- [Rescheduled EPL event](https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/summary?event=740957)
- [Becker prediction-market repository](https://github.com/jon-becker/prediction-market-analysis)


Repository layout: generated tables and summaries are in `data/processed/sports/`; downloaded source payloads are cached in the ignored `data/raw/sports/` directory. Pipeline commands above run from the repository root.
