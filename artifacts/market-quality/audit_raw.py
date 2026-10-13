import argparse
import collections
import copy
import gzip
import hashlib
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path.cwd()))
from app.trade_env.clock import decision_points
from app.trade_env.config import make_config
from app.trade_env.tasks import quotes
from pipelines.kalshi.history import close_price

parser = argparse.ArgumentParser(
    description="Audit cached Kalshi candles and a separate quote-midpoint diagnostic."
)
parser.add_argument("--snapshot", type=pathlib.Path, required=True)
parser.add_argument("--records", type=pathlib.Path, required=True)
parser.add_argument(
    "--cache", type=pathlib.Path, default=pathlib.Path("data/raw/kalshi")
)
parser.add_argument(
    "--output",
    type=pathlib.Path,
    default=pathlib.Path("artifacts/market-quality/kalshi-raw-audit.json"),
)
args = parser.parse_args()
root = args.snapshot
manifest = json.loads((root / "manifest.json").read_text())
with gzip.open(args.records, "rt") as f:
    records = [json.loads(line) for line in f]
lookup = {r["match_id"]: r for r in records}
tickers = {
    m["market_id"].removeprefix("kalshi:"): (r["match_id"], side)
    for r in records
    for side, m in r["markets"].items()
    if isinstance(m, dict)
}
original = collections.Counter()
counts = collections.Counter()
comps = collections.defaultdict(collections.Counter)
samples = {}
raw_prices = {}
oi = []
vol = []
spreads = []
for meta in manifest["raw_sources"]:
    if "/candlesticks?" not in meta["url"]:
        continue
    raw = gzip.decompress((args.cache / meta["raw_file"]).read_bytes())
    assert hashlib.sha256(raw).hexdigest() == meta["sha256"]
    data = json.loads(raw)
    ticker = data["ticker"]
    mid, side = tickers[ticker]
    r = lookup[mid]
    comp = r["competition_id"]
    q = copy.deepcopy(r)
    recordsamples = []
    tradeprices = []
    for candle in data["candlesticks"]:
        t = candle["end_period_ts"]
        v = float(candle["volume"])
        price = close_price(candle["price"])
        bid = close_price(candle["yes_bid"])
        ask = close_price(candle["yes_ask"])
        # Only retained pre-regulation + regulation rows influence decision comparisons.
        if t > r["periods"]["h2_end"]:
            continue
        if v > 0 and price is not None:
            tradeprices.append([t, price])
        valid = (
            bid is not None
            and ask is not None
            and 0 < bid <= ask < 1
            and ask - bid <= 0.10 + 1e-9
        )
        if valid:
            recordsamples.append([t, (bid + ask) / 2])
        if t < r["periods"]["h1_start"]:
            continue
        labels = [
            "raw_regulation_minutes",
            "traded" if v > 0 and price is not None else "no_trade_close",
        ]
        if v > 0 and price is None:
            labels.append("positive_volume_missing_close")
        if valid:
            labels.append("valid_quote_minutes")
            spreads.append(ask - bid)
        if v == 0 and valid:
            labels.append("zero_volume_valid_quote_minutes")
        for label in labels:
            counts[label] += 1
            comps[comp][label] += 1
        if "open_interest" in candle:
            oi.append(float(candle["open_interest"]))
        vol.append(v)
    samples[ticker] = recordsamples
    raw_prices[ticker] = tradeprices
    counts["responses_verified"] += 1
missing = extra = 0
for ticker, raw in raw_prices.items():
    mid, side = tickers[ticker]
    stored = lookup[mid]["prices"][side]
    rawset = {(t, round(p, 9)) for t, p in raw}
    storedset = {(t, round(p, 9)) for t, p in stored}
    missing += len(rawset - storedset)
    extra += len(storedset - rawset)
comparison = collections.Counter()
candidates = []
bycomp = collections.defaultdict(collections.Counter)
monthly = collections.defaultdict(collections.Counter)
for r in records:
    q = copy.deepcopy(r)
    for side, m in q["markets"].items():
        if isinstance(m, dict):
            q["prices"][side] = samples[m["market_id"].removeprefix("kalshi:")]
    pts = decision_points(r["periods"], make_config())
    old = quotes(r, pts, make_config())
    new = quotes(q, pts, make_config())
    diffs = []
    for p, a, b in zip(pts, old, new):
        label = (
            "both"
            if not a["reason"] and not b["reason"]
            else "quote_only"
            if not b["reason"]
            else "trade_only"
            if not a["reason"]
            else "neither"
        )
        comparison[label] += 1
        bycomp[r["competition_id"]][label] += 1
        monthly[r["scheduled_utc"][:7]][a["reason"] or "usable"] += 1
        if a["reason"] and not b["reason"]:
            diffs.append({"decision": p["name"], "old_reason": a["reason"]})
    if diffs:
        candidates.append(
            dict(
                match_id=r["match_id"],
                home=r["home"],
                away=r["away"],
                recovered_decisions=diffs,
            )
        )
report = dict(
    raw_candles=dict(counts),
    by_competition={k: dict(v) for k, v in comps.items()},
    missing_positive_trade_closes=missing,
    extra_stored_trade_closes=extra,
    quote_diagnostic=dict(comparison),
    quote_diagnostic_by_competition={k: dict(v) for k, v in bycomp.items()},
    monthly_decisions={k: dict(v) for k, v in sorted(monthly.items())},
    candidate_matches=len(candidates),
    candidates=candidates,
    quote_policy="Diagnostic only: minute-close bid/ask midpoint, 0 < bid <= ask < 1, spread <= 0.10, same 90s freshness / 5% sum / 60s delay / 120s wait. No order size or quote-change timestamps; not executable fills.",
)
args.output.write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps({k: v for k, v in report.items() if k != "candidates"}, indent=2))
