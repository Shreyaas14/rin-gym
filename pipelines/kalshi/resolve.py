"""Conservative contract identity resolution; no fuzzy names or volume ranking."""

from collections import Counter, defaultdict
from datetime import datetime
import re
from zoneinfo import ZoneInfo

from pipelines.fifa.markets import norm

POLICY = "kalshi-fifa-v2"
# Exact provider naming differences observed in the public catalogs.
ALIASES = {
    "manchesterutd": "manchesterunited",
    "nottingham": "nottinghamforest",
    "wolverhampton": "wolverhamptonwanderers",
    "manutd": "manchesterunited",
    "nottmforest": "nottinghamforest",
    "nottforest": "nottinghamforest",
    "southkorea": "korearepublic",
    "republicofkorea": "korearepublic",
    "unitedstatesofamerica": "usa",
    "bosniaandherz": "bosniaandherzegovina",
}
RULE = re.compile(
    r"(?:3rd Place )?(.+?) (?:vs\.?|at) (.+?) (?:professional )?(EPL|FIFA World Cup|World Cup)"
    r"(?: (?:Round Of (?:16|32)|Quarter[- ]?Finals?|Semi[- ]?Finals?|Final|Third Place))? "
    r"(?:soccer game|soccer match|soccer tie|match|game) originally scheduled for ([A-Za-z]+ \d{1,2}, \d{4})",
    re.I,
)

FAMILIES = {
    "GAME": "moneyline",
    "TOTAL": "totals",
    "TOTALGOAL": "totals",
    "SPREAD": "spreads",
    "BTTS": "both_teams_to_score",
    "SCORE": "soccer_exact_score",
    "1H": "soccer_halftime_result",
    "2H": "soccer_second_half_result",
    "1HTOTAL": "first_half_totals",
    "2HTOTAL": "second_half_totals",
    "1HBTTS": "both_teams_to_score_first_half",
    "2HBTTS": "both_teams_to_score_second_half",
    "TEAMTOTAL": "soccer_team_totals",
    "GOAL": "soccer_anytime_goalscorer",
    "FIRSTGOAL": "soccer_first_goalscorer",
    "CORNERS": "total_corners",
    "TCORNERS": "soccer_team_total_corners",
    "SHOT": "soccer_player_shots",
    "SOG": "soccer_player_shots_on_target",
    "SAVE": "soccer_player_goalkeeper_saves",
    "AST": "soccer_player_assists",
    "SOA": "soccer_player_goals_plus_assists",
    "ADVANCE": "soccer_team_to_advance",
}


def team(value):
    value = norm(value)
    return ALIASES.get(value, value)


def family(series):
    prefix = "KXEPL" if series.startswith("KXEPL") else "KXWC"
    return FAMILIES.get(series[len(prefix) :], "kalshi_" + series.lower())


def fixture_identity(market):
    # Match the explicit fixture clause after `the`, not an earlier `at least`
    # threshold or `score at the end` phrase in a prop's settlement condition.
    clauses = re.split(r"\bthe\s+", market.get("rules_primary", ""), flags=re.I)
    found = next((m for clause in reversed(clauses) if (m := RULE.match(clause))), None)
    if not found:
        return None
    a, b, competition, date = found.groups()
    a = re.split(r"\b(?:the|in)\s+", a, flags=re.I)[-1].strip()
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            date = datetime.strptime(date, fmt).date()
            break
        except ValueError:
            pass
    else:
        return None
    return (
        frozenset((team(a), team(b))),
        "2000000000" if competition.upper() == "EPL" else "17",
        date,
    )


