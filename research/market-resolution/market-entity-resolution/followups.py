"""Post-hoc diagnostics of two observed issues; not a held-out benchmark."""

import argparse
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from cases import CASES
from run import QUESTIONS, load_key, request_json


def normalize_instants(text):
    pattern = r"\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC(?:[+-]\d{2}:\d{2})?"

    def replace(match):
        value = match.group().replace(" UTC", "")
        if len(value) == 16:
            value += "+00:00"
        return datetime.fromisoformat(value).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    return re.sub(pattern, replace, text)


def win_payouts(rules):
    # Deliberately narrow parser for the synthetic two-outcome sports template.
    # Refuse unsupported text rather than silently supplying missing terms.
    normal, remainder = rules.split(" If canceled or not completed on that date, ", 1)
    if not normal.startswith("YES pays $1 iff ") or "; NO pays the remainder of $1." not in normal:
        raise ValueError("Unsupported normal payout template")
    normal_predicate = normal.removeprefix("YES pays $1 iff ").split(";", 1)[0]
    fallback, source = remainder.split(". The ", 1)
    if fallback == "both sides pay $0.50":
        cancellation = (0.5, 0.5)
    elif fallback == "YES pays $0 and NO pays $1":
        cancellation = (0, 1)
    else:
        raise ValueError("Unsupported cancellation payout template")
    return normal_predicate, source, [(1, 0), (0, 1), cancellation]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", required=True)
    args = parser.parse_args()
    cases = {case["id"]: case for case in CASES}
    timezone_case = cases["timezone_conversion"]
    cancellation = cases["cancellation_fallback"]
    normalized = {side: {**timezone_case[side], "rules": normalize_instants(timezone_case[side]["rules"])} for side in ("a", "b")}
    assert normalized["a"]["rules"] == normalized["b"]["rules"]
    a_predicate, a_source, a_payouts = win_payouts(cancellation["a"]["rules"])
    b_predicate, b_source, b_payouts = win_payouts(cancellation["b"]["rules"])
    assert a_predicate == b_predicate and a_source == b_source
    states = ("The game is completed and Boston wins", "The game is completed and Boston loses", "The game is canceled or not completed on the scheduled date")
    table = [{"scenario": state, "a_yes": a[0], "a_no": a[1], "b_yes": b[0], "b_no": b[1]} for state, a, b in zip(states, a_payouts, b_payouts)]
    deterministic = {
        "equivalent": all(a[0] == b[0] for a, b in zip(a_payouts, b_payouts)),
        "complementary": all(a[0] == b[1] for a, b in zip(a_payouts, b_payouts)),
    }
    assert deterministic == {"equivalent": False, "complementary": False}
    experiments = [
        {"id": "timezone_swapped", "gold": "equivalent", "a": timezone_case["b"], "b": timezone_case["a"]},
        {"id": "timezone_normalized", "gold": "equivalent", **normalized},
        {"id": "cancellation_swapped", "gold": "related", "a": cancellation["b"], "b": cancellation["a"]},
        {"id": "cancellation_payout_table", "gold": "related", "a": cancellation["a"], "b": cancellation["b"], "payout_table": table},
    ]
    key = load_key(args.env_file)
    if not key:
        raise SystemExit("TYPESAFE_API_KEY missing")
    output = []
    out = Path(__file__).resolve().parent
    for experiment in experiments:
        state = {"contract_a": experiment["a"], "contract_b": experiment["b"], "context": "Synthetic diagnostic contracts. Rules are complete unless explicitly marked as missing."}
        if "payout_table" in experiment:
            state["payout_table"] = experiment["payout_table"]
            state["payout_table_description"] = "Exhaustive payout table derived in code from these simplified sports rules; values are dollars per contract."
        response, elapsed = request_json("/v1/systemone", key, {"model": "jev-latest", "state": state, "questions": copy.deepcopy(QUESTIONS)})
        row = {"id": experiment["id"], "gold": experiment["gold"], "state": state, "response": response, "elapsed_seconds": elapsed}
        output.append(row)
        (out / "followup-results.json").write_text(json.dumps({"description": "Four post-hoc diagnostics chosen after inspecting the initial run; not held-out validation.", "deterministic_cancellation_checks": deterministic, "results": output}, indent=2) + "\n")
        print(json.dumps({"id": experiment["id"], "gold": experiment["gold"], "answers": response["answers"]}), flush=True)


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from None
