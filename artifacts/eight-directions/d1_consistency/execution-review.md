# Independent execution-core review

Read-only review of `/Users/alexkan/Desktop/frontiers/rin-gym-execution-aware/app/trade_env/execution.py` and its tests on 2026-10-13. No implementation or original D1 artifacts changed. No paid calls, fixture/outcome reads, enrollment, or holdout access. Findings concern synthetic acceptance fixtures; they do not establish execution realism or profitability.

1. **[P1] Resting maker orders outlive the declared signal.** `execution.py:497` checks `signal_valid_s` only when submitting, while `execution.py:532–540` forwards the original order lifetime unchanged. The engine then accepts maker prints until that order's cancellation deadline (`execution.py:337–338`). With signal lifetime 5s, order lifetime 10s, latency 1s and cancellation latency 0s, a maker buy fills at timestamp 7. This is not a cancellation race: the signal expired two seconds earlier without any cancellation request. Clamp the maker's local cancellation deadline to the signal deadline, while retaining explicit cancellation-latency races. Also test the inclusive/exclusive boundary and ensure a follow-up taker order cannot receive a fresh signal lifetime. Existing `test_fee_aware_sell_quotes_and_signal_expiry` checks only the hybrid fallback and misses fills of the first resting order.

2. **[P2] A known fee transition during submission latency bypasses the after-fee margin.** `execution.py:507–516` guards using the submission-time fee, but `execution.py:521–540` encodes only a raw-price limit. The arrival fill uses the updated schedule at `execution.py:266–271`. Reproduction uses a schedule known at timestamp 0: no fees through timestamp 1, then 5% taker fees. At unchanged ask 0.51, probability 0.535, 1s latency and 0.02 uncertainty margin, the policy admits an estimated edge of 0.025, but fills with actual after-fee edge 0.012505. Both schedules have `recorded_at=0`, so this is avoidable using already-known information, unlike unforeseen fees or price movement. Preserve the edge bound across known fee transitions when deriving the submitted limit, or explicitly expose this gap in the policy contract. Add a test with a known valid fee change, distinct from the existing test that merely blocks an unknown arrival-time schedule.

3. **[P2] Zero-depth levels are used as executable quotes.** `Book.__post_init__` explicitly accepts quantity zero, but `Book.bid`/`Book.ask` choose prices without filtering positive depth. Consequently `expected_cost` at `execution.py:447–452` and the policy limit at `execution.py:521` can be based on nonexistent liquidity. A valid book with bid 0.39, asks `(0.40, 0)` and `(0.42, 10)`, probability 0.70 and unchanged arrival book yields a taker limit 0.40 and no fill, although the available 0.42 ask has 0.26782 after-fee edge. Filter zero-depth levels for quote selection or reject such snapshots. Test both buy and sell, plus all-zero active sides.

Existing checks that passed: explicit fee/rebate provenance; unknown schedules fail closed; arrival-time post-only rejection; actual bid/ask cash accounting without a second spread debit; partial inventory oversell protection. An additional read-only fixture produced a taker fill followed by a maker fill on the same partially marketable order, sold 0.5 of its 2-share inventory and settled the remaining 1.5. Both `net = gross − fees + rebates` and `net = midpoint − spread_slippage − fees + rebates` held exactly to floating-point tolerance. No accounting bug was found in those paths.

Useful missing regression tests beyond the three findings: a quote moves across a post-only limit between submission and arrival; one partially marketable order receives both maker and taker fills; a partial close then switch retains the unsold quantity and forbids an unintended second full position; and fills just before/at/after explicit cancellation acknowledgement. The evaluation worker owns the residual-inventory and historical as-of-book fixes; this review does not duplicate those findings.

Verification command (15 tests passed):

```sh
cd /Users/alexkan/Desktop/frontiers/rin-gym-execution-aware
PYTHONPATH=. /Users/alexkan/Desktop/frontiers/rin-gym-forecast-only/.venv/bin/python -m unittest discover -s tests/trade_env -p test_execution.py -v
```

Minimal signal-expiry reproduction:

```python
from app.trade_env.execution import *
fee = FeeSchedule("m", 0, None, 0, 0, .05, "historical_verified", "review fixture")
books = [Book(t, ((.49, 10),), ((.51, 10),)) for t in (0, 1)]
r = execute_policy(
    ExecutionEngine([fee]), Order("a", "m", "buy", 1, .8, 0, expiry_s=10),
    .7, books, [Trade(7, .49, 1, "sell")],
    ExecutionPolicy("maker", signal_valid_s=5), scenario="observed", queue_ahead=0,
)
assert r["fills"][0]["timestamp"] == 7  # accepted after the five-second signal deadline
```

Line references identify the reviewed snapshot and may move as the implementation owner fixes it.
