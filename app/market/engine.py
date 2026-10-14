"""Deterministic, fully collateralized exchange for a single YES contract.

All money and prices use integer cents. Negative YES inventory represents NO
exposure; there is no second book or hidden source of liquidity.
"""

from dataclasses import asdict, dataclass
from math import isfinite


@dataclass
class Account:
    cash: int = 100_000
    position: int = 0
    limit: int = 100

    @property
    def collateral(self):
        return max(0, -self.position) * 100


@dataclass
class Order:
    id: str
    owner: str
    side: str
    price: int
    size: int
    remaining: int
    status: str
    sequence: int


class Exchange:
    """An in-memory exchange; the session must serialize calls to this class."""

    def __init__(self):
        self.accounts = {owner: Account() for owner in ("anchor", "pulse", "user")}
        self.orders = {}
        self.trades = []
        self.journal = []
        self.settled = False
        self.outcome = None
        self._sequence = 0

    def _next_sequence(self):
        self._sequence += 1
        return self._sequence

    def _owner(self, owner):
        if not isinstance(owner, str) or owner not in self.accounts:
            raise ValueError("Unknown account")
        return self.accounts[owner]

    def _active(self):
        if self.settled:
            raise ValueError("Market has settled")

    def _open(self, owner=None, side=None):
        return [
            order for order in self.orders.values()
            if order.status == "open"
            and (owner is None or order.owner == owner)
            and (side is None or order.side == side)
        ]

    def _reserved(self, owner):
        return sum(
            order.remaining * (order.price if order.side == "buy" else 100 - order.price)
            for order in self._open(owner=owner)
        )

    def _check_risk(self, owner, side, price, size):
        account = self.accounts[owner]
        orders = self._open(owner=owner)
        buys = sum(o.remaining for o in orders if o.side == "buy")
        sells = sum(o.remaining for o in orders if o.side == "sell")
        if side == "buy":
            buys += size
        else:
            sells += size
        if account.position + buys > account.limit or account.position - sells < -account.limit:
            raise ValueError("Order exceeds position limit including pending orders")
        reservation = size * (price if side == "buy" else 100 - price)
        if account.cash < account.collateral + self._reserved(owner) + reservation:
            raise ValueError("Insufficient available collateral")

    def _opponents(self, side):
        opposite = "sell" if side == "buy" else "buy"
        return sorted(
            self._open(side=opposite),
            key=lambda o: (o.price if side == "buy" else -o.price, o.sequence),
        )

    @staticmethod
    def _crosses(side, price, resting):
        return resting.price <= price if side == "buy" else resting.price >= price

    def submit(self, owner, side="buy", price=None, size=None, tif="GTC"):
        """Accept a limit order, execute against makers, and return its fills.

        Validation, including full pending-order risk, happens before mutation.
        Self-trade prevention cancels the incoming remainder at the first self
        match, preserving price/time priority for all other participants.
        """
        self._active()
        self._owner(owner)
        if side not in ("buy", "sell"):
            raise ValueError("Side must be buy or sell")
        if type(price) is not int or not 1 <= price <= 99:
            raise ValueError("Price must be integer cents from 1 to 99")
        if type(size) is not int or size <= 0:
            raise ValueError("Size must be a positive whole number of shares")
        if tif not in ("GTC", "IOC", "post_only"):
            raise ValueError("Unsupported time in force")
        opponents = self._opponents(side)
        if tif == "post_only" and any(self._crosses(side, price, o) for o in opponents):
            raise ValueError("Post-only order would cross the book")
        self._check_risk(owner, side, price, size)

        sequence = self._next_sequence()
        incoming = Order(f"o{sequence}", owner, side, price, size, size, "open", sequence)
        self.orders[incoming.id] = incoming
        self.journal.append({"type": "order", "sequence": sequence, "order": asdict(incoming), "tif": tif})
        fills = []
        for maker in opponents:
            if not incoming.remaining or not self._crosses(side, price, maker):
                break
            if maker.owner == owner:
                self._cancel(incoming, "self_trade")
                break
            quantity = min(incoming.remaining, maker.remaining)
            buyer = owner if side == "buy" else maker.owner
            seller = maker.owner if side == "buy" else owner
            amount = quantity * maker.price
            self.accounts[buyer].cash -= amount
            self.accounts[buyer].position += quantity
            self.accounts[seller].cash += amount
            self.accounts[seller].position -= quantity
            incoming.remaining -= quantity
            maker.remaining -= quantity
            if not incoming.remaining:
                incoming.status = "filled"
            if not maker.remaining:
                maker.status = "filled"
            sequence = self._next_sequence()
            trade = {
                "id": f"t{sequence}", "price": maker.price, "size": quantity,
                "buyer": buyer, "seller": seller, "maker": maker.owner,
                "taker": owner, "sequence": sequence,
            }
            self.trades.append(trade)
            fills.append(dict(trade))
            self.journal.append({"type": "fill", **trade})
        if incoming.remaining and incoming.status == "open" and tif == "IOC":
            self._cancel(incoming, "IOC")
        return {"order": asdict(incoming), "fills": fills}

    def _cancel(self, order, reason):
        order.status = "cancelled"
        self.journal.append({
            "type": "cancel", "sequence": self._next_sequence(),
            "order_id": order.id, "owner": order.owner, "reason": reason,
        })

    def cancel(self, owner, order_id):
        self._active()
        self._owner(owner)
        if not isinstance(order_id, str) or order_id not in self.orders:
            raise ValueError("Unknown order")
        order = self.orders[order_id]
        if order.owner != owner:
            raise ValueError("Cannot cancel another account's order")
        if order.status == "open":
            self._cancel(order, "owner")
        return asdict(order)

    def cancel_all(self, owner=None):
        self._active()
        if owner is not None:
            self._owner(owner)
        cancelled = []
        for order in self._open(owner=owner):
            self._cancel(order, "refresh" if owner is None else "owner")
            cancelled.append(asdict(order))
        return cancelled

    def settle(self, y):
        if type(y) is not int or y not in (0, 1):
            raise ValueError("Settlement outcome must be integer 0 or 1")
        if self.settled:
            if self.outcome != y:
                raise ValueError("Market already settled with a different outcome")
            return self.snapshot()
        self.cancel_all()
        for account in self.accounts.values():
            account.cash += account.position * y * 100
            account.position = 0
        self.settled = True
        self.outcome = y
        self.journal.append({"type": "settlement", "sequence": self._next_sequence(), "outcome": y})
        return self.snapshot()

    def snapshot(self, mark=None):
        bids = sorted(self._open(side="buy"), key=lambda o: (-o.price, o.sequence))
        asks = sorted(self._open(side="sell"), key=lambda o: (o.price, o.sequence))
        if mark is not None and (
            isinstance(mark, bool) or not isinstance(mark, (int, float))
            or not isfinite(mark) or not 0 <= mark <= 100
        ):
            raise ValueError("Mark must be finite cents from 0 to 100")
        if self.settled:
            mark = self.outcome * 100
        elif mark is None and bids and asks:
            mark = (bids[0].price + asks[0].price) / 2
        accounts = {}
        for owner, account in self.accounts.items():
            reserved = self._reserved(owner)
            equity = account.cash + account.position * mark if mark is not None else None
            accounts[owner] = {
                "cash": account.cash, "position": account.position,
                "collateral": account.collateral, "reserved": reserved,
                "available": account.cash - account.collateral - reserved,
                "equity": equity, "pnl": equity - 100_000 if equity is not None else None,
            }
        return {
            "bids": [asdict(o) for o in bids], "asks": [asdict(o) for o in asks],
            "orders": [asdict(o) for o in sorted(self._open(), key=lambda o: o.sequence)],
            "trades": [dict(trade) for trade in self.trades], "accounts": accounts,
            "settled": self.settled, "outcome": self.outcome,
        }
