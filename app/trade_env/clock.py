"""Decision points of a match in epoch seconds."""

import re

HALF = re.compile(r"(h[12])\+(\d+)$")
HALF_NAMES = {"h1": "first half", "h2": "second half"}


def locate(name, periods, config):
    if name == "pre":
        t = periods["h1_start"] - config["pre_lead_s"]
        return {"t": t, "phase": "before kickoff", "label": "pre-match"}
    if name == "ht":
        return {"t": periods["h1_end"], "phase": "halftime", "label": "halftime"}
    found = HALF.match(name)
    if not found:
        raise ValueError("Unknown decision point: " + name)
    half, minutes = found.group(1), int(found.group(2))
    start = periods[half + "_start"]
    t = start + 60 * minutes
    if t >= periods[half + "_end"]:
        return None
    elapsed = int(t - start)
    clock = f"{elapsed // 60}:{elapsed % 60:02d}"
    return {
        "t": t,
        "phase": f"{HALF_NAMES[half]}, {clock} played",
        "label": f"{HALF_NAMES[half]} {clock}",
    }


def decision_points(periods, config):
    """Points in time order; a point at or after its period end is left out."""
    points = []
    for idx, name in enumerate(config["decisions"]):
        point = locate(name, periods, config)
        if point:
            kickoff = periods["h1_start"]
            points.append(
                {
                    "name": name,
                    "idx": idx,
                    **point,
                    "kickoff_t": kickoff if kickoff <= point["t"] else None,
                }
            )
    if any(a["t"] >= b["t"] for a, b in zip(points, points[1:])):
        raise ValueError("Decision points must be listed in time order")
    return points
