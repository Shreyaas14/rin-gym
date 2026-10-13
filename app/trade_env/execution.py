"""Offline liquidity-aware execution. Historical mids are not order books.

The old costs/reward/replay modules remain the exact legacy baseline. This API
requires explicit fee provenance, order intent and book/trade evidence. Queue
assumptions and synthetic quotes must be labeled by callers as sensitivity cases.
"""

from dataclasses import asdict, dataclass, field
from decimal import Decimal, ROUND_HALF_UP
import math


class UnknownFees(ValueError):
    pass


@dataclass(frozen=True)
class FeeSchedule:
    market_id: str
    valid_from: float
    valid_until: float | None
    recorded_at: float
    maker_rate: float
    taker_rate: float
    status: str
    provenance: str
    exponent: float = 1.0
    maker_rebate_rate: float = 0.0
    rebate_evidence: str | None = None
    precision: int | None = None

    def __post_init__(self):
        values = (
            self.maker_rate,
            self.taker_rate,
            self.exponent,
            self.maker_rebate_rate,
        )
        if any(not math.isfinite(v) or v < 0 for v in values):
            raise ValueError("Fee parameters must be finite and nonnegative")
        if not self.provenance or self.status not in (
            "historical_verified",
            "current_snapshot",
            "assumed_legacy",
            "sensitivity",
        ):
            raise ValueError("Explicit supported provenance/status required")
        if self.valid_until is not None and self.valid_until <= self.valid_from:
            raise ValueError("Empty fee validity interval")
        if self.status == "current_snapshot" and self.valid_from < self.recorded_at:
            raise ValueError("Current schedules cannot be backdated")

    def charges(self, role, price, quantity):
        if (
            role not in ("maker", "taker")
            or not 0 <= price <= 1
            or quantity < 0
            or not math.isfinite(quantity)
        ):
            raise ValueError("Invalid fill")
        rate = self.maker_rate if role == "maker" else self.taker_rate
        curve = (price * (1 - price)) ** self.exponent
        fee = quantity * rate * curve
        rebate = (
            quantity * self.maker_rebate_rate * curve
            if role == "maker" and self.rebate_evidence
            else 0.0
        )
        if self.precision is not None:
            quantum = Decimal(1).scaleb(-self.precision)
            fee = float(Decimal(str(fee)).quantize(quantum, rounding=ROUND_HALF_UP))
            rebate = float(
                Decimal(str(rebate)).quantize(quantum, rounding=ROUND_HALF_UP)
            )
        return fee, rebate


class FeeRegistry:
    def __init__(self, schedules):
        self.schedules = list(schedules)

    def at(self, market_id, t, allow_assumed=False):
        valid = [
            f
            for f in self.schedules
            if f.market_id == market_id
            and f.valid_from <= t
            and (f.valid_until is None or t < f.valid_until)
            and (
                allow_assumed or f.status in ("historical_verified", "current_snapshot")
            )
        ]
        if len(valid) != 1:
            raise UnknownFees("Missing or ambiguous historical fee schedule")
        return valid[0]


@dataclass(frozen=True)
class Book:
    timestamp: float
    bids: tuple
    asks: tuple
    tick_size: float = 0.01
    min_size: float = 0.0
    evidence: str = "provided_book"
    min_notional: float = 0.0

    def __post_init__(self):
        if (
            self.tick_size <= 0
            or self.min_size < 0
            or self.min_notional < 0
            or not self.bids
            or not self.asks
            or not any(q > 0 for p, q in self.bids)
            or not any(q > 0 for p, q in self.asks)
        ):
            raise ValueError("Book requires two sides and valid constraints")
        if any(
            not 0 <= p <= 1 or q < 0 or not math.isfinite(p + q)
            for p, q in (*self.bids, *self.asks)
        ):
            raise ValueError("Invalid book levels")
        if self.bid >= self.ask:
            raise ValueError("Locked/crossed books are unavailable")

    @property
    def bid(self):
        return max(p for p, q in self.bids if q > 0)

    @property
    def ask(self):
        return min(p for p, q in self.asks if q > 0)

    @property
    def mid(self):
        return (self.bid + self.ask) / 2


