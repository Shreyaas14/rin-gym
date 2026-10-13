"""Normalize completed candles or Becker/API trades without forward filling."""

from collections import defaultdict
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path

from pipelines.fifa.markets import epoch


def number(value):
    if value is None:
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError("Invalid numeric value") from error
    if not result.is_finite():
        raise ValueError("Nonfinite numeric value")
    return float(result)


def probability(value):
    p = number(value)
    if p is not None and not 0 <= p <= 1:
        raise ValueError("Price outside [0,1]")
    return p


def trade_price(row, side):
    if side + "_price_dollars" in row:
        return probability(row[side + "_price_dollars"])
    value = number(row.get(side + "_price"))
    return probability(value / 100) if value is not None else None


def close_price(distribution):
    if "close_dollars" in distribution:
        return probability(distribution["close_dollars"])
    value = distribution.get("close")
    # Legacy live candles encode integer cents; historical candles use dollar
    # strings. Prefer explicitly named dollar fields when both are present.
    if type(value) is int:
        value /= 100
    return probability(value)


def candles(data, ticker, window, digest):
    if data.get(
        "ticker", data.get("market_ticker", ticker)
    ) != ticker or not isinstance(data.get("candlesticks"), list):
        raise ValueError("Candle response identity mismatch")
    rows = []
    for candle in data["candlesticks"]:
        t = number(candle["end_period_ts"])
        if t is None or t % 60:
            raise ValueError("Invalid minute close timestamp")
        if not window["start_ts"] <= t <= window["end_ts"]:
            continue
        price = close_price(candle["price"])
        volume = number(candle.get("volume", candle.get("volume_fp")))
        # No trade means no newly observed price. Never reuse `previous`.
        if price is None or volume == 0:
            continue
        if volume is None or volume < 0:
            raise ValueError("Missing or invalid candle volume")
        bid = close_price(candle.get("yes_bid", {}))
        ask = close_price(candle.get("yes_ask", {}))
        if bid is not None and ask is not None and bid > ask:
            raise ValueError("Crossed closing quote")
        rows.append(
            dict(
                timestamp=t,
                yes=price,
                no=1 - price,
                bid=bid,
                ask=ask,
                volume=volume,
                price_type="trade_close_1m",
                source_response_sha256=digest,
            )
        )
    return unique_times(rows)


def trades(payloads, ticker, window):
    seen, rows = {}, []
    for row, digest in payloads:
        if row.get("ticker") != ticker or not row.get("trade_id"):
            raise ValueError("Trade identity mismatch")
        normalized = dict(row)
        identity = json.dumps(normalized, sort_keys=True, default=str)
        if row["trade_id"] in seen:
            if seen[row["trade_id"]] != identity:
                raise ValueError("Conflicting trade ID")
            continue
        seen[row["trade_id"]] = identity
        t = epoch(row["created_time"])
        if not window["start_ts"] <= t <= window["end_ts"]:
            continue
        yes, no = (trade_price(row, side) for side in ("yes", "no"))
        if yes is None or no is None or abs(yes + no - 1) > 1e-8:
            raise ValueError("Invalid binary trade prices")
        volume = number(row.get("count_fp", row.get("count")))
        if volume is None or volume <= 0:
            raise ValueError("Invalid trade volume")
        rows.append(
            dict(
                timestamp=t,
                yes=yes,
                no=no,
                bid=None,
                ask=None,
                volume=volume,
                price_type="trade",
                source_response_sha256=digest,
            )
        )
    return unique_times(rows)


def unique_times(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["timestamp"]].append(row)
    result = []
    for t, group in sorted(grouped.items()):
        # Simultaneous trades with different prices have no observable ordering.
        if len({(r["yes"], r["no"]) for r in group}) != 1:
            continue
        result.append(group[0])
    return result


def becker_trades(directory, tickers):
    """Read an existing Becker Kalshi trades directory; preserve original files."""
    import pyarrow.dataset as ds

    root = Path(directory)
    dataset = ds.dataset(root, format="parquet")
    required = {"ticker", "trade_id", "created_time", "yes_price", "no_price"}
    if not required <= set(dataset.schema.names):
        raise ValueError("Expected Becker Kalshi trade schema")
    grouped, sources = defaultdict(list), []
    for fragment in dataset.get_fragments(filter=ds.field("ticker").isin(tickers)):
        path = Path(fragment.path)
        with path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        sources.append(
            dict(
                path=str(path.resolve()),
                sha256=digest,
                bytes=path.stat().st_size,
                format="becker_kalshi_trades",
            )
        )
        table = fragment.to_table(filter=ds.field("ticker").isin(tickers))
        for row in table.to_pylist():
            grouped[row["ticker"]].append((row, digest))
    return grouped, sources
