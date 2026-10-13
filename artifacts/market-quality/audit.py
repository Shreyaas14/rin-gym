import argparse
import bisect
import collections
import gzip
import hashlib
import json
import math
import pathlib
import statistics
import sys

sys.path.insert(0, str(pathlib.Path.cwd()))
import duckdb
from app.trade_env.config import make_config
from app.trade_env.clock import decision_points
from app.trade_env.tasks import quotes
from app.trade_env.state import shown_prices

parser = argparse.ArgumentParser(
    description="Compare existing venue records and DuckDB snapshots; no network or dataset mutations."
)
parser.add_argument("--polymarket-records", type=pathlib.Path, required=True)
parser.add_argument("--kalshi-records", type=pathlib.Path, required=True)
parser.add_argument("--trades-records", type=pathlib.Path, required=True)
parser.add_argument("--polymarket-db", default="data/replay.duckdb")
parser.add_argument("--kalshi-db", default="data/kalshi-replay.duckdb")
parser.add_argument(
    "--output", type=pathlib.Path, default=pathlib.Path("artifacts/market-quality")
)
args = parser.parse_args()
OUT = args.output
OUT.mkdir(parents=True, exist_ok=True)
CONFIG = make_config()
PATHS = {
    "polymarket": args.polymarket_records,
    "kalshi": args.kalshi_records,
    "kalshi_trades_sample": args.trades_records,
}


def dist(a):
    a = sorted(a)
    return (
        dict(
            n=len(a),
            p50=a[int((len(a) - 1) * 0.5)],
            p95=a[int((len(a) - 1) * 0.95)],
            max=a[-1],
            mean=statistics.mean(a),
        )
        if a
        else None
    )


def assess(records):
    totals = collections.Counter()
    ages = []
    gaps = []
    sample_counts = []
    matchrows = []
    decisions = {}
    unchanged = 0
    transitions = 0
    fresh = coherent = gridn = 0
    goalsfresh = goalsn = 0
    for r in records:
        ps = r["periods"]
        start, end = ps["h1_start"], ps["h2_end"]
        points = decision_points(ps, CONFIG)
        qs = quotes(r, points, CONFIG)
        ds = {p["name"]: q["reason"] or "usable" for p, q in zip(points, qs)}
        totals.update(ds.values())
        decisions[r["match_id"]] = ds
        worst = []
        num = 0
        for s in r["prices"].values():
            ig = [p for p in s if start <= p[0] <= end]
            sample_counts.append(len(ig))
            num += len(ig)
            gg = [b[0] - a[0] for a, b in zip(ig, ig[1:])]
            gaps += gg
            worst.append(max(gg, default=0))
            transitions += max(0, len(ig) - 1)
            unchanged += sum(a[1] == b[1] for a, b in zip(ig, ig[1:]))
        localgrid = localfresh = localcoherent = 0
        for t in range(math.ceil(start), math.floor(end) + 1, 60):
            localgrid += 1
            gridn += 1
            values = []
            for s in r["prices"].values():
                i = bisect.bisect_right(s, t, key=lambda p: p[0])
                age = t - s[i - 1][0] if i else None
                if age is not None:
                    ages.append(age)
                values.append(age)
            if all(v is not None and v <= 90 for v in values):
                localfresh += 1
                fresh += 1
            if shown_prices(r["prices"], t, CONFIG)[0]:
                localcoherent += 1
                coherent += 1
        score = (0, 0)
        for e in sorted(r["events"], key=lambda e: (e["t"], e["seq"])):
            now = (max(score[0], e["home_goals"]), max(score[1], e["away_goals"]))
            if now != score and start <= e["t"] <= end - 60:
                goalsn += 1
                goalsfresh += bool(shown_prices(r["prices"], e["t"] + 60, CONFIG)[0])
            score = now
        matchrows.append(
            dict(
                match_id=r["match_id"],
                competition_id=r["competition_id"],
                home=r["home"],
                away=r["away"],
                usable_decisions=sum(v == "usable" for v in ds.values()),
                failures=dict(
                    collections.Counter(v for v in ds.values() if v != "usable")
                ),
                in_regulation_yes_rows=num,
                max_internal_gap_s=max(worst),
                grid_points=localgrid,
                fresh_triplet_points=localfresh,
                coherent_fresh_triplet_points=localcoherent,
            )
        )
    return (
        dict(
            records=len(records),
            decisions=dict(totals),
            tasks=totals["usable"] * 4,
            matches_with_tasks=sum(r["usable_decisions"] > 0 for r in matchrows),
            all_five_decisions=sum(r["usable_decisions"] == 5 for r in matchrows),
            in_regulation_yes_rows=sum(r["in_regulation_yes_rows"] for r in matchrows),
            yes_rows_per_match_side=dist(sample_counts),
            internal_sample_gap_s=dist(gaps),
            asof_sample_age_s=dist(ages),
            unchanged_adjacent_price_fraction=unchanged / transitions
            if transitions
            else None,
            minute_grid=dict(
                points=gridn, fresh_triplets=fresh, coherent_fresh_triplets=coherent
            ),
            goal_plus_60s=dict(points=goalsn, coherent_fresh_triplets=goalsfresh),
        ),
        matchrows,
        decisions,
    )