@dataclass(frozen=True)
class Trade:
    timestamp: float
    price: float
    quantity: float
    aggressor_side: str
    evidence: str = "provided_aggressor_trade"

    def __post_init__(self):
        if (
            self.aggressor_side not in ("buy", "sell")
            or not 0 <= self.price <= 1
            or not math.isfinite(self.quantity)
            or self.quantity < 0
        ):
            raise ValueError("Trade requires known aggressor, price and size")


@dataclass(frozen=True)
class Order:
    order_id: str
    market_id: str
    side: str
    quantity: float
    limit: float
    timestamp: float
    expiry_s: float = 120.0
    post_only: bool = False

    def __post_init__(self):
        if (
            self.side not in ("buy", "sell")
            or not 0 < self.quantity < math.inf
            or not 0 <= self.limit <= 1
            or not 0 < self.expiry_s < math.inf
        ):
            raise ValueError("Invalid order intent")


class ExecutionEngine:
    def __init__(
        self,
        schedules,
        latency_s=1.0,
        cancel_latency_s=0.0,
        max_quote_age_s=90.0,
        allow_assumed_fees=False,
    ):
        if min(latency_s, cancel_latency_s, max_quote_age_s) < 0:
            raise ValueError("Negative latency")
        self.fees = FeeRegistry(schedules)
        self.latency = latency_s
        self.cancel_latency = cancel_latency_s
        self.max_age = max_quote_age_s
        self.allow_assumed = allow_assumed_fees

    def execute(self, order, books, trades, *, scenario, queue_ahead=None):
        if scenario not in ("observed", "optimistic", "pessimistic", "approximate"):
            raise ValueError("Explicit execution evidence/scenario required")
        arrival = order.timestamp + self.latency
        expiry = order.timestamp + order.expiry_s
        cancelled = expiry + self.cancel_latency
        result = {
            "intent": asdict(order),
            "arrival_timestamp": arrival,
            "expiry_timestamp": expiry,
            "cancellation_timestamp": cancelled,
            "scenario": scenario,
            "evidence_class": "approximate_replay"
            if scenario == "observed"
            else "sensitivity",
            "fills": [],
            "requested_quantity": order.quantity,
            "filled_quantity": 0.0,
            "unfilled_quantity": order.quantity,
            "status": "expired_unfilled",
            "fee_schedule": None,
            "queue_ahead_assumed": (
                queue_ahead
                if queue_ahead is None or math.isfinite(queue_ahead)
                else "unbounded"
            ),
            "unfilled_reason": None,
        }

        def finish(status, reason=None):
            result.update(
                status=status,
                unfilled_reason=reason,
                filled_quantity=order.quantity - result["unfilled_quantity"],
            )
            return result

        books = sorted(books, key=lambda b: b.timestamp)

        def asof(t):
            seen = [
                b for b in books if b.timestamp <= t and t - b.timestamp <= self.max_age
            ]
            return seen[-1] if seen else None

        book = asof(arrival)
        if arrival >= cancelled:
            return finish("expired_unfilled", "latency_exceeds_expiry")
        if book is None:
            return finish("unavailable_book", "missing_or_stale_arrival_book")
        if order.quantity < book.min_size:
            return finish("below_minimum_size", "market_minimum")
        if order.quantity * order.limit < book.min_notional:
            return finish("below_minimum_notional", "market_minimum_usd_notional")
        if (
            abs(order.limit / book.tick_size - round(order.limit / book.tick_size))
            > 1e-7
        ):
            return finish("invalid_tick", "market_tick_size")
        cross = (
            order.limit >= book.ask if order.side == "buy" else order.limit <= book.bid
        )
        if order.post_only and cross:
            return finish("post_only_rejected", "would_take_liquidity_at_arrival")
        try:
            fee = self.fees.at(order.market_id, arrival, self.allow_assumed)
        except UnknownFees:
            return finish(
                "unknown_fee_schedule", "no_execution_without_explicit_fee_assumption"
            )
        result["fee_schedule"] = asdict(fee)

        def fill(t, price, quantity, role, reference):
            try:
                schedule = self.fees.at(order.market_id, t, self.allow_assumed)
            except UnknownFees:
                return False
            cost, rebate = schedule.charges(role, price, quantity)
            direction = 1 if order.side == "buy" else -1
            arrival_spread = direction * (price - reference.mid) * quantity
            # Decompose versus both contemporaneous and decision reference; no
            # separate spread debit is added on top of executable price.
            decision_book = asof(order.timestamp) or book
            latency_move = direction * (reference.mid - decision_book.mid) * quantity
            result["fills"].append(
                {
                    "order_id": order.order_id,
                    "timestamp": t,
                    "price": price,
                    "quantity": quantity,
                    "liquidity_role": role,
                    "fee_schedule": asdict(schedule),
                    "fees": cost,
                    "rebates": rebate,
                    "mid_reference": reference.mid,
                    "decision_mid_reference": decision_book.mid,
                    "spread_cost": arrival_spread,
                    "latency_slippage": latency_move,
                    "spread_slippage": arrival_spread + latency_move,
                    "cash_delta": (-1 if order.side == "buy" else 1) * price * quantity
                    - cost
                    + rebate,
                    "evidence": reference.evidence,
                    "scenario": scenario,
                }
            )
            result["unfilled_quantity"] = max(
                0.0, result["unfilled_quantity"] - quantity
            )
            return True

        if cross:
            levels = (
                sorted(book.asks)
                if order.side == "buy"
                else sorted(book.bids, reverse=True)
            )
            for price, size in levels:
                if (order.side == "buy" and price > order.limit) or (
                    order.side == "sell" and price < order.limit
                ):
                    break
                qty = min(size, result["unfilled_quantity"])
                if qty > 0 and not fill(arrival, price, qty, "taker", book):
                    return finish("unknown_fee_schedule")
                if result["unfilled_quantity"] <= 1e-9:
                    return finish("filled")
            # Marketable limit order remainder may rest, unless caller cancels it.
        if queue_ahead is None:
            if scenario == "optimistic":
                queue = 0.0
            elif scenario == "pessimistic":
                queue = math.inf
            else:
                return finish(
                    "expired_partial" if result["fills"] else "expired_unfilled",
                    "queue_position_unknown",
                )
        else:
            if queue_ahead < 0:
                raise ValueError("Negative queue ahead")
            queue = queue_ahead
        result["queue_ahead_assumed"] = queue if math.isfinite(queue) else "unbounded"
        for trade in sorted(trades, key=lambda x: x.timestamp):
            if not arrival < trade.timestamp < cancelled:
                continue
            if trade.aggressor_side == order.side:
                continue
            eligible = (
                trade.price <= order.limit
                if order.side == "buy"
                else trade.price >= order.limit
            )
            if not eligible:
                continue
            consumed = min(queue, trade.quantity)
            queue -= consumed
            quantity = min(result["unfilled_quantity"], trade.quantity - consumed)
            reference = asof(trade.timestamp)
            if quantity > 0 and reference is not None:
                # Price-through prints are queue sensitivity evidence, not an
                # observed fill of our nonexistent historical order.
                if not fill(trade.timestamp, order.limit, quantity, "maker", reference):
                    return finish("unknown_fee_schedule")
                if result["unfilled_quantity"] <= 1e-9:
                    return finish("filled")
        return finish(
            "expired_partial" if result["fills"] else "expired_unfilled",
            "local_cancel_deadline",
        )


