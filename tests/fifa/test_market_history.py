import io
import contextlib
import hashlib
from types import SimpleNamespace
from unittest.mock import patch
import pyarrow as pa
import pyarrow.parquet as pq
import json
from pathlib import Path
import tempfile
import unittest

from pipelines.fifa.market_history import build_requests, download
from pipelines.fifa.markets import MarketClient
from pipelines.fifa.client import AcquisitionError, CacheIntegrityError


def fixture():
    contracts = [
        dict(
            match_id="m",
            market_id="market",
            source_market_json=json.dumps(
                dict(
                    outcomes=["Over", "Under"],
                    clobTokenIds=["over-token", "under-token"],
                )
            ),
        )
    ]
    matches = [dict(match_id="m", scheduled_kickoff_utc="2026-06-01T12:00:00Z")]
    events = {
        "m": [
            dict(
                event_time_utc="2026-06-01T12:00:00Z",
                event_kind="period_start",
                period_code=3,
            ),
            dict(
                event_time_utc="2026-06-01T14:45:00Z",
                event_kind="period_end",
                period_code=11,
            ),
        ]
    }
    return contracts, matches, events


class BatchTests(unittest.TestCase):
    def test_tokens_keep_provider_outcome_order_and_full_game_window(self):
        jobs, excluded = build_requests(*fixture())
        self.assertFalse(excluded)
        rows, body, window = jobs[0]
        self.assertEqual(
            [(r["token_id"], r["outcome"]) for r in rows],
            [("over-token", "Over"), ("under-token", "Under")],
        )
        self.assertEqual(body["markets"], ["over-token", "under-token"])
        self.assertEqual(body["start_ts"], window["kickoff"] - 3600)
        self.assertEqual(body["end_ts"], window["terminal"] + 300)
        self.assertGreater(window["terminal"] - window["kickoff"], 2 * 3600)

    def test_missing_tokens_excluded_without_changing_raw(self):
        cs, ms, es = fixture()
        cs[0]["source_market_json"] = '{"outcomes":["Yes","No"]}'
        before = json.dumps(cs)
        jobs, excluded = build_requests(cs, ms, es)
        self.assertFalse(jobs)
        self.assertEqual(excluded[0]["reason"], "no_complete_outcome_token_mapping")
        self.assertEqual(json.dumps(cs), before)

    def test_batch_limit_and_duplicate_ownership(self):
        cs, ms, es = fixture()
        cs = [
            cs[0]
            | dict(
                market_id=str(i),
                source_market_json=json.dumps(
                    dict(outcomes=["Yes", "No"], clobTokenIds=[f"{i}Y", f"{i}N"])
                ),
            )
            for i in range(25)
        ]
        jobs, _ = build_requests(cs, ms, es)
        self.assertEqual([len(job[0]) for job in jobs], [20, 20, 10])
        with self.assertRaisesRegex(ValueError, "multiple market owners"):
            build_requests(cs + [cs[0]], ms, es)

    def test_batch_response_is_keyed_by_token_not_response_order(self):
        jobs, _ = build_requests(*fixture())
        t = jobs[0][2]["kickoff"]

        class Fake:
            def get(self, *args, **kwargs):
                return {
                    "history": {
                        "under-token": [{"t": t, "p": 0.8}],
                        "over-token": [{"t": t, "p": 0.2}],
                    }
                }, {"sha256": "h"}

        prices, results, meta = download(Fake(), jobs[0])
        self.assertEqual(
            [(p["outcome"], p["price"]) for p in prices],
            [("Over", 0.2), ("Under", 0.8)],
        )
        self.assertTrue(all(r["in_game_points"] == 1 for r in results))

    def test_empty_one_side_retains_other_without_synthesizing_complement(self):
        jobs, _ = build_requests(*fixture())
        t = jobs[0][2]["kickoff"]

        class Fake:
            def get(self, *args, **kwargs):
                return {
                    "history": {"under-token": [], "over-token": [{"t": t, "p": 0.2}]}
                }, {"sha256": "h"}

        prices, results, _ = download(Fake(), jobs[0])
        self.assertEqual(len(prices), 1)
        self.assertEqual(results[1]["reason"], "empty_history")
        self.assertEqual(results[0]["status"], "included")

    def test_out_of_window_history_rejected(self):
        jobs, _ = build_requests(*fixture())

        class Fake:
            def get(self, *args, **kwargs):
                return {
                    "history": {"under-token": [], "over-token": [{"t": 1, "p": 0.2}]}
                }, {"sha256": "h"}

        prices, results, _ = download(Fake(), jobs[0])
        self.assertFalse(prices)
        self.assertEqual(results[0]["reason"], "history_outside_requested_window")

    def test_missing_batch_token_falls_back_to_individual_history(self):
        jobs, _ = build_requests(*fixture())
        t = jobs[0][2]["kickoff"]
        calls = []

        class Fake:
            def get(self, url, **kwargs):
                calls.append(url)
                if "json_body" in kwargs:
                    return {"history": {"over-token": [{"t": t, "p": 0.2}]}}, {
                        "sha256": "batch"
                    }
                return {"history": [{"t": t, "p": 0.8}]}, {"sha256": "single"}

        prices, results, sources = download(Fake(), jobs[0])
        self.assertEqual(len(calls), 2)
        self.assertIn("market=under-token", calls[-1])
        self.assertEqual(results[1]["history_sha256"], "single")
        self.assertEqual(prices[1]["source_response_sha256"], "single")
        self.assertEqual(len(sources), 2)
        self.assertTrue(all(r["status"] == "included" for r in results))

    def test_acquisition_failure_in_batch_and_fallback_is_fatal(self):
        jobs, _ = build_requests(*fixture())
        t = jobs[0][2]["kickoff"]
        for error_type in (AcquisitionError, CacheIntegrityError):
            for fallback in (False, True):

                class Fake:
                    def get(self, url, **kwargs):
                        if fallback and "json_body" in kwargs:
                            return {"history": {"over-token": [{"t": t, "p": 0.2}]}}, {
                                "sha256": "h"
                            }
                        raise error_type("Untrusted response")

                with self.subTest(
                    error=error_type, fallback=fallback
                ), self.assertRaises(error_type):
                    download(Fake(), jobs[0])

    def test_post_cache_distinguishes_bodies_and_replays_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = []

            def opener(request, timeout):
                calls.append((request.get_method(), json.loads(request.data)))
                return io.BytesIO(b'{"history":{}}')

            client = MarketClient(Path(tmp), opener=opener)
            for token in ["a", "b", "a"]:
                client.get("https://example.test/read", json_body={"markets": [token]})
            self.assertEqual(
                calls, [("POST", {"markets": ["a"]}), ("POST", {"markets": ["b"]})]
            )
            cached = MarketClient(Path(tmp), offline=True)
            self.assertEqual(
                cached.get("https://example.test/read", json_body={"markets": ["a"]})[
                    0
                ],
                {"history": {}},
            )
            with self.assertRaises(FileNotFoundError):
                cached.get("https://example.test/read", json_body={"markets": ["c"]})


