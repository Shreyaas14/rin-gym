"""Small synthetic Jev smoke test; no credentials are written to artifacts."""

import argparse
import http.client
import json
import os
from pathlib import Path
import re
import statistics
import time
import urllib.error
import urllib.request
from collections import Counter
from difflib import SequenceMatcher

from cases import CASES

CRITERIA = {
    "equivalent": "YES pays the same amount on both contracts in every scenario permitted by the supplied complete rules, including missing data, cancellation, and boundary cases.",
    "complementary": "YES on contract A has the same payout as NO on contract B in every permitted scenario, including fallback cases; the YES payouts are not equivalent.",
    "related": "The contracts concern the same underlying event or measurement, but an explicit difference permits different payouts and they are not exact complements.",
    "unrelated": "The underlying events or measured entities are different. Sharing a broad topic such as sports is not enough.",
    "insufficient": "Material settlement information is absent, so equivalence or complementarity cannot be established and no explicit mismatch already decides the relation.",
}
QUESTIONS = {
    "relation": {
        "type": "choice",
        "instructions": "Classify the payout relationship between the two supplied synthetic prediction-market contracts. Use the rules, not title resemblance. Check entity, event, units, comparison boundaries, source, observation period, data revision policy, and exception payouts. Normalize mathematically equivalent units and instants. Do not assume missing terms. Return insufficient when material terms are absent unless an explicit difference already rules out matching. Treat supplied complete simplified rules as authoritative; do not invent additional exchange rules.",
        "criteria": CRITERIA,
    },
    "same_yes_payout": {
        "type": "noul",
        "instructions": "Do the supplied rules establish that YES payouts are identical across all possible scenarios, including boundary and fallback cases? Missing material terms do not establish identical payouts.",
    },
    "explicit_divergence": {
        "type": "noul",
        "instructions": "Do explicitly stated differences permit a scenario where the YES payouts differ? Purely missing terms are not an explicitly stated difference.",
    },
}


def load_key(env_file):
    """Read only the named API key, never source a dotenv file as shell code."""
    if env_file is None:
        return os.environ.get("TYPESAFE_API_KEY")
    for line in Path(env_file).read_text().splitlines():
        match = re.match(r'^\s*(?:export\s+)?TYPESAFE_API_KEY\s*=\s*(.*?)\s*$', line)
        if match:
            value = match.group(1).strip()
            if value[:1] in ('"', "'") and value[-1:] == value[:1]:
                return value[1:-1]
            return value.split(" #", 1)[0].strip()
    return None


