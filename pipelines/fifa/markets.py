"""Auditable FIFA fixture resolution and strictly backward Polymarket odds joins.

Run with ``python -m pipelines.fifa.markets``. The FIFA source snapshot is read-only.
"""

import argparse
from bisect import bisect_left
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlencode
from urllib.error import URLError
import uuid

import pyarrow as pa
import pyarrow.parquet as pq

from .client import Client, write_json
from .contracts import (
    SERIES,
    POLICY_VERSION,
    event_provenance,
    contract_provenance,
    remove_collisions,
    certify,
)
from .dataset import resolve_snapshot, verify

GAMMA = "https://gamma-api.polymarket.com/"
SCOPES = {"2000000000": (306, "epl"), "17": (102232, "fifwc")}
# Exact aliases, never substring/fuzzy matching. FC/AFC are removable whole words.
ALIASES = {
    "brighton": "brightonhovealbion",
    "wolves": "wolverhamptonwanderers",
    "newcastle": "newcastleunited",
    "tottenham": "tottenhamhotspur",
    "westham": "westhamunited",
    "czechrepublic": "czechia",
    "unitedstates": "usa",
    "bosniaherzegovina": "bosniaandherzegovina",
    "drcongo": "congodr",
    "ivorycoast": "cotedivoire",
    "turkey": "turkiye",
    "southkorea": "korearepublic",
    "iran": "iriran",
    "capeverde": "caboverde",
}
# Reviewed exceptions: exact fixture prefix AND exact signed time difference.
# FIFA's completed-match calendar is one hour later than the market schedule.
# Neither FIFA event timestamps nor market price timestamps are shifted.
TIME_OVERRIDES = {
    "400021520": (
        "fifwc-mex-ecu-2026-06-30",
        -3600,
        "https://www.fifa.com/en/tournaments/mens/worldcup/canadamexicousa2026/articles/mexico-ecuador-live-stream-team-news-tickets",
    ),
    "400021531": (
        "fifwc-mex-eng-2026-07-05",
        -3600,
        "https://www.englandfootball.com/england/mens-senior-team/fixtures-results/2025-26/World-Cup/mexico-england-fifa-world-cup-round-of-16-monday-6-july-2026-match-centre",
    ),
}
VERSION = "fifa-polymarket-v2"


class MarketClient(Client):
    response_types = (dict, list)


def norm(value):
    value = "".join(
        c
        for c in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(c)
    )
    value = re.sub(r"\b(?:fc|afc)\b", "", value)
    value = re.sub(r"[^a-z0-9]", "", value)
    return ALIASES.get(value, value)


