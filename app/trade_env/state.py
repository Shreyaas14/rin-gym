"""What is known at a decision time: delayed events, as-of prices, nothing later."""

import bisect
import math

from .config import OUTCOMES
from .reward import legal_actions

SIDES = ("home", "away")
OTHER = {"home": "away", "away": "home"}
COUNTS = (
    ("corners", ("corner",)),
    ("fouls", ("foul",)),
    ("offsides", ("offside",)),
    ("yellow", ("yellow_card",)),
    ("red", ("red_card", "second_yellow")),
    ("subs", ("substitution",)),
)
HIDDEN_KINDS = ("coin_toss",)
NEUTRAL_KINDS = ("period_start", "period_end", "match_end")


def sample_time(sample):
    return sample[0]


def asof(series, t, max_age):
    """Last sample at or before t, or None when it is older than max_age."""
    i = bisect.bisect_right(series, t, key=sample_time)
    if i == 0 or t - series[i - 1][0] > max_age:
        return None
    return series[i - 1]


def first_from(series, t, max_wait):
    """First sample at or after t, or None when it is later than t + max_wait."""
    i = bisect.bisect_left(series, t, key=sample_time)
    if i == len(series) or series[i][0] > t + max_wait:
        return None
    return series[i]


def coherent(prices, config):
    """Whether three YES prices sum to 1 within max_sum_dev. fsum and a hair of
    slack keep a quote that sits exactly on the limit, on every Python version."""
    return abs(math.fsum(prices.values()) - 1) <= config["max_sum_dev"] + 1e-9


def shown_prices(prices, t, config):
    """(prices, None), or (None, reason) when the quotes are stale or incoherent."""
    shown = {}
    for outcome in OUTCOMES:
        sample = asof(prices.get(outcome) or [], t, config["price_max_age_s"])
        if sample is None:
            return None, "stale_price"
        shown[outcome] = sample[1]
    if not coherent(shown, config):
        return None, "price_sum"
    return shown, None


def fill_prices(prices, t, limit, config):
    """Mid of the first sample after the fill delay, per outcome.

    The three must pass the same sum test as shown prices: a leg quoted off an
    empty book would otherwise pay out, or cost, tens of cents that never existed.
    """
    fills = {}
    for outcome in OUTCOMES:
        sample = first_from(
            prices.get(outcome) or [],
            t + config["fill_delay_s"],
            config["fill_max_wait_s"],
        )
        if sample is None:
            return None, "no_fill"
        if sample[0] > limit:
            return None, "fill_after_chunk_end"
        fills[outcome] = sample[1]
    if not coherent(fills, config):
        return None, "fill_sum"
    return fills, None


def score_of(events):
    # single rows can carry a stale score, so take the highest seen
    return {
        side: max((e[side + "_goals"] for e in events), default=0) for side in SIDES
    }


def shooter(event, split):
    """Side whose attempt on goal this row records, or None.

    Most feeds log a shot row for every attempt. A split feed logs one only for
    a miss: there a goal counts for its scorer, and a save or a block for the
    side it was made against.
    """
    kind, team = event["kind"], event.get("team")
    if kind == "shot_attempt" or (split and kind in ("goal", "penalty_missed")):
        return team
    if split and kind == "goal_prevention":
        return OTHER.get(team)
    return None


def counts(events, split=False):
    out = {side: {"shots": 0, **{name: 0 for name, _ in COUNTS}} for side in SIDES}
    for event in events:
        side = shooter(event, split)
        if side in out:
            out[side]["shots"] += 1
        if event.get("team") in out:
            for name, kinds in COUNTS:
                if event["kind"] in kinds:
                    out[event["team"]][name] += 1
    for side, goals in score_of(events).items():
        out[side] = {"goals": goals, **out[side]}
    return out


def event_rows(events, score, names):
    """Rows for the prompt; a goal is credited from the score change, not the team."""
    rows = []
    for event in events:
        after = {side: max(score[side], event[side + "_goals"]) for side in SIDES}
        note = None
        if after != score:
            scorers = [names[side] for side in SIDES if after[side] > score[side]]
            note = f"goal for {' and '.join(scorers)}, score {after['home']}-{after['away']}"
            score = after
        if event["kind"] in HIDDEN_KINDS and not note:
            continue
        rows.append(
            {
                "minute": event.get("minute"),
                "kind": event["kind"],
                "team": None
                if event["kind"] in NEUTRAL_KINDS
                else names.get(event.get("team")),
                "text": event.get("description"),
                "note": note,
            }
        )
    return rows


def price_history(prices, points, k, config):
    """Earlier rows of the price table, in time order. A row gets no prices
    when its quotes would not have been shown as the current price either."""
    rows = [(p["t"], p["label"]) for p in points[:k]]
    kickoff = points[k]["kickoff_t"]
    if config["show_kickoff_price"] and kickoff is not None:
        rows.append((kickoff, "kickoff"))
    return [
        {"label": label, "prices": shown_prices(prices, t, config)[0]}
        for t, label in sorted(rows, key=lambda row: row[0])
    ]


def build_state(record, points, k, position, shown, config):
    """State for decision points[k]. Reads only events and prices up to the cutoffs."""
    point = points[k]
    cutoff = point["t"] - config["event_delay_s"]
    since = points[k - 1]["t"] - config["event_delay_s"] if k else float("-inf")
    seen = sorted(
        (e for e in record["events"] if e["t"] <= cutoff),
        key=lambda e: (e["t"], e.get("seq", 0)),
    )
    earlier = [e for e in seen if e["t"] <= since]
    window = [e for e in seen if e["t"] > since]
    names = {"home": record["home"], "away": record["away"]}
    split = record.get("competition_id") in config["split_shot_competitions"]
    totals, before = counts(seen, split), counts(earlier, split)
    competition = config["competitions"].get(
        record.get("competition_id"), record.get("competition_id")
    )
    return {
        "competition": competition,
        "stage": record.get("stage_name"),
        "names": names,
        "phase": point["phase"],
        "first": k == 0,
        "score": score_of(seen),
        "totals": totals,
        "window": {
            side: {
                name: totals[side][name] - before[side][name] for name in totals[side]
            }
            for side in SIDES
        },
        "events": event_rows(window, score_of(earlier), names),
        "prices": shown,
        "history": price_history(record["prices"], points, k, config),
        "position": position,
        "legal_actions": legal_actions(position),
    }
