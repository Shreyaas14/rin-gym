# Polymarket versus Kalshi: data quality and backfill decision

Audited 13 October 2026 against the frozen datasets used by PR #8. **Keep the broad
Kalshi prop backfill stopped. The current home/draw/away simulator has enough
coverage to use both venues, with Polymarket the stronger Premier League source.
Improve Kalshi's representation of already-cached quotes before downloading more
of the same candle history.** For second-by-second event reaction research, a
separate, targeted trade/quote history project is still warranted for both venues.

## Comparable coverage

These results use regulation home/draw/away markets only, the existing five
simulator decision points, a 90-second sample-age limit, a 5-percentage-point
triplet-sum tolerance, a 60-second execution delay and a 120-second fill wait.
Each accepted decision produces four position-specific tasks. Those four tasks
are not four independent market observations. All percentages below use each
venue's mapped-match denominator unless stated otherwise.

| Measurement | Polymarket | Kalshi |
| --- | ---: | ---: |
| Matched Premier League games | 380 / 380 | 375 / 380 |
| Matched World Cup games | 104 / 104 | 104 / 104 |
| Games producing at least one simulation task | 484 | 468 |
| Games passing all five decisions | 433 | 250 |
| Accepted decision points | 2,362 / 2,420 (97.6%) | 1,933 / 2,395 (80.7%) |
| Accepted Premier League decision points | 1,843 / 1,900 (97.0%) | 1,415 / 1,875 (75.5%) |
| Accepted World Cup decision points | 519 / 520 (99.8%) | 518 / 520 (99.6%) |
| Simulation tasks | 9,448 | 7,732 |
| HDA Yes samples during regulation window | 170,294 | 156,082 |
| Fresh, coherent triplet on one-minute grid | 98.7% | 85.8% |
| Median internal sample spacing | 60 seconds | 60 seconds |
| Invalid price values / duplicate token timestamps | 0 / 0 | 0 / 0 |
| HDA settlement disagreements with FIFA | 0 / 1,452 contracts | 0 / 1,437 contracts |

On exactly the same 479 games, Polymarket passes 2,338 decisions and Kalshi 1,933.
There are 1,906 decisions accepted by both, 432 only by Polymarket, 27 only by
Kalshi and 30 by neither. Use those 1,906 common decisions (7,624 position tasks
per venue) for paired strategy comparisons. Comparing aggregate rewards on the
full, differently filtered datasets would confound venue performance and sample
selection. Kalshi's failures are concentrated early in the Premier League season:
August passes 55/150 decisions; September 86/150; April 137/150.

