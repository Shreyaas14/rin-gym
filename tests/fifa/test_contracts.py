import copy
import json
import unittest

from pipelines.fifa.contracts import (
    contract_provenance,
    event_provenance,
    validate_registry,
    certify,
)
from pipelines.fifa.markets import resolve, select_hda
from test_markets import fixture


def registry(market, event):
    ids = json.loads(market["clobTokenIds"])
    outcomes = json.loads(market["outcomes"])
    return {
        token: dict(
            token_id=token,
            condition_id=market["conditionId"],
            module="v1_ctf",
            clob_index=i,
            outcome_index=1 - i,
            outcome=outcomes[i],
            opposite_token_id=ids[1 - i],
            title=market["question"],
            market_slug=market["slug"],
            event_id=int(event["id"]),
            event_slug=event["slug"],
            neg_risk=False,
        )
        for i, token in enumerate(ids)
    }


class ProvenanceTests(unittest.TestCase):
    def test_approved_series_required_even_with_matching_title_and_slug(self):
        match, entry = fixture()
        entry["event"]["series"] = [{"id": "fake"}]
        linked, audit = resolve([match], [entry])
        self.assertFalse(linked)
        self.assertEqual(audit[0]["reason"], "unapproved_competition_series")

    def test_unapproved_custom_archived_and_lookalike_sources_rejected(self):
        _, entry = fixture()
        event = entry["event"]
        market = event["markets"][0]
        for mutation in [
            {"approved": False},
            {"cyom": True},
            {"archived": True},
            {"resolutionSource": "https://www.fifa.com.evil.test/"},
            {"resolutionSource": "http://www.premierleague.com/"},
            {"conditionId": "0x" + "0" * 64},
            {"questionID": None},
        ]:
            with self.subTest(mutation=mutation):
                self.assertIsNotNone(
                    contract_provenance("2000000000", event, market | mutation)
                )
        self.assertIsNone(contract_provenance("2000000000", event, market))
        self.assertIsNotNone(event_provenance("2000000000", event | {"cyom": True}))

    def test_all_duplicate_propositions_quarantined_without_volume_selection(self):
        match, entry = fixture()
        duplicate = copy.deepcopy(entry["event"]["markets"][0])
        duplicate.update(
            id="99",
            conditionId="0x" + "f" * 64,
            clobTokenIds='["990","991"]',
            volume="1000000",
        )
        entry["event"]["markets"].append(duplicate)
        linked, audit = resolve([match], [entry])
        self.assertEqual(len(linked["x"]), 2)
        self.assertEqual(
            sum(
                a.get("reason") == "ambiguous_duplicate_contract_identity"
                for a in audit
            ),
            2,
        )
        with self.assertRaisesRegex(ValueError, "triplet"):
            select_hda(match, linked["x"])

    def test_reused_condition_or_token_quarantines_both_owners(self):
        for key in ["conditionId", "clobTokenIds"]:
            match, entry = fixture()
            entry["event"]["markets"][1][key] = entry["event"]["markets"][0][key]
            linked, _ = resolve([match], [entry])
            self.assertEqual(len(linked["x"]), 1)

    def test_legitimate_different_propositions_remain_distinct(self):
        match, entry = fixture()
        linked, _ = resolve([match], [entry])
        self.assertEqual(len(linked["x"]), 3)

    def test_wrong_rule_subject_date_or_extra_time_is_rejected(self):
        for change in ["subject", "date", "extra"]:
            match, entry = fixture()
            m = entry["event"]["markets"][0]
            if change == "subject":
                m["description"] = m["description"].replace("West Ham", "Arsenal")
            if change == "date":
                m["question"] = m["question"].replace("2026-02-01", "2026-02-02")
            if change == "extra":
                m["description"] += " Includes extra time and penalties."
            linked, _ = resolve([match], [entry])
            with self.subTest(change=change), self.assertRaises(ValueError):
                select_hda(match, linked["x"])


class RegistryTests(unittest.TestCase):
    def test_clob_index_not_onchain_outcome_index(self):
        _, entry = fixture()
        e = entry["event"]
        m = e["markets"][0]
        self.assertIsNone(validate_registry(m, e, registry(m, e)))

    def test_swapped_tokens_wrong_condition_event_question_and_outcome_rejected(self):
        _, entry = fixture()
        e = entry["event"]
        m = e["markets"][0]
        for key, value in [
            ("condition_id", "0x" + "f" * 64),
            ("event_id", 99),
            ("event_slug", "wrong"),
            ("outcome", "No"),
            ("clob_index", 1),
            ("opposite_token_id", "900"),
            ("title", "Wrong question"),
            ("market_slug", "fake"),
            ("module", "unknown"),
        ]:
            rows = registry(m, e)
            rows["2"][key] = value
            with self.subTest(key=key):
                self.assertEqual(
                    validate_registry(m, e, rows), "registry_mismatch_" + key
                )
        self.assertEqual(validate_registry(m, e, {}), "token_not_in_registry")
        m = m | {"clobTokenIds": '["3","2"]'}
        self.assertIsNotNone(validate_registry(m, e, registry(e["markets"][0], e)))

    def test_certification_failure_cannot_silently_publish(self):
        match, entry = fixture()
        linked, audit = resolve([match], [entry])

        class Broken:
            def get(self, *args):
                raise OSError("network failed")

        with self.assertRaises(OSError):
            certify(Broken(), linked, audit)

        class Empty:
            def get(self, *args):
                return {"data": []}, {"sha256": "empty"}

        certify(Empty(), linked, audit)
        self.assertFalse(linked["x"])
        self.assertEqual(
            sum(a.get("reason") == "token_not_in_registry" for a in audit), 3
        )


if __name__ == "__main__":
    unittest.main()
