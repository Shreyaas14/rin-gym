import copy
import json
import unittest
import contextlib
import gzip
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlencode
import pyarrow as pa
import pyarrow.parquet as pq

from pipelines.fifa.client import CacheIntegrityError
from pipelines.fifa.markets import MarketClient, run, epoch

from pipelines.fifa.markets import (
    hda_outcome,
    norm,
    prior_price,
    resolve,
    select_hda,
    time_match,
    validated_history,
)


def fixture():
    match = dict(
        match_id="x",
        competition_id="2000000000",
        home_team_id="h",
        away_team_id="a",
        home_team_name="West Ham United",
        away_team_name="Bournemouth",
        scheduled_kickoff_utc="2026-02-01T15:00:00Z",
    )
    questions = [
        "Will West Ham win on 2026-02-01?",
        "Will West Ham vs. AFC Bournemouth end in a draw?",
        "Will AFC Bournemouth win on 2026-02-01?",
    ]
    markets = [
        dict(
            id=str(i),
            question=q,
            sportsMarketType=None,
            gameStartTime="2026-02-01T15:00:00Z",
            outcomes='["Yes", "No"]',
            clobTokenIds=json.dumps([str(i * 2), str(i * 2 + 1)]),
            approved=True,
            cyom=False,
            archived=False,
            enableOrderBook=True,
            conditionId="0x" + format(i, "064x"),
            questionID="0x" + format(i + 100, "064x"),
            slug="epl-wes-bou-2026-02-01-" + str(i),
            negRisk=False,
            description=(
                [
                    'If West Ham wins, this market will resolve to "Yes".',
                    'If the game ends in a draw, this market will resolve to "Yes".',
                    'If AFC Bournemouth wins, this market will resolve to "Yes".',
                ][i - 1]
                + "\n"
            )
            + "This market refers only to the outcome within the first 90 minutes of regular play plus stoppage time.",
        )
        for i, q in enumerate(questions, 1)
    ]
    event = dict(
        id="10",
        slug="epl-wes-bou-2026-02-01",
        title="West Ham vs. AFC Bournemouth",
        markets=markets,
        series=[{"id": "10188"}],
        cyom=False,
    )
    return match, dict(event=event, competition_id="2000000000", catalog_sha256="hash")


class ResolutionTests(unittest.TestCase):
    def test_exact_aliases_and_unicode_without_substrings(self):
        self.assertEqual(norm("Côte d’Ivoire"), norm("Ivory Coast"))
        self.assertEqual(norm("Bournemouth"), norm("AFC Bournemouth"))
        self.assertEqual(norm("Chelsea FC"), norm("Chelsea"))
        self.assertNotEqual(norm("Manchester United"), norm("Manchester City"))
        self.assertNotEqual(norm("United"), norm("Manchester United"))

    def test_legacy_untyped_markets_and_reversed_title(self):
        m, e = fixture()
        e["event"]["title"] = "AFC Bournemouth vs. West Ham"
        linked, audit = resolve([m], [e])
        selected = select_hda(m, linked["x"])
        self.assertEqual([i["outcome"] for i in selected], ["home", "draw", "away"])
        self.assertEqual(audit[0]["status"], "mapped")

    def test_wrong_league_and_ambiguous_fixture_rejected(self):
        m, e = fixture()
        e["event"]["slug"] = "efl-wes-bou-2026-02-01"
        self.assertFalse(resolve([m], [e])[0])
        m, e = fixture()
        second = m | {"match_id": "another"}
        linked, audit = resolve([m, second], [e])
        self.assertFalse(linked)
        self.assertEqual(audit[0]["reason"], "no_unique_team_time_match")

    def test_slug_date_not_required_to_equal_utc_date(self):
        m, e = fixture()
        e["event"]["slug"] = "epl-wes-bou-2026-01-31"
        self.assertEqual(len(resolve([m], [e])[0]["x"]), 3)

    def test_duplicate_outcome_rejected(self):
        m, e = fixture()
        duplicate = copy.deepcopy(e["event"]["markets"][0])
        duplicate["id"] = "99"
        e["event"]["markets"].append(duplicate)
        with self.assertRaisesRegex(ValueError, "triplet"):
            select_hda(m, resolve([m], [e])[0]["x"])

    def test_each_contract_requires_correct_time(self):
        m, e = fixture()
        e["event"]["markets"][0]["gameStartTime"] = "2026-02-02T15:00:00Z"
        linked, audit = resolve([m], [e])
        self.assertEqual(len(linked["x"]), 2)
        self.assertTrue(
            any(a.get("reason") == "contract_start_mismatch" for a in audit)
        )

    def test_alias_collisions_fail_loudly(self):
        m, e = fixture()
        other = m | dict(match_id="b", home_team_id="other-id")
        with self.assertRaisesRegex(ValueError, "collision"):
            resolve([m, other], [e])

    def test_advancement_is_not_regulation_moneyline(self):
        m, e = fixture()
        k = e["event"]["markets"][0]
        k["sportsMarketType"] = "soccer_team_to_advance"
        self.assertIsNone(hda_outcome(m, k))
        k["sportsMarketType"] = "moneyline"
        k["description"] = "Includes extra time and penalties."
        with self.assertRaises(ValueError):
            select_hda(m, resolve([m], [e])[0]["x"])

    def test_legacy_draw_exception_only_epl(self):
        m, e = fixture()
        e["event"]["markets"][1]["description"] = (
            'If the game ends in a draw, this market will resolve to "Yes".'
        )
        self.assertEqual(
            select_hda(m, resolve([m], [e])[0]["x"])[1]["rule_basis"],
            "epl_draw_companion_rules",
        )
        linked = resolve([m], [e])[0]["x"]
        with self.assertRaisesRegex(ValueError, "regulation_rules"):
            select_hda(m | {"competition_id": "17"}, linked)

    def test_time_override_is_exact_and_fixture_scoped(self):
        m, e = fixture()
        m.update(match_id="400021531", scheduled_kickoff_utc="2026-07-06T01:00:00Z")
        e["event"]["slug"] = "fifwc-mex-eng-2026-07-05"
        self.assertTrue(time_match(m, e["event"], "2026-07-06T00:00:00Z"))
        self.assertFalse(time_match(m, e["event"], "2026-07-05T23:00:00Z"))
        e["event"]["slug"] = "fifwc-mex-ecu-2026-07-05"
        self.assertFalse(time_match(m, e["event"], "2026-07-06T00:00:00Z"))