The one-minute grid runs from actual first-half start through regulation end,
including halftime. It tests as-of sample age and triplet sums, not execution.
A sampled price timestamp does not establish that a recent trade or executable
quote existed. This distinction matters: Polymarket's source is sampled price
history, while the current Kalshi normalization retains completed minute trade
closes. The [Polymarket endpoint](https://docs.polymarket.com/api-reference/markets/get-prices-history)
exposes timestamp/price samples; [Kalshi candles](https://docs.kalshi.com/api-reference/historical/get-historical-market-candlesticks)
separately expose trade prices, bids, asks, volume and open interest.

## What causes rejection?

| First failing check at a decision | Polymarket | Kalshi |
| --- | ---: | ---: |
| Stale/missing shown price | 2 | 237 |
| Shown triplet sum outside tolerance | 31 | 83 |
| No delayed fill observation | 0 | 70 |
| Fill triplet sum outside tolerance | 25 | 72 |
| Total rejected decisions | 58 | 462 |

These are mutually exclusive first-failure counts, not counts of every defect
present at a decision. The five unmapped Kalshi fixtures are outside this table.
Eleven mapped Kalshi games have no accepted decision; all are Premier League
fixtures. Per-match failures and candidates are in [audit.json](audit.json) and
[kalshi-matches.json](kalshi-matches.json).

## Download completeness versus normalization

The raw audit hash-checked all 1,437 candle responses associated with final Kalshi
contracts and compared every positive-volume trade close, through regulation end,
with the exported price series. **Zero downloaded trade closes are missing, and
zero extra closes were introduced.** This demonstrates faithful conversion of the
cached responses; it does not independently prove the exchange returned every
historical trade or candle.

Within the regulation window, the cache contains 167,284 contract-minute candles:
156,082 have trade closes and 11,202 have no trade close, all with zero volume.
There are no positive-volume candles with a missing close. Repeating the same
historical download is unlikely to create trades in those minutes.

Of the zero-volume minutes, 6,643 already contain a bid and ask satisfying the
diagnostic filter `0 < bid <= ask < 1` and a spread of at most $0.10. The current
pipeline skips the whole candle when no trade occurred, so those quote-only
observations are retained in raw files but absent from the normalized price table.
This is a representation limitation, not evidence of a missing download.

A separate quote-midpoint diagnostic, retaining the simulator's other checks,
produces:

| Diagnostic result | Decisions |
| --- | ---: |
| Both original trade-close and quote-midpoint views pass | 1,717 |
| Only quote view passes | 282 |
| Only original trade view passes | 216 |
| Neither passes | 180 |

The quote view passes 1,999 decisions versus 1,933 originally: a net increase of
66 decisions, not 282. Premier League improves from 1,415 to 1,567; World Cup falls
from 518 to 432 under this diagnostic policy, which excludes quotes at 0 or 1. The
result depends on the quote policy. Do not silently swap sources or select a
source after looking at future fills/rewards. Preserve separately labeled
`trade_close_1m` and quote observations, measure spread and availability, and
validate an explicit policy before changing the default simulator input.

Candles provide no executable size in this dataset, and a candle timestamp does
not give the last quote-change timestamp. Midpoint-based hypothetical fills remain
a simulation assumption. The existing Polymarket price table has no historical
bid/ask or depth fields either. Lifetime contract volume and settlement-time open
interest are not substitutes for liquidity known at a historical decision.

## Granularity and event alignment

The existing Germany–Paraguay Kalshi trade sample has 17,578 regulation Yes
observations after the simulator's conservative integer-second collapse, compared
with 354 minute closes. Its median spacing is one second, versus 60 seconds.
However, both representations already pass all five decisions and produce the
same number of tasks (20). More rows are useful for event timing, but do not
necessarily recover simulation coverage. This one liquid World Cup fixture is
not representative evidence for illiquid Premier League matches.

Across 2,058 decision instants where both venues provide fresh, coherent shown
prices, the median absolute per-outcome difference is 0.5 percentage points, the
95th percentile is 2.5 points, and the maximum is 47.5 points. For example,
Arsenal–Bournemouth at `h2+30` differs by 47.5 points on the away outcome; FIFA
records a goal 71 seconds before that decision. Polymarket's stored away price
is 0.115 at decision minus 37 seconds, then 0.58 at plus 16 seconds; Kalshi's is
0.59 at minus 38 seconds. That is a timing/source-behavior issue to investigate,
not grounds to choose a contract using the price agreement or final score.

Fresh/coherent triplets 60 seconds after regulation score increases are available
for 1,132/1,342 observations on Polymarket (84.4%) and 896/1,329 on Kalshi (67.4%).
These are availability measurements, not measured exchange response latency:
FIFA event timestamps can themselves lag the real-world event, and independent
market samples can reflect different moments. Minute histories are insufficient
for confident second-by-second causal reaction or execution claims.

## Broader markets

Polymarket's final catalog contains 42,746 markets in 31 families, versus Kalshi's
1,437 finalized HDA markets. The stopped Kalshi catalog mapped 41,630 contracts,
but that count is not verified price coverage. Props cannot be compared fairly
until market definitions, periods, player/stat identities and evaluation targets
are supported by the simulation.

Polymarket is not uniformly complete across props: four retained markets (eight
outcome tokens) have no in-game samples, and another twelve outcome tokens have
only one to four in-game samples. Flag these as unsuitable for in-game replay;
do not treat fixture linkage as proof of usable price coverage. They do not affect
the complete HDA set. Flat histories are measured in [integrity.json](integrity.json)
but are not automatically defects: a near-certain or inactive market can have a
constant sampled price. The previous ingestion also excluded 156 contracts with
empty outcome histories; retrying those is lower priority than a defined prop
simulation target.

## Recommended next work and stopping conditions

1. **Current five-decision HDA replay/simulation: no broad backfill.** Use the
   verified Polymarket set and Kalshi's eligible subset. Keep the paired-decision
   cohort for comparisons and report the excluded Premier League games.
2. **Kalshi quote retention: reprocess the existing raw cache.** Add a distinct
   quote representation without overwriting trade observations. Evaluate it on
   the 166 fixtures where the diagnostic recovers at least one decision, with
   explicit spread, timing and pricing rules. No new downloads are required for
   this experiment. Promotion requires integrity checks, no look-ahead and a
   predeclared policy that does not cherry-pick future successful fills.
3. **Investigate seven fixture/history issues selectively.** The five existing
   Kalshi date disagreements require schedule/contract evidence before remapping.
   Polymarket's two stale decisions occur in Arsenal–Sunderland
   (`3paqoync1x6ffgogsb85v15p0`) and Leeds United–Burnley
   (`4w69cq7gxy6jlk8hwflij7cic`); the latter has a 1,979-second internal sample gap.
   A bounded source re-query or alternate history source is justified for these
   cases, not a complete season re-download. If the source still has no history,
   retain the exclusion and stop retrying.
4. **For finer event replay, run a bounded, stratified trade-history pilot.**
   Include sparse early-season Premier League fixtures, triplet-sum failures and
   matches near goals, rather than extrapolating from the liquid World Cup sample.
   Compare recovered decisions, goal-window timing and API/storage cost. Expand
   only if it demonstrates the granularity or usable coverage required by the
   chosen simulation; raw row count alone is not a success criterion.
5. **Resume props only when a prop simulation target is implemented.** Download
   the required families and keep market-rule differences explicit. Completing
   every catalog contract does not repair current HDA gaps.

No backfill was restarted, no canonical dataset was replaced, and no relaxed
freshness or price-sum thresholds were applied to the published dataset.

## Reproduction and evidence

Machine-readable results: [comparison](audit.json), [raw candle audit](kalshi-raw-audit.json),
[integrity and family coverage](integrity.json), [Polymarket fixtures](polymarket-matches.json)
and [Kalshi fixtures](kalshi-matches.json). Input record hashes, source manifest
hashes and the simulator configuration are recorded in the comparison. Scripts
perform local reads only and take explicit input paths.

Run from the repository root, supplying the existing exported record files:

```sh
python artifacts/market-quality/audit.py \
  --polymarket-records "$POLYMARKET_RECORDS" \
  --kalshi-records "$KALSHI_RECORDS" \
  --trades-records "$KALSHI_TRADES_RECORDS"
python artifacts/market-quality/audit_raw.py \
  --snapshot "$KALSHI_SNAPSHOT" \
  --records "$KALSHI_RECORDS" \
  --cache data/raw/kalshi
python artifacts/market-quality/audit_integrity.py
ruff check artifacts/market-quality/*.py
git diff --check
```

The scripts default to `data/replay.duckdb` and `data/kalshi-replay.duckdb`; the
comparison and integrity scripts accept explicit database overrides. Each script
also accepts an output path. Existing record exports were used for this run;
records can be regenerated with `python -m app.replay export-records` against the
corresponding DuckDB database. Database lineage should match the recorded manifest
hashes before comparing results. The committed audit results describe these frozen inputs; rerun the scripts after changing a dataset or pricing policy.
