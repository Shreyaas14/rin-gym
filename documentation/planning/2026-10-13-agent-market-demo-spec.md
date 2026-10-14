---
title: Soccer replay prediction market demo
type: spec
status: approved
owner: alexkan
created: 2026-10-13
tags: [demo, soccer, prediction-markets, paper-trading]
---

# Soccer replay prediction market demo

## Summary

Replay a historical soccer match inside a prediction market operated by two AI traders. As the match unfolds, each trader estimates the chance of a home win and places orders. A user joins with paper money and trades in the same order book. Executed orders and available quotes create the demo's prices; Polymarket and Kalshi histories appear as optional reference lines.

The central demo moment: a goal changes the game, the agents reprice their orders, the user trades against them, and the market moves. At the end of regulation, the market settles and all three participants see their results.

**Approved scope:** one binary market, chosen by the user on October 13, 2026. The user subsequently authorized implementation, explicitly retaining the original replay UI and adding visible order-book data. This document does not claim that the agents have a forecasting advantage. See the [implementation plan](2026-10-13-agent-market-demo-plan.md) for environment-driven presentation choices.

## Context

The user reports that match-event data and historical Polymarket/Kalshi odds are already available. The main `rin-gym` checkout currently contains an offline FIFA event replay renderer in `app/replay/render.py`, a small Czechia–South Africa fixture, and historical Polymarket overlays. It renders images/video rather than accepting interactive orders. See [the current application guide](../../docs/application/README.md).

Related checkouts contain more general replay/data adapters and a Jev evaluation adapter. Their availability is useful, but they are not assumed to be integrated into this checkout. Implementation must select the relevant changes or reuse their interfaces explicitly. The existing historical-price trading simulator must not supply fills for this new exchange.