class PublicationTests(unittest.TestCase):
    def test_unpriceable_contracts_excluded_raw_unchanged_and_errors_do_not_publish(
        self,
    ):
        from pipelines.fifa.market_history import run
        from pipelines.fifa.markets import CONTRACTS, epoch

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "mapping"
            fifa = root / "fifa"
            source.mkdir()
            fifa.mkdir()
            cs, ms, es = fixture()
            rows = []
            for market_id, ids in [
                ("good", ["a", "b"]),
                ("empty", ["c", "d"]),
                ("unlaunched", []),
            ]:
                rows.append(
                    dict(
                        match_id="m",
                        competition_id="17",
                        market_id=market_id,
                        polymarket_event_id="event",
                        slug="fixture",
                        question="Fixture prop",
                        sports_market_type="totals",
                        hda_outcome=None,
                        kickoff_delta_seconds=0.0,
                        catalog_sha256="catalog",
                        source_market_json=json.dumps(
                            dict(outcomes=["Over", "Under"], clobTokenIds=ids)
                        ),
                    )
                )
            raw = source / "fixture-contracts.parquet"
            pq.write_table(pa.Table.from_pylist(rows, schema=CONTRACTS), raw)
            before = hashlib.sha256(raw.read_bytes()).hexdigest()
            pq.write_table(pa.Table.from_pylist(ms), fifa / "matches.parquet")
            pq.write_table(
                pa.Table.from_pylist([e | {"match_id": "m"} for e in es["m"]]),
                fifa / "events.parquet",
            )
            (source / "manifest.json").write_text(
                json.dumps(
                    dict(
                        source_snapshot=str(fifa),
                        contract_policy_version="polymarket-contract-policy-v1",
                        files={"fixture-contracts": {"sha256": before}},
                    )
                )
            )
            t = epoch(ms[0]["scheduled_kickoff_utc"])

            class Fake:
                fail = False

                def __init__(self, *args, **kwargs):
                    pass

                def get(self, *args, **kwargs):
                    if self.fail:
                        raise OSError("temporary transport failure")
                    return {
                        "history": {
                            "a": [{"t": t, "p": 0.2}],
                            "b": [{"t": t, "p": 0.8}],
                            "c": [],
                            "d": [],
                        }
                    }, {"sha256": "history"}

            args = SimpleNamespace(
                dataset=source,
                output=root / "final",
                cache=root / "raw",
                workers=1,
                offline=True,
                refresh=False,
            )
            with patch(
                "pipelines.fifa.market_history.MarketClient", Fake
            ), contextlib.redirect_stdout(io.StringIO()):
                out = run(args)
                final = pq.read_table(out / "markets.parquet").to_pylist()
                self.assertEqual([m["market_id"] for m in final], ["good"])
                self.assertEqual(pq.read_metadata(out / "prices.parquet").num_rows, 2)
                audit = json.loads((out / "market-resolution.json").read_text())
                self.assertEqual(len(audit), 3)
                self.assertEqual(sum(a["status"] == "excluded" for a in audit), 2)
                self.assertEqual(hashlib.sha256(raw.read_bytes()).hexdigest(), before)
                pointer = (args.output / "current.json").read_bytes()
                for fatal in (
                    AcquisitionError("Invalid response"),
                    CacheIntegrityError("Corrupt cache"),
                ):
                    with patch.object(Fake, "get", side_effect=fatal):
                        with self.assertRaises(type(fatal)):
                            run(args)
                    self.assertEqual(
                        (args.output / "current.json").read_bytes(), pointer
                    )
                Fake.fail = True
                with self.assertRaisesRegex(RuntimeError, "pointer unchanged"):
                    run(args)
                self.assertEqual((args.output / "current.json").read_bytes(), pointer)


if __name__ == "__main__":
    unittest.main()
