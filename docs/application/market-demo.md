# Interactive replay market

The original replay now has an interactive browser companion with a picker for the 10 historical games with the highest event density, ranked from 484 candidates. It preserves the cream background, blue/orange teams, striped pitch, observed action markers and match feed, and adds an executable paper order book, agent valuations, market history, a trade ticket and account balances. Games retain anonymous labels from Game 001 through Game 484, with Team A/Team B and stable player aliases instead of real identities.

## Start

From the `rin-gym` checkout, with Python 3.11 or later:

```sh
python3 -m app.market --port 8765
```

Open [the local demo](http://127.0.0.1:8765). No package installation or external request is needed for the default mode. The server binds to loopback; each browser cookie gets its own private exchange. Keep the server running while using the page.

Choose a game from the ranked picker. Density is recorded events per minute of active regulation play: events inside each half’s start/end boundaries divided by the combined duration of both halves, including stoppage time and excluding halftime, pre-match activity, extra time and shootouts. Boundary events count. The picker shows rank, density and event count; only the top 10 can be selected, and the densest game opens by default. Every selection starts paused, with fresh 1,000-dollar paper accounts and a new order book, and preserves the current live/local mode. Old-game commands cannot change the new session. Buy YES to back a Team A regulation win or buy NO to back a draw/Team B win. A limit order may rest until the next agent refresh; choose immediate-or-cancel to take available liquidity and cancel the remainder. Closing a position can fill partially when the book has insufficient depth.

Agents refresh every 15 match seconds and on recorded regulation events, with no trading rounds during halftime. Each agent posts up to four bid and four ask levels, spaced two cents apart, with more shares farther from its valuation. The opening book normally has 16 orders and 104 shares per side. Quotes and taker orders remain limited by available collateral and the 100-share position limit; actual trades require crossing another trader’s price. The browser displays the best ten orders per side.

## Anonymous dataset

The server automatically reads the portable catalog in `data/processed/market-demo/`. This workspace's catalog contains 484 real games exported from the existing replay database. The server verifies and scores all candidates, then exposes only the top 10 through the picker and game-selection API. Scores are computed from verified events, not trusted from manifest metadata. Ties use active event count, total event count, then anonymous ID. Use `--dataset /absolute/path/to/catalog` to choose another exported catalog. If no catalog exists and no explicit path is supplied, the server offers one anonymous bundled example; an explicitly missing catalog fails with an error.

Create a catalog from an existing replay database:

```sh
python3 -m app.market.import_dataset \
  --database /absolute/path/to/replay.duckdb \
  --output data/processed/market-demo
```

Only the importer requires `duckdb`; it opens a Polymarket replay database read-only. Kalshi or mixed-venue databases are rejected explicitly because this version labels its reference as Polymarket. The browser/server runtime continues to use only the standard library and exported JSON. Import into a fresh output directory; an existing published manifest is protected from overwrite. Copying that catalog directory preserves the candidate set and its top-10 selection without copying the original database.

The importer removes team/country/player names, real fixture dates, competition labels, raw commentary and source match/player IDs. Descriptions are generated from allowlisted event types and stable per-game aliases such as Player A01. Both substitution participants retain aliases. Every timeline and reference series is shifted to a common synthetic origin; the browser displays elapsed time. Public catalog metadata contains only anonymous IDs, labels, rank, event counts, active minutes, density and reference availability, with no final scores. This hides explicit identities; it does not claim that a distinctive historical event sequence is impossible to recognize.

## What runs locally

The default **Local simulation** runs two deterministic Poisson continuation baselines. Anchor emphasizes score and time; Pulse adjusts scoring rates using recent attacking events. They are labelled as local strategies, not Jev. Their numerical forecasts are illustrative and have not been calibrated to establish an advantage.

Both agents post collateral-backed bids and asks and may take the other agent's orders. There is no artificial fill liquidity. Human trades change the same ledger, positions and quotes. The YES book includes prices, remaining sizes, cumulative depth and owners. NO prices map to the complementary side of the same book.

The displayed midpoint is the average best bid/ask, not an executable price or an average of model estimates. Actual trades appear separately. Only valid observed FIFA coordinates appear on the pitch. Each exported game has its own verified Polymarket home-win reference; no Kalshi history is fabricated. Stale observations create chart gaps and cannot affect orders or fills.

Each account starts with 1,000 paper dollars. Cash is measured in cents internally; negative YES positions represent NO exposure. Collateral and resting-order reservations reduce available buying power. Fees are zero. The sum of account cash is always 3,000 paper dollars and the sum of signed positions is zero until settlement. Indicative P&L uses the current midpoint, or a visibly stale last mark when no two-sided book exists.

Each market settles at its own explicit second-half whistle, including stoppage time. Extra time, shootouts and administrative match-end events are excluded. YES pays one dollar if Team A leads at that boundary; otherwise NO pays one dollar. Final P&Ls sum to zero.

## Live Jev

Start the server from a terminal where `TYPESAFE_API_KEY` is already exported. Do not paste credentials into the UI, repository or artifacts. The page then enables **Live Jev** in the mode selector. Selecting a mode resets the paper session.

The adapter uses the official [TypeSafe endpoint and typed Noul schema](https://docs.typesafe.ai/api), with a pinned model default of `jev-1.13.0`. Change the pinned version with `--model` when needed. Both strategies receive the same anonymous event-prefix snapshot, with different valuation instructions. Fixture identity, provider metadata, current historical odds, future events and settlement labels are excluded.

Each decision round runs the two requests concurrently with a shared ten-second application deadline and ten-second socket timeout; there are no automatic retries. Failed agents have no orders for that round. If both fail, replay pauses and the user can retry. A retry preserves the round identity and healthy agent/human orders. Late responses cannot place orders. No failure silently switches to local estimates.

Live integration is covered by mocked transport tests and a real four-request pilot completed October 13, 2026 after the user authorized loading the TypeSafe credential into server memory. All four pinned-model responses were valid. Opening home-win estimates were 56% for Anchor and 52% for Pulse; the next round returned 55% and 52%. The agents generated a two-sided book, a human paper order filled, and the session audit passed. Evidence is in `artifacts/market-demo/live-pilot/`. This checks integration, not forecast quality or full-match liquidity. The faster cadence produces roughly 500 valuation rounds per complete game (516 for the default Game 032), with two model requests per uncached live round; retries/reset patterns and caching determine actual call count. The server does not apply a dollar spending cap, so live mode should be run deliberately.

## Recorded valuations and audit export

The footer's **Export session** link downloads the session journal: anonymous model observations and answers, source hash, original commands, exchange actions, fills, reservations reconstructed from orders, final state, and provider receipts where live calls occurred. This history contains paper activity and no authorization headers.

After a complete live run, save [the recording endpoint](http://127.0.0.1:8765/api/recording) from the same browser session as `valuations.json`. Restart with:

```sh
python3 -m app.market --port 8765 --recorded /absolute/path/to/valuations.json
```

Select **Recorded Jev**. The recording must use schema `market-valuations/v1` and match both the selected game's anonymous payload hash and pinned model. Values are indexed by anonymous snapshot hash and agent. A missing snapshot produces an explicit agent failure; it is never interpolated or replaced with historical prices. Orders and fills are still calculated interactively, so different human trades produce different results. Select Local simulation or Live Jev before changing games when a game-specific recording is active. Recordings created before the anonymous-payload policy changed are incompatible and fail explicitly.

## Verification

```sh
python3 -m unittest discover -s tests/market -v
node --check app/market/static/app.js
python3 -m compileall -q app/market
```

The test suite covers price/time priority, partial/multilevel matching, post-only/IOC, self-trade prevention, collateral and pending exposure, conservation, both settlement outcomes, human YES/NO trades, close/cancel/reset, failed valuations, anonymous as-of inputs, recorded model compatibility, mocked live transport and isolated local HTTP sessions. HTTP tests need permission to bind a loopback socket in restricted environments.

Verify a saved journal independently:

```sh
python3 -m app.market.audit /absolute/path/to/journal.json
```

The verifier regenerates engine activity from the journal, compares actual fills and the final book/ledger, and fails on inconsistencies. The original implementation's browser-exported run is saved in `artifacts/market-demo/browser-session.json`: 438 orders, 37 fills, settled, and 300,000 cents of total paper cash. Its independent result is in `artifacts/market-demo/verification.json`.

The anonymous catalog update initially passed 59 automated tests, Ruff, JavaScript syntax checking and Python compilation. All 484 games were compared against their source regulation scores, timing, event counts and valid coordinates. All 484 also completed an offline human-fill/settlement/journal-audit pass with conserved balances; the 16 second-caution games were audited again after dismissal-label handling changed. Results are saved in `artifacts/market-demo/anonymous-catalog/`. Browser checks confirmed game switching, anonymous player text, restored balances and mobile layout.

Implementation adds `app/market/` and leaves the original video renderer and source payloads intact. Data/server/valuation code uses Python's standard library; frontend assets have no CDN dependency. Sessions and per-game valuation caches are in memory, with up to 32 browser sessions per process. Export before restarting the server or changing games to preserve a journal. Shared public hosting and durable multi-user storage are outside this demo.

The top-10 density update passed 67 automated tests and adds coverage for active-time normalization, halftime/pre-match/extra-time exclusion, deterministic ties, manifest-metric spoofing, and selection restrictions. The complete anonymous ranking is saved in `artifacts/market-demo/anonymous-catalog/event-density-ranking.json`.