@dataclass
class Account:
    cash: float = 0.0
    gross_cash: float = 0.0
    midpoint_cash: float = 0.0
    fees: float = 0.0
    rebates: float = 0.0
    spread_slippage: float = 0.0
    positions: dict = field(default_factory=dict)
    settled: bool = False

    def apply(self, outcome, side, fills):
        if self.settled or side not in ("buy", "sell"):
            raise ValueError("Invalid accounting operation")
        quantity = sum(f["quantity"] for f in fills)
        if side == "sell" and quantity > self.positions.get(outcome, 0) + 1e-9:
            raise ValueError("Cannot oversell inventory")
        direction = -1 if side == "buy" else 1
        for f in fills:
            self.cash += f["cash_delta"]
            self.gross_cash += direction * f["price"] * f["quantity"]
            self.midpoint_cash += (
                direction * f["decision_mid_reference"] * f["quantity"]
            )
            self.fees += f["fees"]
            self.rebates += f["rebates"]
            self.spread_slippage += f["spread_slippage"]
        self.positions[outcome] = self.positions.get(outcome, 0) - direction * quantity

    def settle(self, result):
        if self.settled:
            raise ValueError("Cannot settle twice")
        value = self.positions.get(result, 0)
        self.cash += value
        self.gross_cash += value
        self.midpoint_cash += value
        self.settled = True

    @property
    def net_pnl(self):
        return self.cash

    @property
    def gross_pnl(self):
        return self.gross_cash

    @property
    def midpoint_pnl(self):
        return self.midpoint_cash