TypeSafe documents Jev as a model that evaluates typed questions against supplied state. Its Noul primitive returns a number between zero and one for a binary question. That fits a narrow valuation interface, while ordinary application code handles orders, risk limits, matching, and settlement. These capabilities do not establish soccer-specific forecast quality. Sources: [introduction](https://docs.typesafe.ai/introduction), [Noul](https://docs.typesafe.ai/primitives/noul).

## Decisions

### 1. Product promise and first session

Proposed title: **Trade the Match**.

Proposed pitch: “Two AI traders. One historical match. Your paper money. Can you beat the market?”

The first version is one curated match, two named agents, and one human participant in a private session. Another browser session receives its own exchange, balances, and order history.

1. Open the demo and see the teams, market rules, paper balance, and two agent profiles.
2. Start the replay. Watch recorded match events and the agents' quotes evolve.
3. Pause at any point to inspect the book, agent valuations, and recent trades.
4. Buy YES or NO, place a limit order, or close a position. See the counterparty and actual fill price.
5. Resume through a major event and observe the resulting orders and executions.
6. Reach the regulation whistle and see settlement, final paper P&L, and a trade-by-trade recap.

A complete presentation should take roughly three to five minutes, with optional pauses. The fixture must be selected for an understandable event sequence and verified settlement data. Prefer a match with both venue reference histories; missing one venue must not block trading.

### 2. Contract and market scope

**Question:** “Will [home team] win in regulation time?”

- YES pays one paper dollar if the home team leads at the end of the second half, including stoppage time.
- NO pays one paper dollar for a draw or away win at that boundary.
- Extra time and penalty shootouts do not count.
- Quotes range from 1¢ to 99¢ in 1¢ increments; quantities are whole shares. Settlement is exactly 0¢ or 100¢.
- Every account begins with 1,000 paper dollars and no position. Fees are zero for the demo.
- No real-money deposits, wallets, venue order routing, or shared public exchange are included.

The selected match must have an explicit regulation-end event and a verified score at that event. A final score after extra time is insufficient. Missing or contradictory terminal data blocks fixture selection; a runtime terminal-data error suspends trading and reports the error instead of guessing a settlement.

### 3. How the exchange creates prices

Use a central limit order book with price priority and then arrival-sequence priority. Every execution needs an actual opposite order with sufficient remaining quantity and collateral. There is no hidden liquidity provider or fill at a historical reference price.

Maintain one canonical YES book. Buying NO at price `r` is economically equivalent to selling YES at `1 - r`. Transform NO orders into this book so YES and NO users share liquidity. Display the user's original outcome and price in confirmations.

- A resting limit order is a maker order.
- An incoming order that crosses resting liquidity is a taker order; an ordinary limit order can have both roles across different fills.
- A buy executes against the lowest asks at or below its limit. A sell executes against the highest bids at or above its limit.
- The execution price is the resting order's price. Orders can fill partially and across multiple price levels.
- Post-only orders that would cross are rejected. Immediate-or-cancel orders execute the available eligible quantity and cancel the remainder.
- Prevent an account from trading against itself by canceling the incoming conflicting remainder. Do not count these cancellations as trades.
- Reserve funds when accepting an order; release the unused reservation on fill, cancellation, or expiry. Cancel/replace is atomic and loses queue priority.

The screen must distinguish three numbers:

| Value | Definition | Purpose |
| --- | --- | --- |
| Market indication | `(best YES bid + best YES ask) / 2` | Main probability chart when both sides exist |
| Last trade | Price and timestamp of the latest actual execution | Evidence of where shares changed hands |
| Agent valuation | Each agent's own estimate of a home win | Explains disagreement and trading decisions |

The midpoint is an indicative price, not an executable offer or a mathematically proven probability. The chart may move when quotes change without a trade. Render trade markers separately. If either side is missing, show “No two-sided quote,” break the midpoint line, and retain the last trade with its age. Do not substitute the mean agent belief.

Derive NO quotes from the same book: `NO bid = 1 - YES ask` and `NO ask = 1 - YES bid`. A quick-buy ticket previews the actual depth-weighted execution price and maximum spend. It submits an immediate-or-cancel limit order with a user-visible worst acceptable price. Insufficient depth produces a partial fill or no fill; it never creates shares at the midpoint.

**Illustrative mechanics, not a forecast:** Agent A values YES at 60% and posts 58¢ bid / 62¢ ask. Agent B values YES at 68% and buys ten shares at A's resting 62¢ ask. The last trade becomes 62¢. The current midpoint still depends on the remaining book. A user's limit buy for ten shares at 61¢ waits for a qualifying seller; the system does not fill it at an average of 60% and 68%.

### 4. Two model-backed traders

Start with two independent Jev-backed agents. They may use the same model version with different valuation instructions and trading settings. Label them as two strategies using Jev; do not imply they are separately trained models.

| Agent | Valuation emphasis | Initial trading settings |
| --- | --- | --- |
| Anchor | Score, period, time remaining, and pre-match strength; considers all supplied events | 2¢ half-spread, ten-share quotes, 2¢ minimum taker edge |
| Pulse | Same factual snapshot, with greater emphasis on recent attacking events and disciplinary changes | 3¢ half-spread, five-share quotes, 1¢ minimum taker edge |

Both may make and take. Neither is assigned a permanently bullish or bearish view. Different instructions may still produce similar estimates; disagreement and executions must be checked in a pilot rather than manufactured with random price offsets.

The model's narrow job is to answer: “Given only this state, what is the probability that the home team wins at the end of regulation?” Use Noul as the initial adapter candidate, retain its raw output, and call it an **agent estimate**. Whether its values are useful sports probabilities remains an empirical question.

The default trader consists of a model valuation plus a deterministic order policy. It does not ask the model to invent exchange instructions. For agent `i`, let `p_i` be its valuation and `q_i` its signed YES position. Start with:

```text
position_limit = 100 shares
inventory_adjustment = 0.03 * q_i / position_limit
quote_center = clamp(p_i - inventory_adjustment, 0.01, 0.99)
bid = floor_to_cent(quote_center - half_spread_i)
ask = ceil_to_cent(quote_center + half_spread_i)
```

Omit quotes outside 1¢–99¢ or with unavailable risk capacity. Do not clamp both quotes into the same price or allow an agent's bid to meet its own ask. Place at most one bid and one ask per agent. Positive YES inventory lowers the center to encourage reducing exposure; negative inventory raises it.

For taking, compare the executable opposing price to the same inventory-adjusted valuation. Buy when `quote_center - ask >= minimum_edge`; sell when `bid - quote_center >= minimum_edge`. Use an immediate-or-cancel limit at the edge boundary. Recompute allowed size after every fill and enforce both collateral and position limits. Each agent gets one bounded action batch per decision round; it does not continuously trade in an unbounded loop.

TypeSafe's returned Choice/Score confidence summarizes the answer distribution; Noul has no separate confidence field. A 50% win estimate is a legitimate sporting forecast and must not automatically disable the trader. Use explicit data-quality checks and validation, rather than treating closeness to 50% as missing knowledge. See [TypeSafe confidence documentation](https://docs.typesafe.ai/confidence).

### 5. Information available to the agents

Every valuation uses a versioned as-of snapshot containing only:

- Period, elapsed time, observed score, cards, substitutions, and recent event history.
- Rolling shot/attacking-event counts where their feed definitions are understood.
- xG or event locations only when genuinely supplied by the selected source.
- A frozen pre-match strength prior when available, identical for both agents and recorded with provenance.

No final-result fields, future events, full-match totals, future odds, future chart segments, or hidden settlement labels enter the request. Runtime code reconstructs state from the revealed event prefix rather than passing the raw match object through.

Use neutral Home/Away labels and omit exact fixture date, player names, venue, and competition identifiers from model requests to reduce recognition of a known historical result. This reduces leakage risk without proving the model has no prior knowledge. The human-facing display may retain real team identities.

Historical in-play odds are comparison-only in this version. They do not enter valuation, order pricing, matching, or P&L marks. If a pre-match prior comes from a venue, disclose that initialization separately; do not claim the agent is independent of market information.

Agent status cards show current valuation, position, active bid/ask, last action, and data age. Any short explanation is generated from actual policy inputs, such as “Bought: ask was 4¢ below valuation.” Do not present template text as a model-generated reasoning trace.

### 6. Replay, ordering, and interaction

Keep three clocks separate: source event time, replay cursor, and browser wall time. Compress quiet stretches for presentation while preserving every event's source timestamp and order. Do not invent intermediate ball or player trajectories.

Run a decision round at kickoff, every 60 seconds of active regulation play, and immediately after a recorded goal, red card, or penalty award. Coalesce triggers at the same timestamp. Only trigger on event types actually present in the feed.

Each round follows this order:

1. Suspend order entry and finish applying the timestamp's event group in deterministic source order. Stop immediately if regulation has ended.
2. Expire all remaining orders from the previous round, including human limits. The ticket explicitly states “Good until next agent refresh.” Positions persist.
3. Build one common match snapshot. Obtain both model valuations against it. Responses carry the session epoch and snapshot ID.
4. Apply each agent's order batch against the evolving book, alternating first priority between A and B on successive rounds. Each batch first attempts eligible taker actions and then posts remaining permitted maker quotes. The second batch can take the first agent's fresh orders.
5. Publish trades, book, account updates, and agent cards atomically, then reopen order entry and advance the replay.

API response arrival time does not determine agent priority. A late or mismatched response is discarded. A new round cannot begin until the previous one finishes or times out.

Human orders use the same sequencer, matching, and risk checks. After a human fill, run one deterministic agent reaction batch using the last valid valuations and updated positions; this supports responsive quotes without new forecast calls. Do not recursively trigger more reaction batches from those resulting fills.

Pause freezes match-time progression and scheduled valuation rounds. Human orders and the bounded agent reaction still work at that frozen state. Play speeds are presentation controls, never changes to matching rules. The user may inspect earlier events, but trading from a rewound state requires a fresh session with reset balances, positions, and history.

Final-whistle handling closes order entry before any further order is accepted, cancels resting orders, settles exactly once, and ignores pending model responses. Reset creates a new session epoch; responses from the prior session cannot mutate it.

### 7. Paper accounts and settlement

Use a fully collateralized signed-position ledger for the canonical YES contract:

```text
C = cash ledger balance, including sale proceeds
q = signed YES position; negative q is equivalent NO exposure
required_position_collateral = max(0, -q) paper dollars
equity_at_indicative_price_m = C + q * m

buy n YES at p:  C := C - n*p; q := q + n
sell n YES at p: C := C + n*p; q := q - n

settlement outcome y in {0,1}: C := C + q*y; q := 0
```

One dollar of collateral backs each short YES share. A sale at `p` contributes `p` in proceeds, so opening that share consumes `1 - p` of previously available cash. This is also the economic cost of buying NO at `1 - p`. The UI shows available buying power separately from cash, collateral, and open-order reservations; short-sale proceeds are not freely spendable.

Reserve open orders conservatively. In addition to existing position collateral, reserve `n*p` for each outstanding canonical buy and `n*(1-p)` for each outstanding canonical sell. This may reserve more than strictly necessary for offsetting orders, but prevents reuse of collateral. Check the possible long and short exposure from all live orders against the account's position limit independently. Reservation updates and fills must be atomic.

Examples, starting from 1,000 paper dollars:

- Buy ten YES at 62¢: cash becomes 993.80, position becomes +10. Final equity is 1,003.80 if YES wins or 993.80 if NO wins.
- Buy ten NO at 38¢, represented as selling ten YES at 62¢: cash becomes 1,006.20 with ten dollars reserved, so buying power is 996.20. Final equity is 1,006.20 if NO wins or 996.20 if YES wins.

All trades transfer equal cash and opposite positions between two accounts. The sum of signed positions stays zero, and total account cash stays 3,000 paper dollars before and after settlement. Use integer cents for cash and price calculations. No account may end negative.

Before settlement, label P&L marked to midpoint as indicative. If the book lacks a current two-sided quote, show the last mark with its age and mark P&L stale. Show executable close proceeds separately when sufficient depth exists. At settlement, report final equity minus the 1,000-dollar starting balance; the three participants' final P&Ls must sum to zero.

### 8. Demo screen

| Area | Content |
| --- | --- |
| Header | Teams, score, regulation clock, replay status, paper-trading label |
| Main stage | Pitch/event replay and concise event feed |
| Price chart | Synthetic midpoint, actual trade markers, optional venue histories |
| Order book | YES/NO view of shared depth, spread, recent executions |
| Trade ticket | Buy YES/NO, close exposure, quick order or limit, size and cost preview |
| Agent cards | Strategy, valuation, active orders, position, latest policy action |
| Account panel | Buying power, collateral, open orders, exposure, indicative/final P&L |
| Controls | Play/pause, speed, step to next decision, reset session |

The historical lines are explicitly labelled by venue and price type. Use only observations available at the displayed cursor, keep original prices, show age, and break stale/missing segments. Initially hide observations older than 90 seconds. Minute candles become available at their period end. Compare only contracts with matching home-team and regulation-settlement rules.

Venue data availability must never create synthetic depth. Turning reference lines on or off must leave model requests, quotes, fills, and account history unchanged.

### 9. System boundaries and demo reliability

Proposed components are logical boundaries, not a mandated framework choice:

```text
Match data -> As-of replay state -> Agent valuation adapters -> Order policies
                                                                  |
Human trade ticket -----------------------------------------------+
                                                                  v
                                                    Matching engine + ledger
                                                                  |
                                                        Session event stream
                                                                  v
                                                              Browser UI

Venue odds -> As-of reference adapter -------------------------> Chart only
Regulation outcome -> Settlement service ----------------------> Ledger
```

The server owns the replay cursor, sequence numbers, book, balances, and settlement. The browser submits commands and renders authoritative snapshots. Commands carry idempotency IDs and session epochs so retries cannot double-submit orders. A reconnect receives the current snapshot and subsequent events; it does not replay old button clicks.

Persist a session journal with source/config hashes, snapshots, raw validated model answers, model identity, order commands, reservations, fills, cancellations, and settlement. Record actual outcomes even when there are no trades. Replaying the journal with the same human commands must reproduce the ledger and book exactly.

Because default valuations depend only on match state, they can be cached per agent, snapshot, model, and instruction version. Provide two clearly labelled presentation modes:

- **Recorded model valuations:** prerecorded real model responses; orders and fills still run interactively against the user's actual actions.
- **Live model valuations:** fresh responses, with replay held during each decision round.

Never replay precomputed fills into an interactive session. A cached valuation is reusable; cached future orders that assume the original user positions are not.

For live mode, set a ten-second valuation deadline per round with at most one bounded retry within that deadline. On one agent's failure, leave that agent unquoted for the round and identify it as unavailable; the other may continue. If both fail, pause the replay with a retry option. Do not silently replace failed model estimates with historical odds or a heuristic. Switching to recorded valuations requires an explicit user action and a visible mode change.

Pin a resolved model version in the session manifest rather than depending solely on a moving alias. No paid calls are needed to review this spec; validate API latency, response behavior, and spend in a separately scoped implementation pilot.

## Acceptance criteria

1. A user completes a session through regulation settlement, including at least one actual paper fill with an identified agent counterparty.
2. A curated pilot demonstrates real agent-to-agent executions and at least one material quote change following a major match event. If the agents never cross or fail to maintain useful depth, report the pilot failure and revise the strategies; never force fills.
3. Identical crossing orders receive identical treatment for human and agent accounts. Automated tests cover price/time priority, maker-price execution, multiple levels, partial fills, IOC/post-only behavior, cancellation, and self-trade prevention.
4. Accounting tests cover long and NO-equivalent positions, pending-order collateral, both settlement outcomes, position limits, reset, duplicate commands, and exactly-once settlement. Cash and signed-position conservation hold after every journal entry.
5. Prefix tests verify that changing future events, venue histories, or hidden final labels cannot change an earlier model request or synthetic execution. UI inspection confirms no future chart segment is visible.
6. Paused trading, a user fill, a model timeout, and reset during an in-flight response all produce valid books and balances. No old quote survives a refresh boundary or final whistle.
7. Recorded-valuation mode completes without a live API dependency after assets load. Reusing the same recorded valuations with different human trades can produce different books and P&Ls.
8. The ticket reports cost/limit and available depth before submission, then shows actual fills and any unfilled quantity. An empty book produces no fill.
9. The final screen reconciles all fills to settlement and displays a zero sum of final P&Ls across the three accounts.
10. Review evidence includes screenshots of the opening book, post-goal refresh, user fill, no-liquidity state, and settlement, plus the actual verification commands run during implementation. This spec does not claim those checks have run.

## Scope after the first demo

Defer three-outcome markets, real-money execution, public multiplayer, tournament leaderboards, trained policy comparisons, calibrated profitability claims, fee schedules, and support for arbitrary historical matches. First prove that one match, two useful agents, one shared book, and one human session form an understandable and reliable experience.

## Review points

The user authorized implementation with the original replay UI and visible order-book data. Home-win in regulation, comparison-only venue odds, full collateral, zero fees and refresh-boundary expiry are implemented. Live and compatible recorded Jev modes are supported. Local simulation is the offline default. After the user authorized loading the TypeSafe credential, a real four-request pilot verified provider acceptance, valid probability responses, two-sided quotes, a human paper fill and a passing accounting audit. Evidence is in `artifacts/market-demo/live-pilot/`. Full-match liquidity and forecast quality remain unvalidated.

## Related

- [Existing replay application](../../docs/application/README.md)
- [Existing forecasting research directions](../../research/forecasting/research-directions.md)
- [TypeSafe introduction](https://docs.typesafe.ai/introduction)
- [TypeSafe Noul primitive](https://docs.typesafe.ai/primitives/noul)
- [TypeSafe confidence semantics](https://docs.typesafe.ai/confidence)

## Origin

Requested by the user in the October 13, 2026 Codex conversation: replace a passive historical-odds display with a model-driven soccer replay market that supports human paper trading.
