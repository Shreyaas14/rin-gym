"""Optional forecast targets and bounded scores; labels never enter observations."""

import math

from .config import OUTCOMES
from .parse import clean_probs, last_object

GOALS = ("home", "away", "none")


def targets(record, t, shown, config):
    # Import locally: state uses reward, which uses these scoring functions.
    from .state import asof, score_of

    end = t + config["forecast_horizon_s"]
    result = {"next_goal": None, "prices": None}
    if end > record["periods"]["h2_end"]:
        return result  # censored horizons are not settlement-price forecasts
    prior = score_of([e for e in record["events"] if e["t"] <= t])
    later = sorted({e["t"] for e in record["events"] if t < e["t"] <= end})
    result["next_goal"] = "none"
    for at in later:
        after = score_of([e for e in record["events"] if e["t"] <= at])
        sides = [side for side in ("home", "away") if after[side] > prior[side]]
        if sides:
            # Score changes credit own goals correctly; simultaneous ambiguous
            # changes cannot establish which side scored first.
            result["next_goal"] = sides[0] if len(sides) == 1 else None
            break
        prior = after
    samples = {
        o: asof(record["prices"][o], end, config["price_max_age_s"]) for o in OUTCOMES
    }
    if all(s is not None and s[0] > t for s in samples.values()):
        result["prices"] = {o: s[1] for o, s in samples.items()}
    return result


def predictions(text, tolerance):
    obj = last_object(text) if isinstance(text, str) else None
    if not isinstance(obj, dict):
        return None
    goal = obj.get("next_goal")
    prices = obj.get("future_prices")
    if not isinstance(goal, dict) or set(goal) != set(GOALS):
        return None
    normalized = clean_probs(
        {"home": goal["home"], "draw": goal["none"], "away": goal["away"]},
        tolerance,
    )
    if (
        normalized is None
        or not isinstance(prices, dict)
        or set(prices) != set(OUTCOMES)
    ):
        return None
    if any(
        type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1
        for v in prices.values()
    ):
        return None
    return {
        "next_goal": {
            "home": normalized["home"],
            "away": normalized["away"],
            "none": normalized["draw"],
        },
        "prices": prices,
    }


def brier(probs, outcome):
    return math.fsum((p - float(k == outcome)) ** 2 for k, p in probs.items())


def components(task, probs, forecast, config):
    """Baseline-relative proper scores. Invalid forecasts get worst-case scores.

    Equal averaging per match keeps the auxiliary budget independent of length.
    Availability is reported separately; a masked target contributes zero.
    """
    scoring = task["scoring"]
    labels = scoring["forecast"]
    result_loss = brier(probs, scoring["result"]) if probs else 2.0
    result_edge = brier(scoring["market_probs"], scoring["result"]) - result_loss
    goal_loss = price_loss = goal_edge = price_edge = persistence_loss = 0.0
    if labels["next_goal"] is not None:
        goal_loss = (
            brier(forecast["next_goal"], labels["next_goal"]) if forecast else 2.0
        )
        goal_edge = 2 / 3 - goal_loss  # uniform, fixed reference; not a fitted baseline
    if labels["prices"] is not None:
        price_loss = (
            (
                math.fsum(
                    (forecast["prices"][o] - labels["prices"][o]) ** 2 for o in OUTCOMES
                )
                / 3
            )
            if forecast
            else 1.0
        )
        persistence_loss = (
            math.fsum(
                (scoring["shown_prices"][o] - labels["prices"][o]) ** 2
                for o in OUTCOMES
            )
            / 3
        )
        price_edge = persistence_loss - price_loss
    n = scoring["forecast_points"]
    return {
        "result_brier": result_loss,
        "market_brier": brier(scoring["market_probs"], scoring["result"]),
        "price_persistence_mse": persistence_loss,
        "goal_brier": goal_loss,
        "price_mse": price_loss,
        "goal_available": float(labels["next_goal"] is not None),
        "price_available": float(labels["prices"] is not None),
        "result_reward": config["w_result"] * result_edge / n,
        "goal_reward": config["w_goal"] * goal_edge / n,
        "price_reward": config["w_price"] * price_edge / n,
    }


def validate_targets(tasks):
    """Reject malformed labels/normalization before opening a remote client."""
    counts = {}
    for task in tasks:
        counts.setdefault(task["match_id"], set()).add(task["t"])
    seen = {}
    for task in tasks:
        scoring = task["scoring"]
        label = scoring.get("forecast")
        if not isinstance(label, dict) or set(label) != {"next_goal", "prices"}:
            raise ValueError(f"{task['id']}: rebuild tasks with forecast_rewards=true")
        if label["next_goal"] is not None and label["next_goal"] not in GOALS:
            raise ValueError(f"{task['id']}: invalid goal target")
        prices = label["prices"]
        if prices is not None and (
            not isinstance(prices, dict)
            or set(prices) != set(OUTCOMES)
            or any(
                type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1
                for v in prices.values()
            )
        ):
            raise ValueError(f"{task['id']}: invalid price target")
        if scoring.get("forecast_points") != len(counts[task["match_id"]]):
            raise ValueError(f"{task['id']}: invalid forecast normalization")
        key = (task["match_id"], task["t"])
        if key in seen and seen[key] != label:
            raise ValueError(f"{task['id']}: position-dependent forecast target")
        seen[key] = label