@dataclass(frozen=True)
class ExecutionPolicy:
    mode: str
    uncertainty_margin: float = 0.02
    slippage_margin: float = 0.0
    signal_valid_s: float = 180.0

    def __post_init__(self):
        if (
            self.mode not in ("taker", "maker", "hybrid")
            or min(self.uncertainty_margin, self.slippage_margin, self.signal_valid_s)
            < 0
        ):
            raise ValueError("Invalid execution policy")


def expected_cost(probability, side, quantity, book, schedule, role):
    """Expected entry debit/exit credit at executable quote, before uncertainty.

    Fees are distinct from the embedded bid/ask spread. Expected rebates are zero
    unless the supplied schedule contains verified eligibility/allocation evidence.
    This function receives only the observed book and contemporaneous forecast.
    """
    if (
        not 0 <= probability <= 1
        or quantity <= 0
        or side not in ("buy", "sell")
        or role not in ("maker", "taker")
    ):
        raise ValueError("Invalid cost estimate")
    price = (
        (book.bid if role == "maker" else book.ask)
        if side == "buy"
        else (book.ask if role == "maker" else book.bid)
    )
    fee, rebate = schedule.charges(role, price, quantity)
    all_in = (
        price + (fee - rebate) / quantity
        if side == "buy"
        else price - (fee - rebate) / quantity
    )
    edge = probability - all_in if side == "buy" else all_in - probability
    return {
        "side": side,
        "liquidity_role": role,
        "quote_price": price,
        "quantity": quantity,
        "estimated_fees": fee,
        "estimated_rebates": rebate,
        "all_in_per_share": all_in,
        "net_edge_per_share": edge,
        "fee_schedule": asdict(schedule),
        "spread_embedded_in_quote": True,
    }