def request_json(path, key, payload=None):
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        "https://api.typesafe.ai" + path,
        data=body,
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    )
    started = time.perf_counter()
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = json.load(response)
            return data, time.perf_counter() - started
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise RuntimeError(f"TypeSafe HTTP status {exc.code}; response body suppressed") from None
        except (urllib.error.URLError, http.client.RemoteDisconnected, TimeoutError) as exc:
            if attempt == 2:
                raise RuntimeError(f"Network failure: {type(exc).__name__}; details suppressed") from None
        time.sleep(2 ** attempt)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true", help="Validate fixtures and run title-similarity baseline without loading a key or using the network")
    parser.add_argument("--env-file", help="Read TYPESAFE_API_KEY from this file only when a live run is authorized")
    parser.add_argument("--model", default="jev-latest")
    parser.add_argument("--resume", action="store_true", help="Reuse saved results for the same fixtures and questions")
    parser.add_argument("--output-dir", type=Path, help="Use a separate directory for another model run")
    args = parser.parse_args()
    out = args.output_dir or Path(__file__).resolve().parent
    out.mkdir(parents=True, exist_ok=True)
    ids = [case["id"] for case in CASES]
    assert len(ids) == len(set(ids)) == 24
    assert set(case["gold"] for case in CASES) == set(CRITERIA)
    baseline = []
    for case in CASES:
        similarity = SequenceMatcher(None, case["a"]["title"].lower(), case["b"]["title"].lower()).ratio()
        baseline.append({"id": case["id"], "similarity": similarity, "accept": similarity >= 0.8, "gold": case["gold"]})
    accepted = [row for row in baseline if row["accept"]]
    true_accepted = sum(row["gold"] == "equivalent" for row in accepted)
    summary = {
        "fixture_type": "hand-authored synthetic simplified contracts, not real exchange snapshots",
        "cases": len(CASES), "gold_counts": dict(Counter(case["gold"] for case in CASES)),
        "baseline_threshold": 0.8,
        "baseline_accepts": len(accepted), "baseline_false_accepts": len(accepted) - true_accepted,
        "baseline_equivalence_precision": true_accepted / len(accepted) if accepted else None,
        "baseline_equivalence_recall": true_accepted / sum(case["gold"] == "equivalent" for case in CASES),
        "limitations": "Constructed diagnostic sample; no production accuracy, calibration, or retrieval-recall claims. Threshold fixed before model evaluation. Gold labels authored by the experiment designer; not independently adjudicated.",
    }
    if args.resume and (out / "fixtures.json").exists():
        if json.loads((out / "fixtures.json").read_text()) != CASES:
            raise SystemExit("Saved fixtures differ; use a new output directory")
    (out / "fixtures.json").write_text(json.dumps(CASES, indent=2) + "\n")
    (out / "baseline.json").write_text(json.dumps(baseline, indent=2) + "\n")
    if args.offline:
        (out / "offline-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(json.dumps(summary, indent=2))
        return
    key = load_key(args.env_file)
    if not key:
        raise SystemExit("TYPESAFE_API_KEY is missing; no network request was made")
    models, _ = request_json("/v1/models", key)
    (out / "available-models.json").write_text(json.dumps(models, indent=2) + "\n")
    results = json.loads((out / "live-results.json").read_text()) if args.resume and (out / "live-results.json").exists() else []
    saved_ids = {row["id"] for row in results}
    if len(saved_ids) != len(results) or not saved_ids.issubset(ids):
        raise SystemExit("Saved results have unexpected or duplicate case IDs")
    metadata = {"requested_model": args.model, "questions": QUESTIONS}
    if args.resume and (out / "run-metadata.json").exists():
        if json.loads((out / "run-metadata.json").read_text()) != metadata:
            raise SystemExit("Saved model or questions differ; use a new output directory")
    (out / "run-metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    for index, case in enumerate(CASES, 1):
        if case["id"] in saved_ids:
            continue
        # Gold label and rationale are deliberately excluded from model input.
        payload = {"model": args.model, "state": {"contract_a": case["a"], "contract_b": case["b"], "context": "Synthetic diagnostic contracts. Rules are complete unless explicitly marked as missing."}, "questions": QUESTIONS}
        response, elapsed = request_json("/v1/systemone", key, payload)
        relation = response["answers"]["relation"]
        if relation["choice"] not in CRITERIA:
            raise RuntimeError("Unexpected relation option")
        row = {"id": case["id"], "gold": case["gold"], "elapsed_seconds": elapsed, "response": response}
        results.append(row)
        (out / "live-results.json").write_text(json.dumps(results, indent=2) + "\n")
        print(json.dumps({"case": case["id"], "completed": index, "total": len(CASES), "prediction": relation["choice"], "gold": case["gold"], "p_equivalent": relation["probabilities"]["equivalent"]}), flush=True)
    correct = sum(row["response"]["answers"]["relation"]["choice"] == row["gold"] for row in results)
    accept_ids = [row["id"] for row in results if row["response"]["answers"]["relation"]["probabilities"]["equivalent"] >= 0.99]
    accepted_rows = [row for row in results if row["id"] in accept_ids]
    summary.update({
        "model_ids": sorted(set(row["response"]["model"] for row in results)),
        "correct_relation_labels": correct, "relation_accuracy": correct / len(results),
        "acceptance_threshold": 0.99, "accepted_ids": accept_ids,
        "false_equivalence_accepts": sum(row["gold"] != "equivalent" for row in accepted_rows),
        "equivalence_precision": sum(row["gold"] == "equivalent" for row in accepted_rows) / len(accepted_rows) if accepted_rows else None,
        "equivalence_recall": sum(row["gold"] == "equivalent" for row in accepted_rows) / sum(case["gold"] == "equivalent" for case in CASES),
        "median_end_to_end_seconds": statistics.median(row["elapsed_seconds"] for row in results),
        "usage_totals": {field: sum(row["response"].get("usage", {}).get(field, 0) for row in results) for field in ("input_tokens", "output_tokens")},
        "request_questions": QUESTIONS,
    })
    (out / "live-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from None
