import argparse
import duckdb
import json
import pathlib

parser = argparse.ArgumentParser(
    description="Check settlements and per-family history coverage in both DuckDB snapshots."
)
parser.add_argument("--polymarket-db", default="data/replay.duckdb")
parser.add_argument("--kalshi-db", default="data/kalshi-replay.duckdb")
parser.add_argument(
    "--output",
    type=pathlib.Path,
    default=pathlib.Path("artifacts/market-quality/integrity.json"),
)
args = parser.parse_args()
out = {}
for venue, db in [("polymarket", args.polymarket_db), ("kalshi", args.kalshi_db)]:
    c = duckdb.connect(db, read_only=True)
    stats = {}
    bad = []
    unknown = []
    checked = 0
    for mid, side, raw, home, away in c.execute(
        "select m.match_id,m.hda_outcome,m.source_market_json,f.regulation_home_score,f.regulation_away_score from markets m join matches f using(match_id) where m.hda_outcome is not null"
    ).fetchall():
        m = json.loads(raw)
        result = "home" if home > away else "away" if away > home else "draw"
        expected = float(side == result)
        if venue == "polymarket":
            prices = json.loads(m["outcomePrices"])
            outcomes = json.loads(m["outcomes"])
            actual = float(prices[outcomes.index("Yes")])
        else:
            actual = {"yes": 1.0, "no": 0.0}.get(m.get("result"))
        if actual not in (0.0, 1.0):
            unknown.append(mid)
            continue
        checked += 1
        if expected != actual:
            bad.append(dict(match_id=mid, side=side, actual=actual, expected=expected))
    stats["settlement"] = {"checked": checked, "unknown": unknown, "disagreements": bad}
    cur = c.execute(
        """with p as (select market_id,token_id,count(*) filter(where in_game) n,count(distinct price) filter(where in_game) nprices from prices group by 1,2) select m.market_family,count(*) tokens,sum(n=0) no_in_game,sum(n between 1 and 4) fewer_than_five,sum(n>0 and nprices=1) constant_history from p join markets m using(market_id) group by 1 order by 1"""
    )
    stats["history_by_family"] = [
        dict(zip([col[0] for col in cur.description], row)) for row in cur.fetchall()
    ]
    stats["missing_quote_fields"] = (
        c.execute("select count(*) from prices").fetchone()[0]
        if venue == "polymarket"
        else c.execute(
            "select count(*) from prices where bid is null or ask is null"
        ).fetchone()[0]
    )
    out[venue] = stats
args.output.write_text(json.dumps(out, indent=2) + "\n")
print(json.dumps(out, indent=2))
