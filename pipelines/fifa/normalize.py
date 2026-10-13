"""Conservative normalization; original records remain losslessly replayable."""

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import math
import re

from .client import dumps
from .schema import SCHEMA_VERSION, NORMALIZATION_VERSION

PHASES = {
    2: "pre_match",
    3: "first_half",
    4: "halftime",
    5: "second_half",
    7: "extra_time_first_half",
    8: "extra_time_break",
    9: "extra_time_second_half",
    10: "finished",
    11: "shootout",
    16: "break",
    17: "break",
}
KINDS = {
    0: "goal",
    39: "goal",
    41: "goal",
    34: "own_goal",
    1: "assist",
    12: "shot_attempt",
    33: "shot_attempt",
    49: "shot_attempt",
    17: "goal_prevention",
    57: "goal_prevention",
    16: "corner",
    18: "foul",
    15: "offside",
    6: "penalty_awarded",
    72: "penalty_conceded",
    51: "penalty_missed",
    60: "penalty_missed",
    65: "penalty_missed",
    2: "yellow_card",
    3: "red_card",
    4: "second_yellow",
    5: "substitution",
    71: "var",
    7: "period_start",
    8: "period_end",
    26: "match_end",
    9: "pause",
    83: "pause",
    85: "pause",
    10: "resume",
    78: "resume",
    79: "coin_toss",
    80: "coin_toss",
    81: "coin_toss",
    82: "coin_toss",
}
PLAY_PERIODS = {3, 5, 7, 9, 11}
PLACEHOLDERS = {
    "yellow_card",
    "red_card",
    "second_yellow",
    "substitution",
    "period_start",
    "period_end",
    "match_end",
    "pause",
    "resume",
}


def sid(value):
    return None if value is None else str(value)


def stamp(value):
    try:
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value.astimezone(timezone.utc) if value.tzinfo else None
    except (TypeError, AttributeError, ValueError):
        return None


