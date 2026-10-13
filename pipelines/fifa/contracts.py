"""Fail-closed provider provenance and contract identity checks.

These checks establish a consistent approved Polymarket listing, not a guarantee
against fraud or oracle disputes. Never infer trust from volume or winning price.
"""

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import json
import re
from urllib.parse import urlencode, urlsplit

POLICY_VERSION = "polymarket-contract-policy-v1"
SERIES = {"2000000000": "10188", "17": "11433"}
HOSTS = {
    "2000000000": {"www.premierleague.com", "premierleague.com"},
    "17": {"www.fifa.com", "fifa.com"},
}
HEX32 = re.compile(r"0x[0-9a-fA-F]{64}\Z")
REGISTRY = "https://data-api.polymarket.com/v2/tokens?"


def array(value):
    return json.loads(value) if isinstance(value, str) else value


def event_provenance(comp, event):
    if SERIES[comp] not in {str(s.get("id")) for s in event.get("series", [])}:
        return "unapproved_competition_series"
    if event.get("cyom") is not False:
        return "custom_or_unknown_event_origin"
    return None


def contract_provenance(comp, event, market):
    if market.get("approved") is not True or market.get("cyom") is not False:
        return "unapproved_or_custom_contract"
    if market.get("archived") is not False:
        return "archived_or_unknown_contract_state"
    if market.get("enableOrderBook") is not True:
        return "no_orderbook_contract"
    for key in ("conditionId", "questionID"):
        if not HEX32.fullmatch(market.get(key) or "") or int(market[key], 16) == 0:
            return "invalid_" + key
    for source in (event.get("resolutionSource"), market.get("resolutionSource")):
        if source:
            url = urlsplit(source)
            if (
                url.scheme != "https"
                or url.hostname not in HOSTS[comp]
                or url.username
                or url.password
            ):
                return "unexpected_resolution_source"
    if (
        not market.get("description")
        or not market.get("question")
        or not market.get("slug")
    ):
        return "missing_contract_terms"
    outcomes, tokens = array(market.get("outcomes")), array(market.get("clobTokenIds"))
    if (
        not isinstance(outcomes, list)
        or not isinstance(tokens, list)
        or len(outcomes) != 2
        or len(tokens) != 2
    ):
        return "no_complete_outcome_token_mapping"
    if (
        len(set(tokens)) != 2
        or len(set(outcomes)) != 2
        or any(
            not isinstance(t, str) or not t.isdecimal() or int(t) <= 0 for t in tokens
        )
    ):
        return "invalid_outcome_token_mapping"
    return None


def remove_collisions(linked, audit):
    """Quarantine every owner of a collision; never silently keep the first one."""
    indices = defaultdict(list)
    for mid, items in linked.items():
        for item in items:
            m = item["market"]
            identity = (mid, str(m["id"]))
            keys = [("condition", m["conditionId"].lower())]
            keys.extend(("token", t) for t in array(m["clobTokenIds"]))
            # Identical propositions under different IDs are ambiguous even when
            # both listings are approved. Distinct questions/lines remain separate.
            question = re.sub(r"[^\w]", "", m["question"].casefold())
            keys.append(("proposition", mid, question))
            for key in keys:
                indices[key].append(identity)
    rejected = {
        identity
        for owners in indices.values()
        if len(set(owners)) > 1
        for identity in owners
    }
    for mid, items in linked.items():
        kept = []
        for item in items:
            if (mid, str(item["market"]["id"])) in rejected:
                audit.append(
                    dict(
                        status="excluded_contract",
                        match_id=mid,
                        market_id=str(item["market"]["id"]),
                        reason="ambiguous_duplicate_contract_identity",
                    )
                )
            else:
                kept.append(item)
        linked[mid] = kept


def validate_registry(market, event, records):
    tokens, outcomes = array(market["clobTokenIds"]), array(market["outcomes"])
    for index, (token, outcome) in enumerate(zip(tokens, outcomes)):
        row = records.get(token)
        if row is None:
            return "token_not_in_registry"
        expected = dict(
            condition_id=market["conditionId"].lower(),
            module="v1_ctf",
            clob_index=index,
            outcome=outcome,
            opposite_token_id=tokens[1 - index],
            title=market["question"],
            market_slug=market["slug"],
            event_id=str(event["id"]),
            event_slug=event["slug"],
            neg_risk=market.get("negRisk"),
        )
        for key, value in expected.items():
            actual = row.get(key)
            if key == "event_id":
                actual = str(actual)
            if key == "condition_id" and isinstance(actual, str):
                actual = actual.lower()
            if actual != value:
                return "registry_mismatch_" + key
        if market.get("negRisk") is True and row.get(
            "neg_risk_market_id"
        ) != market.get("negRiskMarketID"):
            return "registry_mismatch_neg_risk_market_id"
    return None


def certify(client, linked, audit, workers=8):
    tokens = sorted(
        {
            t
            for items in linked.values()
            for item in items
            for t in array(item["market"]["clobTokenIds"])
        }
    )
    batches = [tokens[i : i + 50] for i in range(0, len(tokens), 50)]

    def fetch(batch):
        body, meta = client.get(REGISTRY + urlencode({"token_id": ",".join(batch)}))
        rows = body.get("data")
        if not isinstance(rows, list):
            raise ValueError("invalid_token_registry_response")
        ids = [r.get("token_id") for r in rows]
        if len(ids) != len(set(ids)) or set(ids) - set(batch):
            raise ValueError("unexpected_registry_tokens")
        return rows, meta

    records, sources, hashes = {}, [], {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for start in range(0, len(batches), workers * 2):
            for rows, meta in pool.map(fetch, batches[start : start + workers * 2]):
                sources.append(meta)
                for row in rows:
                    records[row["token_id"]] = row
                    hashes[row["token_id"]] = meta["sha256"]
            if start % (workers * 20) == 0:
                print(
                    f"Contract registry: {min(start+workers*2,len(batches))}/{len(batches)} batches",
                    flush=True,
                )
    for mid, items in linked.items():
        kept = []
        for item in items:
            reason = validate_registry(item["market"], item["event"], records)
            if reason:
                audit.append(
                    dict(
                        status="excluded_contract",
                        match_id=mid,
                        market_id=str(item["market"]["id"]),
                        reason=reason,
                    )
                )
            else:
                item["token_registry_sha256"] = sorted(
                    {hashes[t] for t in array(item["market"]["clobTokenIds"])}
                )
                kept.append(item)
        linked[mid] = kept
    return sources
