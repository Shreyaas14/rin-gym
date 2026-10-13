"""Read the model's answer: the last JSON object that is not part of its reasoning."""

import json
import math
import re

from .config import OUTCOMES

TAG = re.compile(r"<(/?)think>", re.I)


def objects(text):
    """(start, end, value) of every outermost JSON object, in order."""
    decoder = json.JSONDecoder()
    found, at = [], text.find("{")
    while at != -1:
        try:
            value, end = decoder.raw_decode(text, at)
        except (ValueError, RecursionError):
            end = at + 1
        else:
            found.append((at, end, value))
        at = text.find("{", end)
    return found


def last_object(text):
    """The answer, or None.

    Reasoning is everything up to the last </think> and everything after a
    <think> that is never closed; an object in there is a draft. A tag inside a
    JSON object is only text, so an answer may mention one in its reason.
    """
    found = objects(text)
    tags = [
        tag
        for tag in TAG.finditer(text)
        if not any(start < tag.start() < end for start, end, _ in found)
    ]
    closes = [tag.end() for tag in tags if tag.group(1)]
    begin = closes[-1] if closes else 0
    opens = [tag.start() for tag in tags if not tag.group(1) and tag.start() >= begin]
    limit = opens[0] if opens else len(text)
    answers = [value for start, end, value in found if begin <= start and end <= limit]
    return answers[-1] if answers else None


def clean_probs(probs, tol):
    """Three probabilities renormalised to sum to 1, or None."""
    if not isinstance(probs, dict) or set(probs) != set(OUTCOMES):
        return None
    values = [probs[o] for o in OUTCOMES]
    # the range test also turns away NaN, infinity and integers too big for a float
    if any(type(v) not in (int, float) or not 0 <= v <= 1 for v in values):
        return None
    total = math.fsum(values)
    if abs(total - 1) > tol + 1e-9:
        return None
    return {o: v / total for o, v in zip(OUTCOMES, values)}


def parse(completion, legal_actions, tol=0.05):
    out = {"ok": False, "error": None, "probs": None, "action": None, "reason": None}
    if not isinstance(completion, str):
        return {**out, "error": "no_json"}
    answer = last_object(completion)
    if answer is None:
        return {**out, "error": "no_json"}
    out["probs"] = clean_probs(answer.get("probs"), tol)
    action = answer.get("action")
    if isinstance(action, str) and action.strip() in legal_actions:
        out["action"] = action.strip()
    if isinstance(answer.get("reason"), str):
        out["reason"] = answer["reason"]
    if out["probs"] is None:
        return {**out, "error": "bad_probs"}
    if out["action"] is None:
        return {**out, "error": "illegal_action"}
    return {**out, "ok": True}