records = {}
for venue, path in PATHS.items():
    with gzip.open(path, "rt") as f:
        records[venue] = [json.loads(line) for line in f]
common = set(r["match_id"] for r in records["polymarket"]) & set(
    r["match_id"] for r in records["kalshi"]
)
report = {
    "config": CONFIG,
    "input_sha256": {
        k: hashlib.sha256(p.read_bytes()).hexdigest() for k, p in PATHS.items()
    },
    "venues": {},
    "paired": {},
}
rows = {}
decs = {}
for venue, rr in records.items():
    summary, rows[venue], decs[venue] = assess(rr)
    report["venues"][venue] = summary
    report["venues"][venue]["by_competition"] = {
        comp: assess([r for r in rr if r["competition_id"] == comp])[0]
        for comp in sorted({r["competition_id"] for r in rr})
    }
for venue in ["polymarket", "kalshi"]:
    report["paired"][venue] = assess(
        [r for r in records[venue] if r["match_id"] in common]
    )[0]
paired = collections.Counter()
recovery = []
for mid in sorted(common):
    for d, p in decs["polymarket"][mid].items():
        k = decs["kalshi"][mid][d]
        paired[
            (
                "both"
                if p == k == "usable"
                else "polymarket_only"
                if p == "usable"
                else "kalshi_only"
                if k == "usable"
                else "neither"
            )
        ] += 1
for r in rows["kalshi"]:
    if r["usable_decisions"] < 5:
        recovery.append(
            dict(
                r,
                polymarket_usable_decisions=sum(
                    x == "usable" for x in decs["polymarket"][r["match_id"]].values()
                ),
            )
        )
report["paired_decisions"] = dict(paired)
report["kalshi_recovery_candidates"] = sorted(
    recovery, key=lambda r: (r["usable_decisions"], r["match_id"])
)
# Compare both venues at identical decision instants using as-of samples, without normalization.
lookup = {v: {r["match_id"]: r for r in rr} for v, rr in records.items()}
errors = []
points = []
for mid in common:
    p, k = lookup["polymarket"][mid], lookup["kalshi"][mid]
    assert p["periods"] == k["periods"] and p["regulation"] == k["regulation"]
    for d in decision_points(p["periods"], CONFIG):
        pp = shown_prices(p["prices"], d["t"], CONFIG)[0]
        kp = shown_prices(k["prices"], d["t"], CONFIG)[0]
        if pp and kp:
            dd = [abs(pp[s] - kp[s]) for s in pp]
            errors += dd
            points.append(
                dict(
                    match_id=mid,
                    decision=d["name"],
                    max_side_difference=max(dd),
                    polymarket=pp,
                    kalshi=kp,
                )
            )
report["paired_price_absolute_difference"] = dist(errors)
report["largest_paired_price_differences"] = sorted(
    points, key=lambda p: p["max_side_difference"], reverse=True
)[:10]
# Compare existing full trade sample against its minute-candle counterpart.
mid = records["kalshi_trades_sample"][0]["match_id"]
report["trade_sample_candle_comparison"] = assess([lookup["kalshi"][mid]])[0]
for venue, db in [("polymarket", args.polymarket_db), ("kalshi", args.kalshi_db)]:
    con = duckdb.connect(db, read_only=True)
    metadata = json.loads(con.execute("select * from replay_metadata").fetchone()[0])
    report["venues"][venue]["source_manifests"] = {
        k: v for k, v in metadata.items() if k.endswith("_sha256")
    }
    report["venues"][venue]["catalog"] = dict(
        markets=con.execute("select count(*) from markets").fetchone()[0],
        prices=con.execute("select count(*) from prices").fetchone()[0],
        market_families=dict(
            con.execute(
                "select market_family,count(*) from markets group by 1"
            ).fetchall()
        ),
    )
    report["venues"][venue]["identity_checks"] = {
        "invalid_prices": con.execute(
            "select count(*) from prices where price is null or not isfinite(price) or price<0 or price>1 or timestamp is null or not isfinite(timestamp)"
        ).fetchone()[0],
        "duplicate_samples": con.execute(
            "select count(*) from (select token_id,timestamp from prices group by 1,2 having count(*)>1)"
        ).fetchone()[0],
        "duplicate_hda_roles": con.execute(
            "select count(*) from (select match_id,hda_outcome from markets where hda_outcome is not null group by 1,2 having count(*)>1)"
        ).fetchone()[0],
    }
    # per-family liquidity metadata is snapshot lifetime, not in-game execution evidence.
    props = con.execute(
        "select market_family,count(*),sum(list_sum(list_transform(tokens,t -> t.in_game_points)) = 0) from markets group by 1"
    ).fetchall()
    report["venues"][venue]["families_without_any_in_game_points"] = {
        f: n for f, c, n in props if n
    }
    (OUT / (venue + "-matches.json")).write_text(
        json.dumps(rows[venue], indent=2) + "\n"
    )
(OUT / "audit.json").write_text(json.dumps(report, indent=2) + "\n")
print(
    json.dumps(
        {
            k: {a: b for a, b in v.items() if a not in ["by_competition", "catalog"]}
            for k, v in report["venues"].items()
        },
        indent=2,
    )
)
print("paired", report["paired_decisions"])