def epoch(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("Timezone required")
    return value.timestamp()


def array(value):
    return json.loads(value) if isinstance(value, str) else value


def time_match(match, event, value):
    if not value:
        return False
    delta = epoch(value) - epoch(match["scheduled_kickoff_utc"])
    if abs(delta) <= 900:
        return True
    override = TIME_OVERRIDES.get(match["match_id"])
    return bool(
        override
        and delta == override[1]
        and (
            event["slug"] == override[0] or event["slug"].startswith(override[0] + "-")
        )
    )


def catalog(client, matches):
    """Paginate to exhaustion, including closed/open markets and auxiliary events."""
    sources, events = [], {}
    for comp in sorted({m["competition_id"] for m in matches}):
        tag, prefix = SCOPES[comp]
        dates = [
            epoch(m["scheduled_kickoff_utc"])
            for m in matches
            if m["competition_id"] == comp
        ]

        def iso(ts):
            return datetime.fromtimestamp(ts, timezone.utc).isoformat()

        for offset in range(0, 100000, 100):
            params = dict(
                tag_id=tag,
                limit=100,
                offset=offset,
                order="id",
                ascending="true",
                end_date_min=iso(min(dates) - 7 * 86400),
                end_date_max=iso(max(dates) + 7 * 86400),
            )
            page, meta = client.get(GAMMA + "events?" + urlencode(params))
            if not isinstance(page, list):
                raise ValueError("Expected event catalog array")
            sources.append(meta)
            if page and all(str(e["id"]) in events for e in page):
                raise ValueError("Repeated catalog page")
            for event in page:
                if (
                    str(event["id"]) in events
                    and events[str(event["id"])]["event"] != event
                ):
                    raise ValueError("Conflicting event copies")
                events[str(event["id"])] = dict(
                    event=event, competition_id=comp, catalog_sha256=meta["sha256"]
                )
            print(f"Catalog {prefix}: {offset + len(page)} events", flush=True)
            if len(page) < 100:
                break
        else:
            raise ValueError("Catalog pagination incomplete")
    return list(events.values()), sources


def resolve(matches, entries):
    """Resolve each market event to at most one FIFA fixture, then each contract."""
    index = defaultdict(list)
    identities = {}
    for match in matches:
        comp = match["competition_id"]
        for side in ("home", "away"):
            key = comp, norm(match[side + "_team_name"])
            tid = match[side + "_team_id"]
            if key in identities and identities[key] != tid:
                raise ValueError("Team alias collision: " + str(key))
            identities[key] = tid
        pair = frozenset(norm(match[s + "_team_name"]) for s in ("home", "away"))
        if len(pair) != 2:
            raise ValueError("Ambiguous fixture team names")
        index[(comp, pair)].append(match)
    linked, audit = defaultdict(list), []
    owners = {}
    for entry in entries:
        event, comp = entry["event"], entry["competition_id"]
        teams = event["title"].split(" - ")[0].split(" vs. ")
        record = dict(
            polymarket_event_id=str(event["id"]),
            slug=event["slug"],
            title=event["title"],
            competition_id=comp,
            catalog_sha256=entry["catalog_sha256"],
        )
        # League tags alone can contain wrongly tagged events from other leagues.
        if len(teams) != 2 or not event["slug"].startswith(SCOPES[comp][1] + "-"):
            audit.append(
                record | dict(status="excluded", reason="not_a_supported_fixture_title")
            )
            continue
        reason = event_provenance(comp, event)
        if reason:
            audit.append(record | dict(status="excluded", reason=reason))
            continue
        candidates = [
            m
            for m in index[(comp, frozenset(map(norm, teams)))]
            if any(
                time_match(m, event, k.get("gameStartTime")) for k in event["markets"]
            )
        ]
        if len(candidates) != 1:
            audit.append(
                record
                | dict(
                    status="excluded",
                    reason="no_unique_team_time_match",
                    candidate_match_ids=[m["match_id"] for m in candidates],
                )
            )
            continue
        match = candidates[0]
        accepted = 0
        for market in event["markets"]:
            reason = contract_provenance(comp, event, market)
            if reason:
                audit.append(
                    record
                    | dict(
                        status="excluded_contract",
                        market_id=str(market["id"]),
                        match_id=match["match_id"],
                        reason=reason,
                    )
                )
                continue
            if not time_match(match, event, market.get("gameStartTime")):
                audit.append(
                    record
                    | dict(
                        status="excluded_contract",
                        market_id=str(market["id"]),
                        reason="contract_start_mismatch",
                    )
                )
                continue
            mid = str(market["id"])
            if mid in owners:
                if owners[mid] != match["match_id"]:
                    raise ValueError("Market maps to multiple FIFA fixtures: " + mid)
                prior = next(
                    i
                    for i in linked[match["match_id"]]
                    if str(i["market"]["id"]) == mid
                )
                if prior["market"] != market or prior["event"]["id"] != event["id"]:
                    raise ValueError("Conflicting duplicate market record: " + mid)
                continue
            owners[mid] = match["match_id"]
            linked[match["match_id"]].append(
                dict(
                    market=market,
                    event=event,
                    catalog_sha256=entry["catalog_sha256"],
                    kickoff_delta_seconds=epoch(market["gameStartTime"])
                    - epoch(match["scheduled_kickoff_utc"]),
                )
            )
            accepted += 1
        audit.append(
            record
            | dict(status="mapped", match_id=match["match_id"], contracts=accepted)
        )
    remove_collisions(linked, audit)
    return linked, audit


def hda_outcome(match, market):
    if market.get("sportsMarketType") not in (None, "moneyline"):
        return None
    if array(market.get("outcomes", "[]")) != ["Yes", "No"]:
        return None
    question = market["question"]
    win = re.fullmatch(r"Will (.+) win on \d{4}-\d{2}-\d{2}\?", question)
    if win:
        for side in ("home", "away"):
            if norm(win[1]) == norm(match[side + "_team_name"]):
                return side
    draw = re.fullmatch(r"Will (.+) vs\. (.+) end in a draw\?", question)
    if draw and {norm(x) for x in draw.groups()} == {
        norm(match[s + "_team_name"]) for s in ("home", "away")
    }:
        return "draw"
    return None


def select_hda(match, linked):
    by_outcome = defaultdict(list)
    for item in linked:
        outcome = hda_outcome(match, item["market"])
        if outcome:
            by_outcome[outcome].append(item)
    if any(len(by_outcome[s]) != 1 for s in ("home", "draw", "away")):
        raise ValueError("no_unique_complete_hda_triplet")
    ordered = [by_outcome[s][0] for s in ("home", "draw", "away")]
    if len({i["event"]["id"] for i in ordered}) != 1:
        raise ValueError("hda_contracts_in_different_events")
    primary = re.fullmatch(
        r"(?:epl|fifwc)-[a-z0-9]+-[a-z0-9]+-(\d{4}-\d{2}-\d{2})",
        ordered[0]["event"]["slug"],
    )
    if primary is None:
        raise ValueError("not_primary_fixture_moneyline")
    for side, item in zip(("home", "draw", "away"), ordered):
        market = item["market"]
        desc = (
            market.get("description", "").casefold().replace("“", '"').replace("”", '"')
        )
        explicit = (
            "this market refers only to the outcome within the first 90 minutes of regular play plus stoppage time."
            in desc
        )
        if re.search(r"(?:includes?|including)\s+(?:extra time|overtime|penalt)", desc):
            raise ValueError("conflicting_regulation_rules")
        if side != "draw":
            if not market["question"].endswith("win on " + primary[1] + "?"):
                raise ValueError("contract_question_date_mismatch")
            subjects = re.findall(
                r'if ([^\n]+?) wins, this market will resolve to "yes"\.', desc
            )
            if len(subjects) != 1 or norm(subjects[0]) != norm(
                match[side + "_team_name"]
            ):
                raise ValueError("contract_rule_subject_mismatch")
        elif (
            'if the game ends in a draw, this market will resolve to "yes".' not in desc
        ):
            raise ValueError("contract_rule_subject_mismatch")
        # Legacy EPL draw contracts omit the duration clause. EPL has no ET;
        # companion win contracts must explicitly specify regulation time.
        legacy_draw = (
            side == "draw"
            and match["competition_id"] == "2000000000"
            and "if the game ends in a draw" in desc
        )
        if not explicit and not legacy_draw:
            raise ValueError("unverified_regulation_rules")
        tokens = array(market.get("clobTokenIds", "[]"))
        if not isinstance(tokens, list) or len(tokens) != 2 or not all(tokens):
            raise ValueError("missing_binary_tokens")
        item["outcome"] = side
        item["token_id"] = str(tokens[0])
        item["rule_basis"] = (
            "explicit_90_minutes" if explicit else "epl_draw_companion_rules"
        )
    return ordered


def validated_history(payload):
    history = payload.get("history")
    if not isinstance(history, list) or not history:
        raise ValueError("empty_history")
    times = [p["t"] for p in history]
    if times != sorted(set(times)):
        raise ValueError("unordered_or_duplicate_history")
    if any(
        not isinstance(p["t"], (int, float))
        or not math.isfinite(p["t"])
        or not isinstance(p["p"], (int, float))
        or not math.isfinite(p["p"])
        or not 0 <= p["p"] <= 1
        for p in history
    ):
        raise ValueError("invalid_history")
    return history


def prior_price(history, timestamp, max_age=90):
    times = [p["t"] for p in history]
    at = bisect_left(times, timestamp) - 1
    if at < 0:
        return None, None, None, False
    point = history[at]
    age = timestamp - point["t"]
    return point["p"], float(point["t"]), float(age), age <= max_age


ODDS = pa.struct(
    [
        ("outcome", pa.string()),
        ("market_id", pa.string()),
        ("token_id", pa.string()),
        ("price", pa.float64()),
        ("price_timestamp", pa.float64()),
        ("age_seconds", pa.float64()),
        ("fresh", pa.bool_()),
    ]
)
MARKET = pa.struct(
    [
        ("outcome", pa.string()),
        ("market_id", pa.string()),
        ("token_id", pa.string()),
        ("polymarket_event_id", pa.string()),
        ("slug", pa.string()),
        ("rule_basis", pa.string()),
        ("catalog_sha256", pa.string()),
        ("history_sha256", pa.string()),
        ("condition_id", pa.string()),
        ("token_registry_sha256", pa.list_(pa.string())),
        ("kickoff_delta_seconds", pa.float64()),
    ]
)
CONTRACTS = pa.schema(
    [
        ("match_id", pa.string()),
        ("competition_id", pa.string()),
        ("market_id", pa.string()),
        ("polymarket_event_id", pa.string()),
        ("slug", pa.string()),
        ("question", pa.string()),
        ("sports_market_type", pa.string()),
        ("hda_outcome", pa.string()),
        ("kickoff_delta_seconds", pa.float64()),
        ("catalog_sha256", pa.string()),
        ("source_market_json", pa.string()),
        ("condition_id", pa.string()),
        ("series_id", pa.string()),
        ("contract_policy_version", pa.string()),
        ("token_registry_sha256", pa.list_(pa.string())),
    ]
)


def run(args):
    source = resolve_snapshot(args.dataset)
    verify(source)
    mt, et = [pq.read_table(source / (n + ".parquet")) for n in ("matches", "events")]
    matches, events = mt.to_pylist(), et.to_pylist()
    grouped = defaultdict(list)
    for event in events:
        grouped[event["match_id"]].append(event)
    client = MarketClient(args.cache, offline=args.offline, refresh=args.refresh)
    entries, sources = catalog(client, matches)
    linked, event_audit = resolve(matches, entries)
    sources.extend(certify(client, linked, event_audit, args.workers))
    version = (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        + "-"
        + uuid.uuid4().hex[:8]
    )
    out = args.output / "snapshots" / version
    out.mkdir(parents=True)
    contract_rows = []
    for match in matches:
        for item in linked[match["match_id"]]:
            market = item["market"]
            contract_rows.append(
                dict(
                    match_id=match["match_id"],
                    competition_id=match["competition_id"],
                    market_id=str(market["id"]),
                    polymarket_event_id=str(item["event"]["id"]),
                    slug=item["event"]["slug"],
                    question=market["question"],
                    sports_market_type=market.get("sportsMarketType"),
                    hda_outcome=hda_outcome(match, market),
                    kickoff_delta_seconds=item["kickoff_delta_seconds"],
                    catalog_sha256=item["catalog_sha256"],
                    source_market_json=json.dumps(market, ensure_ascii=False),
                    condition_id=market["conditionId"],
                    series_id=SERIES[match["competition_id"]],
                    contract_policy_version=POLICY_VERSION,
                    token_registry_sha256=item["token_registry_sha256"],
                )
            )
    # Audit catalog is broader than the final H/D/A replay/forecast tables.
    pq.write_table(
        pa.Table.from_pylist(contract_rows, schema=CONTRACTS),
        out / "fixture-contracts.parquet",
        compression="zstd",
    )
    write_json(out / "catalog-resolution.json", event_audit)

    def one(match):
        mid = match["match_id"]
        audit = dict(
            match_id=mid,
            competition_id=match["competition_id"],
            fixture=match["home_team_name"] + " vs. " + match["away_team_name"],
            fixture_contracts=len(linked[mid]),
            status="excluded",
            reason=None,
            markets=[],
            sources=[],
            settlement_agrees=None,
            usable_events=0,
        )
        output_events = []
        try:
            chosen = select_hda(match, linked[mid])
            ends = [
                e
                for e in grouped[mid]
                if e["period_code"] == 5 and e["event_kind"] == "period_end"
            ]
            starts = [
                e
                for e in grouped[mid]
                if e["period_code"] == 3 and e["event_kind"] == "period_start"
            ]
            if len(ends) != 1 or len(starts) != 1 or match["timeline_status"] != "ok":
                raise ValueError("invalid_regulation_boundaries")
            kickoff, terminal = (
                epoch(starts[0]["event_time_utc"]),
                epoch(ends[0]["event_time_utc"]),
            )
            histories = []
            for item in chosen:
                params = dict(
                    market=item["token_id"],
                    startTs=int(
                        min(kickoff, epoch(match["scheduled_kickoff_utc"])) - 3600
                    ),
                    endTs=int(terminal + 120),
                    fidelity=1,
                )
                payload, meta = client.get(
                    "https://clob.polymarket.com/prices-history?" + urlencode(params)
                )
                audit["sources"].append(meta)
                histories.append(validated_history(payload))
                audit["markets"].append(
                    {
                        k: item[k]
                        for k in (
                            "outcome",
                            "token_id",
                            "rule_basis",
                            "catalog_sha256",
                            "kickoff_delta_seconds",
                        )
                    }
                    | dict(
                        market_id=str(item["market"]["id"]),
                        polymarket_event_id=str(item["event"]["id"]),
                        slug=item["event"]["slug"],
                        history_sha256=meta["sha256"],
                        condition_id=item["market"]["conditionId"],
                        token_registry_sha256=item["token_registry_sha256"],
                    )
                )
            for event in grouped[mid]:
                t = epoch(event["event_time_utc"]) if event["event_time_utc"] else None
                in_regulation = (
                    t is not None
                    and kickoff <= t <= terminal
                    and event["period_code"] in (3, 5)
                )
                odds = []
                for market, history in zip(audit["markets"], histories):
                    p, pt, age, fresh = (
                        prior_price(history, t, args.max_age)
                        if in_regulation
                        else (None, None, None, False)
                    )
                    odds.append(
                        {k: market[k] for k in ("outcome", "market_id", "token_id")}
                        | dict(
                            price=p, price_timestamp=pt, age_seconds=age, fresh=fresh
                        )
                    )
                usable = in_regulation and all(p["fresh"] for p in odds)
                output_events.append(event | dict(market_odds=odds, odds_usable=usable))
                audit["usable_events"] += int(usable)
            if not audit["usable_events"]:
                raise ValueError("no_simultaneously_fresh_in_game_odds")
            # Diagnostic only: never use settlement to select an identity or cohort.
            hg, ag = match["regulation_home_score"], match["regulation_away_score"]
            if hg is not None and ag is not None:
                expected = [int(hg > ag), int(hg == ag), int(hg < ag)]
                settled = [
                    array(i["market"].get("outcomePrices", "[]")) for i in chosen
                ]
                if all(
                    isinstance(p, list)
                    and len(p) == 2
                    and str(p[0]) in ("0", "1", "0.0", "1.0")
                    for p in settled
                ):
                    audit["settlement_agrees"] = [
                        float(p[0]) for p in settled
                    ] == expected
            audit.update(status="included", regulation_end_timestamp=terminal)
        except (OSError, URLError) as error:
            audit["reason"] = "request_error: " + str(error)
            output_events = []
        except (ValueError, KeyError, TypeError) as error:
            audit["reason"] = str(error)
            output_events = []
        return match, audit, output_events

    final_matches, final_events, audits = [], [], []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for i, (match, audit, rows) in enumerate(pool.map(one, matches)):
            audits.append(audit)
            sources.extend(audit["sources"])
            if audit["status"] == "included":
                final_matches.append(match | dict(prediction_markets=audit["markets"]))
                final_events.extend(rows)
            if (i + 1) % 20 == 0 or audit["status"] != "included":
                print(
                    f"Resolved {i+1}/{len(matches)}; included {len(final_matches)}; {audit['fixture']}: {audit['reason'] or 'ok'}",
                    flush=True,
                )
    schemas = {
        "matches": mt.schema.append(pa.field("prediction_markets", pa.list_(MARKET))),
        "events": et.schema.append(pa.field("market_odds", pa.list_(ODDS))).append(
            pa.field("odds_usable", pa.bool_())
        ),
    }
    files = {}
    for name, rows in (("matches", final_matches), ("events", final_events)):
        table = pa.Table.from_pylist(rows, schema=schemas[name])
        table.validate(full=True)
        path = out / (name + ".parquet")
        pq.write_table(table, path, compression="zstd")
        files[name] = dict(
            rows=len(rows), sha256=hashlib.sha256(path.read_bytes()).hexdigest()
        )
    files["fixture-contracts"] = dict(
        rows=len(contract_rows),
        sha256=hashlib.sha256(
            (out / "fixture-contracts.parquet").read_bytes()
        ).hexdigest(),
    )
    coverage = {}
    for comp in SCOPES:
        subset = [a for a in audits if a["competition_id"] == comp]
        coverage[comp] = dict(
            raw_matches=len(subset),
            fixture_matched=sum(a["fixture_contracts"] > 0 for a in subset),
            fixture_contracts=sum(a["fixture_contracts"] for a in subset),
            final_matches=sum(a["status"] == "included" for a in subset),
            usable_events=sum(
                a["usable_events"] for a in subset if a["status"] == "included"
            ),
            exclusions=dict(
                Counter(a["reason"] for a in subset if a["status"] != "included")
            ),
        )
    manifest = dict(
        dataset_version=version,
        resolver_version=VERSION,
        contract_policy_version=POLICY_VERSION,
        approved_series=SERIES,
        contract_exclusions=dict(
            Counter(
                a["reason"] for a in event_audit if a["status"] == "excluded_contract"
            )
        ),
        source_snapshot=str(source.resolve()),
        source_manifest_sha256=hashlib.sha256(
            (source / "manifest.json").read_bytes()
        ).hexdigest(),
        max_price_age_seconds=args.max_age,
        history_scope="regulation_home_draw_away_yes_tokens",
        coverage=coverage,
        files=files,
        raw_sources=sources,
        aliases=ALIASES,
        time_overrides=TIME_OVERRIDES,
        settlement_disagreements=[
            a["match_id"] for a in audits if a["settlement_agrees"] is False
        ],
        limitations=[
            "Occurrence timestamps are not first-seen publication timestamps.",
            "Sampled prices are not executable bid/ask quotes; no interpolation or normalization.",
            "fixture-contracts is fixture linkage only; props have no player/entity labels or price histories.",
            "All replay events of retained matches are preserved; filter odds_usable for fresh regulation observations.",
        ],
    )
    write_json(out / "match-resolution.json", audits)
    write_json(out / "manifest.json", manifest)
    if any((a["reason"] or "").startswith("request_error:") for a in audits):
        raise RuntimeError(
            "Request failures; candidate snapshot retained, current pointer unchanged. Rerun to resume."
        )
    write_json(args.output / "current.json", dict(dataset_version=version))
    print(
        json.dumps(
            dict(
                snapshot=str(out.resolve()),
                coverage=coverage,
                settlement_disagreements=manifest["settlement_disagreements"],
            ),
            indent=2,
        )
    )
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path("data/processed/fifa"))
    parser.add_argument(
        "--output", type=Path, default=Path("data/processed/fifa-markets")
    )
    parser.add_argument("--cache", type=Path, default=Path("data/raw/polymarket"))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-age", type=int, default=90)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args(argv)
    if args.workers < 1 or args.max_age < 1:
        parser.error("--workers and --max-age must be positive")
    run(args)


if __name__ == "__main__":
    main()