def resolve(matches, entries):
    lookup = defaultdict(list)
    for match in matches:
        pair = frozenset(team(match[k]) for k in ("home_team_name", "away_team_name"))
        dt = match["scheduled_kickoff_utc"]
        for date in {dt.date(), dt.astimezone(ZoneInfo("America/New_York")).date()}:
            lookup[pair, match["competition_id"], date].append(match)
    # Never pick a copy arbitrarily if the same ticker identifies conflicting metadata.
    copies = defaultdict(list)
    for entry in entries:
        copies[entry["market"].get("ticker")].append(entry)
    linked, audit = [], []
    for ticker, items in sorted(copies.items(), key=lambda p: str(p[0])):
        m = items[0]["market"]
        series = items[0]["series"]
        reason = None
        identity_fields = (
            "event_ticker",
            "title",
            "yes_sub_title",
            "rules_primary",
            "rules_secondary",
            "custom_strike",
            "floor_strike",
            "cap_strike",
        )
        if any(
            any(i["market"].get(k) != m.get(k) for k in identity_fields)
            for i in items[1:]
        ):
            reason = "conflicting_ticker_identity"
        event = m.get("event_ticker", "")
        if not reason and (
            not ticker
            or not (ticker == event or ticker.startswith(event + "-"))
            or not event.startswith(series + "-")
            or m.get("market_type") != "binary"
            or m.get("exchange_index", 0) != 0
            or not m.get("rules_primary")
            or not series.startswith(("KXEPL", "KXWC"))
        ):
            reason = "invalid_official_contract_identity"
        identity = fixture_identity(m) if not reason else None
        if not reason and not identity:
            reason = "no_explicit_fixture_in_rules"
        candidates = lookup.get(identity, []) if identity else []
        if not reason and len(candidates) != 1:
            reason = "ambiguous_fixture" if candidates else "no_matching_fixture"
        match = candidates[0] if len(candidates) == 1 else None
        outcome = None
        if not reason:
            required_prefix = (
                "KXEPL" if match["competition_id"] == "2000000000" else "KXWC"
            )
            if not series.startswith(required_prefix):
                reason = "wrong_competition_series"
            # Date corroborates rules; a copied team title alone is insufficient.
            stamp = re.match(r"^[^-]+-(\d{2}[A-Z]{3}\d{2})", event)
            try:
                stamp_date = (
                    datetime.strptime(stamp[1], "%y%b%d").date() if stamp else None
                )
            except ValueError:
                stamp_date = None
            if stamp_date != identity[2]:
                reason = "ticker_rule_date_mismatch"
        if not reason and family(series) == "moneyline":
            rules = m["rules_primary"].lower()
            if (
                "after 90 minutes plus stoppage time (does not include extra time or penalties)"
                not in rules
            ):
                reason = "not_regulation_moneyline"
            subject = re.sub(
                r"^Reg Time:\s*", "", m.get("yes_sub_title", ""), flags=re.I
            )
            if subject.casefold() in ("tie", "draw"):
                outcome = "draw"
            else:
                sides = [
                    side
                    for side in ("home", "away")
                    if team(subject) == team(match[side + "_team_name"])
                ]
                if len(sides) == 1:
                    outcome = sides[0]
            if outcome is None:
                reason = "unknown_moneyline_subject"
            expected = (
                "if "
                + subject.lower()
                + (" is the result of " if " is the result of " in rules else " wins ")
            )
            if not rules.startswith(expected):
                reason = "rule_subject_mismatch"
        item = dict(
            ticker=ticker,
            event_ticker=event,
            series=series,
            match_id=match["match_id"] if match else None,
            status="excluded" if reason else "mapped",
            reason=reason,
        )
        audit.append(item)
        if not reason:
            linked.append(
                dict(entry=items[0], match=match, hda_outcome=outcome, audit=item)
            )
    # Quarantine duplicate propositions and competing H/D/A event triplets.
    owners = defaultdict(list)
    for item in linked:
        m = item["entry"]["market"]
        key = (
            item["match"]["match_id"],
            item["entry"]["series"],
            re.sub(r"\s+", " ", m["rules_primary"].strip().lower()),
        )
        owners[key].append(item)
    for group in owners.values():
        if len(group) > 1:
            for item in group:
                item["audit"].update(status="excluded", reason="duplicate_proposition")
    hda = defaultdict(list)
    for item in linked:
        if item["hda_outcome"] and item["audit"]["status"] == "mapped":
            hda[item["match"]["match_id"]].append(item)
    for group in hda.values():
        if (
            Counter(i["hda_outcome"] for i in group)
            != Counter({"home": 1, "draw": 1, "away": 1})
            or len({i["entry"]["market"]["event_ticker"] for i in group}) != 1
        ):
            for item in group:
                item["audit"].update(
                    status="excluded", reason="ambiguous_or_incomplete_hda"
                )
    return [i for i in linked if i["audit"]["status"] == "mapped"], audit