def integer(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def numeric(value):
    return (
        float(value)
        if isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        else None
    )


def label(items):
    return next(
        (
            x["Description"]
            for x in (items or [])
            if str(x.get("Locale", "")).lower().startswith("en")
        ),
        None,
    )


def score(event):
    value = integer(event.get("HomeGoals")), integer(event.get("AwayGoals"))
    return value if all(x is not None and x >= 0 for x in value) else None


def unique_score(events):
    values = {score(e) for e in events}
    return next(iter(values)) if len(values) == 1 and None not in values else None


def normalize(match, calendar_meta, timeline, timeline_meta, version, error=None):
    mid = sid(match["IdMatch"])
    home, away = match.get("Home") or {}, match.get("Away") or {}
    properties = match.get("Properties") or {}
    common = dict(
        source="fifa",
        match_id=mid,
        competition_id=sid(match["IdCompetition"]),
        season_id=sid(match["IdSeason"]),
        dataset_version=version,
        schema_version=SCHEMA_VERSION,
        normalization_version=NORMALIZATION_VERSION,
    )
    flags = []
    m = dict(
        common,
        competition_name=label(match.get("CompetitionName")) or "",
        season_name=label(match.get("SeasonName")) or "",
        scheduled_kickoff_utc=stamp(match.get("Date")),
        home_team_id=sid(home.get("IdTeam")),
        away_team_id=sid(away.get("IdTeam")),
        home_team_name=label(home.get("TeamName")),
        away_team_name=label(away.get("TeamName")),
        home_placeholder=match.get("PlaceHolderA"),
        away_placeholder=match.get("PlaceHolderB"),
        matchday_raw=sid(match.get("MatchDay")),
        match_status_code=integer(match.get("MatchStatus")),
        # Only observed codes are normalized; do not guess undocumented statuses.
        match_status={0: "completed", 1: "scheduled"}.get(
            match.get("MatchStatus"), "unknown"
        ),
        calendar_home_score=integer(home.get("Score")),
        calendar_away_score=integer(away.get("Score")),
        winner_team_id=sid(match.get("Winner")),
        result_type_code=integer(match.get("ResultType")),
        ifes_match_id=sid(properties.get("IdIFES")),
        statsperform_match_id=sid(properties.get("IdStatsPerform")),
        timeline_status="fetch_error" if error else "ok",
        event_count=None,
        located_event_count=None,
        calendar_response_sha256=calendar_meta["sha256"],
        timeline_response_sha256=(timeline_meta or {}).get("sha256"),
        calendar_fetched_at_utc=stamp(calendar_meta.get("fetched_at_utc")),
        timeline_fetched_at_utc=stamp((timeline_meta or {}).get("fetched_at_utc")),
        quality_flags=flags,
        source_match_json=dumps(match),
    )
    for key in ("stage", "group"):
        m[key + "_id"] = sid(match.get("Id" + key.title()))
        m[key + "_name"] = label(match.get(key.title() + "Name"))
    for key in (
        "regulation_home_score",
        "regulation_away_score",
        "final_home_score",
        "final_away_score",
        "shootout_home_score",
        "shootout_away_score",
        "went_to_extra_time",
        "went_to_shootout",
    ):
        m[key] = None
    if error:
        flags.append("timeline_fetch_error")
        return m, []
    if not isinstance(timeline, dict) or not isinstance(timeline.get("Event"), list):
        raise ValueError("Timeline Event must be an array: " + mid)
    for raw_key, value in (
        ("IdMatch", mid),
        ("IdSeason", common["season_id"]),
        ("IdCompetition", common["competition_id"]),
    ):
        if sid(timeline.get(raw_key)) != value:
            raise ValueError("Timeline identity mismatch: " + mid + "/" + raw_key)
    raw = timeline["Event"]
    m["event_count"] = len(raw)
    if not raw:
        m.update(timeline_status="empty", located_event_count=0)
        if m["match_status"] == "completed":
            flags.append("completed_match_empty_timeline")
        return m, []
    starts = defaultdict(list)
    for event in raw:
        if event.get("Type") == 7 and stamp(event.get("Timestamp")):
            starts[event.get("Period")].append(stamp(event["Timestamp"]))
    event_rows, identities, previous = [], set(), None
    for index, e in enumerate(raw):
        if not isinstance(e, dict) or e.get("EventId") is None:
            raise ValueError("Missing event identity: " + mid)
        team, eid = sid(e.get("IdTeam")), sid(e["EventId"])
        identity = (team, eid)
        if identity in identities:
            raise ValueError(
                "Duplicate team/event identity: " + mid + "/" + str(identity)
            )
        identities.add(identity)
        code, period = integer(e.get("Type")), integer(e.get("Period"))
        kind, phase = KINDS.get(code, "unknown"), PHASES.get(period, "unknown")
        when = stamp(e.get("Timestamp"))
        ef = []
        if kind == "unknown":
            ef.append("unknown_event_type")
        if phase == "unknown":
            ef.append("unknown_period")
        if when is None:
            ef.append("invalid_timestamp")
        if previous and when and when < previous:
            ef.append("timestamp_reversal")
        if when:
            previous = when
        if (
            when
            and m["scheduled_kickoff_utc"]
            and not -600 <= (when - m["scheduled_kickoff_utc"]).total_seconds() <= 21600
        ):
            ef.append("timestamp_outside_match_window")
        if team is not None and team not in (m["home_team_id"], m["away_team_id"]):
            ef.append("foreign_team_id")
        x, y = numeric(e.get("PositionX")), numeric(e.get("PositionY"))
        if x is None or y is None:
            location = "missing"
        elif not (0 <= x <= 100 and 0 <= y <= 100):
            location = "out_of_range"
        elif x == y == 0:
            location = (
                "placeholder"
                if properties.get("IdStatsPerform") and kind in PLACEHOLDERS
                else "ambiguous_zero"
            )
        else:
            location = "present_unverified"
        if location in ("out_of_range", "ambiguous_zero"):
            ef.append(location + "_coordinates")
        clock = re.fullmatch(r"(\d+)'(?:\+(\d+)')?", e.get("MatchMinute") or "")
        elapsed = None
        anchors = set(starts[period])
        if period in PLAY_PERIODS and len(anchors) == 1 and when:
            elapsed = round((when - next(iter(anchors))).total_seconds() * 1000)
            if elapsed < 0:
                ef.append("event_before_period_start")
                elapsed = None
        row = dict(
            common,
            event_uid=hashlib.sha256(
                dumps(["fifa", mid, team, eid]).encode()
            ).hexdigest(),
            event_id=eid,
            source_index=index,
            event_time_utc=when,
            period_code=period,
            phase=phase,
            match_minute_raw=e.get("MatchMinute"),
            clock_minute=int(clock[1]) if clock else None,
            stoppage_minute=int(clock[2] or 0) if clock else None,
            period_elapsed_ms=elapsed,
            event_type_code=code,
            event_type_label=label(e.get("TypeLocalized")),
            event_kind=kind,
            event_description=label(e.get("EventDescription")),
            team_id=team,
            player_id=sid(e.get("IdPlayer")),
            sub_player_id=sid(e.get("IdSubPlayer")),
            sub_team_id=sid(e.get("IdSubTeam")),
            person_id=sid(e.get("IdPerson")),
            home_score=integer(e.get("HomeGoals")),
            away_score=integer(e.get("AwayGoals")),
            home_shootout_score=integer(e.get("HomePenaltyGoals")),
            away_shootout_score=integer(e.get("AwayPenaltyGoals")),
            x_raw=x,
            y_raw=y,
            location_status=location,
            goal_gate_x_raw=numeric(e.get("GoalGatePositionX")),
            goal_gate_y_raw=numeric(e.get("GoalGatePositionY")),
            foul_party_code=integer(e.get("FoulParty")),
            qualifiers_json=dumps(e["Qualifiers"])
            if e.get("Qualifiers") is not None
            else None,
            var_notification_json=dumps(e["VarNotificationData"])
            if e.get("VarNotificationData") is not None
            else None,
            quality_flags=ef,
            source_response_sha256=timeline_meta["sha256"],
            fetched_at_utc=stamp(timeline_meta.get("fetched_at_utc")),
            source_event_json=dumps(e),
        )
        event_rows.append(row)
    periods = {e.get("Period") for e in raw}
    m.update(
        went_to_extra_time=bool(periods & {7, 9}),
        went_to_shootout=11 in periods,
        located_event_count=sum(
            e["location_status"] == "present_unverified" for e in event_rows
        ),
    )
    regulation = unique_score(
        [e for e in raw if e.get("Type") == 8 and e.get("Period") == 5]
    )
    if regulation:
        m["regulation_home_score"], m["regulation_away_score"] = regulation
    else:
        flags.append("ambiguous_regulation_score")
    final_records = [e for e in raw if e.get("Type") == 26]
    final = unique_score(final_records)
    if m["match_status"] == "completed":
        if final and final == (m["calendar_home_score"], m["calendar_away_score"]):
            m["final_home_score"], m["final_away_score"] = final
        else:
            flags.append("final_score_conflict_or_missing")
    if 11 in periods:
        tallies = {
            (integer(e.get("HomePenaltyGoals")), integer(e.get("AwayPenaltyGoals")))
            for e in final_records
        }
        if len(tallies) == 1 and None not in next(iter(tallies)):
            tally = next(iter(tallies))
            calendar_tally = (
                integer(match.get("HomeTeamPenaltyScore")),
                integer(match.get("AwayTeamPenaltyScore")),
            )
            if tally == calendar_tally:
                m["shootout_home_score"], m["shootout_away_score"] = tally
            else:
                flags.append("shootout_score_conflict")
        else:
            flags.append("ambiguous_shootout_score")
    if m["match_status"] == "completed":
        for p in sorted(({3, 5} | (periods & {7, 9, 11}))):
            for code in (7, 8):
                if not any(e.get("Type") == code and e.get("Period") == p for e in raw):
                    flags.append(
                        f'missing_period_{p}_{"start" if code == 7 else "end"}'
                    )
    flags.extend(sorted({f for e in event_rows for f in e["quality_flags"]}))
    return m, event_rows