def execute_policy(
    engine, order, probability, books, trades, policy, *, scenario, queue_ahead=None
):
    """GTC post-only with local cancel; hybrid waits for assumed cancel ack.

    Rechecks current bid/ask, fee schedule, remaining quantity and signal expiry
    before crossing. Forecast/signal and requested size are otherwise unchanged.
    It does not use upcoming trades, fill prices or outcomes for order selection.
    """
    orders = []
    fills = []

    def submit(t, quantity, role):
        available = [
            b for b in books if b.timestamp <= t and t - b.timestamp <= engine.max_age
        ]
        base = {
            "timestamp": t,
            "requested_quantity": quantity,
            "liquidity_role_intent": role,
            "policy": asdict(policy),
            "signal_probability": probability,
            "signal_timestamp": order.timestamp,
        }
        remaining_signal_life = order.timestamp + policy.signal_valid_s - t
        if remaining_signal_life <= 0:
            base["status"] = "signal_expired"
            orders.append(base)
            return None
        if not available:
            base["status"] = "unavailable_decision_book"
            orders.append(base)
            return None
        observed = max(available, key=lambda b: b.timestamp)
        try:
            fee = engine.fees.at(order.market_id, t, engine.allow_assumed)
        except UnknownFees:
            base["status"] = "unknown_fee_schedule"
            orders.append(base)
            return None
        lifetime = min(order.expiry_s, remaining_signal_life)
        # Only include announced changes that were recorded by the decision time.
        # Later fee evidence cannot influence an earlier order. The current fee
        # remains the explicit baseline for retrospective sensitivity schedules.
        known = [fee] + [
            f
            for f in engine.fees.schedules
            if f.market_id == order.market_id
            and f.recorded_at <= t
            and f.valid_from < t + lifetime
            and (f.valid_until is None or f.valid_until > t)
            and (
                engine.allow_assumed
                or f.status in ("historical_verified", "current_snapshot")
            )
        ]
        quote = min(
            (
                expected_cost(probability, order.side, quantity, observed, f, role)
                for f in known
            ),
            key=lambda q: q["net_edge_per_share"],
        )
        base["fee_estimation"] = (
            "worst already-published schedule during valid order lifetime"
        )
        base["expected_cost"] = quote
        if (
            quote["net_edge_per_share"]
            <= policy.uncertainty_margin + policy.slippage_margin + 1e-12
        ):
            base["status"] = "edge_below_margin"
            orders.append(base)
            return None
        price = quote["quote_price"]
        if role == "taker":
            price += policy.slippage_margin * (1 if order.side == "buy" else -1)
        price = (
            min(price, order.limit) if order.side == "buy" else max(price, order.limit)
        )
        # Round limit conservatively to the currently observed market tick.
        ticks = price / observed.tick_size
        price = (
            math.floor(ticks + 1e-9) if order.side == "buy" else math.ceil(ticks - 1e-9)
        ) * observed.tick_size
        request = Order(
            order.order_id + ":" + role,
            order.market_id,
            order.side,
            quantity,
            price,
            t,
            lifetime,
            role == "maker",
        )
        result = engine.execute(
            request,
            books,
            trades,
            scenario=scenario,
            queue_ahead=queue_ahead if role == "maker" else math.inf,
        )
        # FAK-style taker intent cannot accidentally rest as a maker order.
        if (
            role == "taker"
            and result["unfilled_quantity"] > 0
            and result["status"] in ("expired_partial", "expired_unfilled")
        ):
            result["status"] = (
                "cancelled_partial" if result["fills"] else "cancelled_unfilled"
            )
            result["cancellation_timestamp"] = result["arrival_timestamp"]
        orders.append({**base, **result})
        fills.extend(result["fills"])
        return result

    role = "taker" if policy.mode == "taker" else "maker"
    first = submit(order.timestamp, order.quantity, role)
    if policy.mode == "hybrid" and first and first["unfilled_quantity"] > 1e-9:
        # A post-only rejection never had a resting order, so can cross after its
        # rejection arrives; otherwise wait for the explicit local cancellation.
        when = (
            first["arrival_timestamp"]
            if first["status"] == "post_only_rejected"
            else first["cancellation_timestamp"]
        )
        submit(when, first["unfilled_quantity"], "taker")
    filled = sum(f["quantity"] for f in fills)
    return {
        "intent": asdict(order),
        "policy": asdict(policy),
        "scenario": scenario,
        "orders": orders,
        "fills": fills,
        "requested_quantity": order.quantity,
        "filled_quantity": filled,
        "unfilled_quantity": max(0.0, order.quantity - filled),
        "status": "filled"
        if filled >= order.quantity - 1e-9
        else "partial"
        if filled
        else "unfilled",
    }