class HistoryTests(unittest.TestCase):
    def test_strictly_preceding_never_same_time_or_future(self):
        h = [dict(t=100, p=0.2), dict(t=160, p=0.8)]
        self.assertEqual(prior_price(h, 160), (0.2, 100.0, 60.0, True))
        self.assertEqual(prior_price(h, 99), (None, None, None, False))
        self.assertEqual(prior_price(h, 251), (0.8, 160.0, 91.0, False))
        self.assertTrue(prior_price(h, 250)[3])

    def test_empty_duplicate_unsorted_nonfinite_out_of_range_rejected(self):
        for history in [
            [],
            [dict(t=1, p=0.5), dict(t=1, p=0.6)],
            [dict(t=2, p=0.2), dict(t=1, p=0.3)],
            [dict(t=1, p=float("nan"))],
            [dict(t=1, p=1.01)],
        ]:
            with self.subTest(history=history), self.assertRaises(ValueError):
                validated_history({"history": history})


class PublicationIntegrityTests(unittest.TestCase):
    def test_corrupt_history_keeps_existing_hda_pointer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "fifa"
            source.mkdir()
            match, entry = fixture()
            match.update(
                timeline_status="ok", regulation_home_score=1, regulation_away_score=0
            )
            start = epoch(match["scheduled_kickoff_utc"])
            terminal = start + 7200
            events = [
                dict(
                    match_id=match["match_id"],
                    event_time_utc=match["scheduled_kickoff_utc"],
                    period_code=3,
                    event_kind="period_start",
                ),
                dict(
                    match_id=match["match_id"],
                    event_time_utc="2026-02-01T17:00:00Z",
                    period_code=5,
                    event_kind="period_end",
                ),
            ]
            pq.write_table(pa.Table.from_pylist([match]), source / "matches.parquet")
            pq.write_table(pa.Table.from_pylist(events), source / "events.parquet")
            (source / "manifest.json").write_text("{}")
            client = MarketClient(root / "raw", offline=True)
            for market in entry["event"]["markets"]:
                params = dict(
                    market=json.loads(market["clobTokenIds"])[0],
                    startTs=int(start - 3600),
                    endTs=int(terminal + 120),
                    fidelity=1,
                )
                _, meta = client.store(
                    "https://clob.polymarket.com/prices-history?" + urlencode(params),
                    json.dumps({"history": [{"t": start - 10, "p": 0.3}]}).encode(),
                )

            def certify(client, linked, audit, workers):
                for items in linked.values():
                    for item in items:
                        item["token_registry_sha256"] = ["registry-proof"]
                return []

            args = SimpleNamespace(
                dataset=source,
                output=root / "mapping",
                cache=root / "raw",
                offline=True,
                refresh=False,
                workers=1,
                max_age=90,
            )
            with patch("pipelines.fifa.markets.verify"), patch(
                "pipelines.fifa.markets.catalog", return_value=([entry], [])
            ), patch(
                "pipelines.fifa.markets.certify", side_effect=certify
            ), contextlib.redirect_stdout(io.StringIO()):
                out = run(args)
                self.assertEqual(pq.read_metadata(out / "matches.parquet").num_rows, 1)
                pointer = (args.output / "current.json").read_bytes()
                (args.cache / meta["raw_file"]).write_bytes(gzip.compress(b"{}"))
                with self.assertRaises(CacheIntegrityError):
                    run(args)
                self.assertEqual((args.output / "current.json").read_bytes(), pointer)


if __name__ == "__main__":
    unittest.main()
