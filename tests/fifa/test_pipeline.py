import copy
import gzip
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from pipelines.fifa.client import Client, BASE, AcquisitionError, CacheIntegrityError
from pipelines.fifa.dataset import ingest, resolve_snapshot, verify
from pipelines.fifa.normalize import normalize
from pipelines.fifa.replay import load_timeline, export_replay


def event(eid, code, period, at, home=0, away=0, team=None, **kwargs):
    return dict(
        EventId=eid,
        Type=code,
        Period=period,
        Timestamp="2026-06-01T" + at + "Z",
        IdTeam=team,
        HomeGoals=home,
        AwayGoals=away,
        HomePenaltyGoals=0,
        AwayPenaltyGoals=0,
        MatchMinute="0'",
        TypeLocalized=[{"Locale": "en-GB", "Description": str(code)}],
        Qualifiers=[],
        **kwargs,
    )


def fixture():
    match = {
        "IdMatch": "match-A",
        "IdCompetition": "17",
        "IdSeason": "2026",
        "MatchStatus": 0,
        "Date": "2026-06-01T12:00:00Z",
        "Home": {
            "IdTeam": "home-A",
            "Score": 1,
            "TeamName": [{"Locale": "en-GB", "Description": "Home"}],
        },
        "Away": {
            "IdTeam": "away-B",
            "Score": 0,
            "TeamName": [{"Locale": "en-GB", "Description": "Away"}],
        },
        "CompetitionName": [{"Locale": "en-GB", "Description": "Cup"}],
        "SeasonName": [{"Locale": "en-GB", "Description": "2026"}],
        "Properties": {"IdIFES": "100"},
    }
    events = [
        event("1", 7, 3, "12:00:00"),
        event("2", 0, 3, "12:10:00", 1, 0, "home-A", PositionX=90.0, PositionY=50.0),
        event("3", 8, 3, "12:47:00", 1),
        event("4", 7, 5, "13:02:00", 1),
        event("5", 8, 5, "13:52:00", 1),
        event("6", 26, 10, "13:52:00", 1),
    ]
    timeline = {k: match[k] for k in ["IdMatch", "IdCompetition", "IdSeason"]} | {
        "Event": events
    }
    metadata = {
        "sha256": "f" * 64,
        "fetched_at_utc": "2026-10-01T00:00:00+00:00",
        "url": BASE + "test",
    }
    return match, timeline, metadata


class NormalizeTests(unittest.TestCase):
    def setUp(self):
        self.m, self.t, self.meta = fixture()

    def run_normalize(self):
        return normalize(self.m, self.meta, self.t, self.meta, "v1")

    def test_opposing_team_ids_remain_distinct(self):
        self.t["Event"][0]["EventId"] = "2"
        self.t["Event"][0]["IdTeam"] = "away-B"
        m, es = self.run_normalize()
        self.assertEqual(len({e["event_uid"] for e in es}), len(es))
        self.assertEqual(es[0]["event_id"], es[1]["event_id"])

    def test_same_team_identity_collision_rejected(self):
        self.t["Event"].append(copy.deepcopy(self.t["Event"][0]))
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.run_normalize()

    def test_missing_event_id_rejected(self):
        del self.t["Event"][0]["EventId"]
        with self.assertRaisesRegex(ValueError, "Missing event"):
            self.run_normalize()

    def test_regulation_extra_time_shootout_are_separate(self):
        self.t["Event"] = self.t["Event"][:-1] + [
            event("7", 7, 7, "14:00:00", 1),
            event("8", 8, 7, "14:15:00", 2),
            event("9", 7, 9, "14:18:00", 2),
            event("10", 8, 9, "14:34:00", 2, 2),
            event("11", 7, 11, "14:40:00", 2, 2),
            event("12", 41, 11, "14:41:00", 2, 2, "home-A"),
            event("13", 8, 11, "14:50:00", 2, 2),
            event("14", 26, 10, "14:50:00", 2, 2),
        ]
        self.t["Event"][-1].update(HomePenaltyGoals=4, AwayPenaltyGoals=3)
        self.m["Home"]["Score"] = self.m["Away"]["Score"] = 2
        self.m.update(HomeTeamPenaltyScore=4, AwayTeamPenaltyScore=3)
        m, es = self.run_normalize()
        self.assertEqual(
            (m["regulation_home_score"], m["regulation_away_score"]), (1, 0)
        )
        self.assertEqual((m["final_home_score"], m["final_away_score"]), (2, 2))
        self.assertEqual((m["shootout_home_score"], m["shootout_away_score"]), (4, 3))
        self.assertTrue(m["went_to_extra_time"] and m["went_to_shootout"])
        self.assertEqual(es[-3]["phase"], "shootout")

    def test_zero_corner_preserved_but_pl_clock_placeholder(self):
        self.t["Event"][1].update(Type=16, PositionX=0.0, PositionY=0.0)
        _, es = self.run_normalize()
        self.assertEqual(es[1]["location_status"], "ambiguous_zero")
        self.m["Properties"] = {"IdStatsPerform": "abc"}
        self.t["Event"][0].update(PositionX=0.0, PositionY=0.0)
        _, es = self.run_normalize()
        self.assertEqual(es[0]["location_status"], "placeholder")
        self.assertEqual(es[1]["location_status"], "ambiguous_zero")

    def test_unambiguous_regulation_marker_required(self):
        self.t["Event"] = [e for e in self.t["Event"] if e["EventId"] != "5"]
        m, _ = self.run_normalize()
        self.assertIsNone(m["regulation_home_score"])
        self.assertIn("ambiguous_regulation_score", m["quality_flags"])

    def test_conflicting_final_label_is_null(self):
        self.m["Home"]["Score"] = 4
        m, _ = self.run_normalize()
        self.assertIsNone(m["final_home_score"])
        self.assertIn("final_score_conflict_or_missing", m["quality_flags"])

    def test_unknown_code_and_clock_do_not_discard_event(self):
        self.t["Event"][1].update(
            Type=999, Period=999, MatchMinute="45'+3'", Timestamp="invalid"
        )
        _, es = self.run_normalize()
        r = es[1]
        self.assertEqual((r["event_kind"], r["phase"]), ("unknown", "unknown"))
        self.assertEqual((r["clock_minute"], r["stoppage_minute"]), (45, 3))
        self.assertIsNone(r["event_time_utc"])
        self.assertIsNone(r["period_elapsed_ms"])
        self.assertEqual(json.loads(r["source_event_json"]), self.t["Event"][1])

    def test_substitution_before_start_has_no_negative_clock(self):
        self.t["Event"].insert(3, event("x", 5, 5, "13:01:00", 1))
        _, es = self.run_normalize()
        self.assertIsNone(es[3]["period_elapsed_ms"])
        self.assertIn("event_before_period_start", es[3]["quality_flags"])

    def test_utc_and_fetch_time_not_conflated(self):
        _, es = self.run_normalize()
        self.assertNotEqual(es[0]["event_time_utc"], es[0]["fetched_at_utc"])
        self.assertIsNotNone(es[0]["event_time_utc"].tzinfo)

    def test_foreign_timeline_rejected(self):
        self.t["IdMatch"] = "different"
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            self.run_normalize()

    def test_empty_completed_timeline_flagged(self):
        self.t["Event"] = []
        m, es = self.run_normalize()
        self.assertEqual(m["timeline_status"], "empty")
        self.assertEqual(es, [])
        self.assertIn("completed_match_empty_timeline", m["quality_flags"])

    def test_qualifier_var_and_unknown_fields_preserved(self):
        self.t["Event"][1].update(
            Qualifiers=[{"Id": "42", "QualifierId": 59, "JerseyNumbers": ["10"]}],
            VarNotificationData={"Result": 2},
            NewField={"future": "data"},
        )
        _, es = self.run_normalize()
        self.assertEqual(
            json.loads(es[1]["qualifiers_json"]), self.t["Event"][1]["Qualifiers"]
        )
        self.assertEqual(json.loads(es[1]["source_event_json"]), self.t["Event"][1])


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_offline_cache_and_hash_verification(self):
        c = Client(self.root, offline=True)
        body = b'{"ok":true}'
        _, meta = c.store(BASE + "test", body, "2026-01-01T00:00:00Z")
        self.assertEqual(c.get(BASE + "test")[0], {"ok": True})
        (self.root / meta["raw_file"]).write_bytes(gzip.compress(b"{}"))
        with self.assertRaisesRegex(CacheIntegrityError, "integrity"):
            c.get(BASE + "test")

    def test_invalid_live_json_and_corrupt_cache_are_fatal(self):
        for body in (b"not json", b"[]"):
            with self.subTest(body=body), self.assertRaises(AcquisitionError):
                Client(self.root).store(BASE + "invalid", body)
        client = Client(self.root, offline=True)
        _, meta = client.store(BASE + "broken", b'{"ok":true}')
        payload = self.root / meta["raw_file"]
        for body in (b"not gzip", gzip.compress(b"{}")):
            payload.write_bytes(body)
            with self.subTest(body=body), self.assertRaises(CacheIntegrityError):
                client.get(BASE + "broken")
        client._index(BASE + "broken").write_text("not json")
        with self.assertRaises(CacheIntegrityError):
            client.get(BASE + "broken")

    def test_offline_miss_never_calls_network(self):
        def fail(*a, **kw):
            self.fail("Network called")

        with self.assertRaises(FileNotFoundError):
            Client(self.root, offline=True, opener=fail).get(BASE + "missing")

    def test_retry_after_honored(self):
        errors = [
            HTTPError(BASE + "test", 429, "rate limit", {"Retry-After": "7"}, None),
            io.BytesIO(b"{}"),
        ]
        delays = []

        def open_(*a, **kw):
            v = errors.pop(0)
            if isinstance(v, Exception):
                raise v
            return v

        Client(self.root, opener=open_, sleep=delays.append).get(BASE + "test")
        self.assertEqual(delays, [7.0])

    def test_404_not_retried(self):
        calls = []

        def open_(*a, **kw):
            calls.append(1)
            raise HTTPError(BASE + "test", 404, "missing", {}, None)

        with self.assertRaises(HTTPError):
            Client(self.root, opener=open_).get(BASE + "test")
        self.assertEqual(len(calls), 1)

    def test_pagination_and_hash_forwarding(self):
        c = Client(self.root)
        urls = []
        pages = [
            {
                "Results": [{"IdMatch": "a", "IdCompetition": "c", "IdSeason": "s"}],
                "ContinuationToken": "next",
                "ContinuationHash": "hash",
            },
            {"Results": [{"IdMatch": "b", "IdCompetition": "c", "IdSeason": "s"}]},
        ]

        def get(url):
            urls.append(url)
            return pages.pop(0), {"sha256": "hash"}

        c.get = get
        matches, _ = c.calendar("c", "s")
        self.assertEqual(len(matches), 2)
        self.assertIn("continuationToken=next", urls[1])
        self.assertIn("continuationHash=hash", urls[1])

    def test_repeated_continuation_rejected(self):
        c = Client(self.root)
        c.get = lambda u: ({"Results": [], "ContinuationToken": "same"}, {})
        with self.assertRaisesRegex(ValueError, "Repeated"):
            c.calendar("c", "s")

    def test_wrong_scope_rejected(self):
        c = Client(self.root)
        c.get = lambda u: (
            {"Results": [{"IdMatch": "a", "IdCompetition": "wrong", "IdSeason": "s"}]},
            {},
        )
        with self.assertRaisesRegex(ValueError, "different"):
            c.calendar("c", "s")


class DatasetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.m, self.t, self.meta = fixture()
        self.c = Client(self.root / "raw", offline=True)
        self.c.calendar = lambda c, s: ([(self.m, self.meta)], [self.meta])
        self.c.timelines = lambda matches, workers: iter(
            [(self.m, self.meta, self.t, self.meta, None)]
        )

    def run_ingest(self, partial=False):
        return ingest(
            self.c,
            self.root / "out",
            cohorts=[],
            scopes=[("17", "2026")],
            allow_partial=partial,
        )

    def test_parquet_roundtrip_and_lossless_replay_without_raw_cache(self):
        path, _ = self.run_ingest()
        self.assertEqual(verify(path)["events"], 6)
        replay = load_timeline(self.root / "out", "match-A")
        self.assertEqual(replay["Event"], self.t["Event"])
        p = export_replay(path, "match-A", self.root / "replay")
        self.assertEqual(json.loads(p.read_text())["Event"], self.t["Event"])

    def test_failed_refresh_preserves_pointer(self):
        self.run_ingest()
        before = (self.root / "out/current.json").read_bytes()
        self.c.timelines = lambda *args: iter(
            [(self.m, self.meta, None, None, "HTTP 503")]
        )
        with self.assertRaisesRegex(ValueError, "unchanged"):
            self.run_ingest()
        self.assertEqual(before, (self.root / "out/current.json").read_bytes())
        self.assertEqual(verify(self.root / "out")["events"], 6)

    def test_partial_snapshot_retains_match_and_error(self):
        self.c.timelines = lambda *args: iter(
            [(self.m, self.meta, None, None, "HTTP 503")]
        )
        path, manifest = self.run_ingest(True)
        self.assertEqual(verify(path)["matches"], 1)
        self.assertEqual(verify(path)["events"], 0)
        self.assertEqual(len(manifest["errors"]), 1)
        with self.assertRaisesRegex(ValueError, "valid replay"):
            load_timeline(path, "match-A")

    def test_reruns_do_not_duplicate_events_and_old_snapshot_survives(self):
        old, _ = self.run_ingest()
        new, _ = self.run_ingest()
        self.assertNotEqual(old, new)
        self.assertEqual(verify(old)["events"], verify(new)["events"])
        self.assertEqual(resolve_snapshot(self.root / "out"), new)

    def test_tampered_parquet_rejected(self):
        path, _ = self.run_ingest()
        with (path / "events.parquet").open("ab") as f:
            f.write(b"corruption")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            verify(path)

    def test_writer_failure_does_not_publish(self):
        self.run_ingest()
        before = (self.root / "out/current.json").read_bytes()
        with patch(
            "pipelines.fifa.dataset.pq.write_table", side_effect=OSError("full disk")
        ):
            with self.assertRaises(OSError):
                self.run_ingest()
        self.assertEqual(before, (self.root / "out/current.json").read_bytes())
        self.assertEqual(list((self.root / "out/snapshots").glob(".building-*")), [])

    def test_pl_placeholder_sanitization_preserves_other_zero_locations(self):
        self.m["Properties"] = {"IdStatsPerform": "match-A"}
        self.t["Event"][0].update(PositionX=0, PositionY=0)
        self.t["Event"][1].update(Type=16, PositionX=0, PositionY=0)
        path, _ = self.run_ingest()
        raw = load_timeline(path, "match-A")
        clean = load_timeline(path, "match-A", True)
        self.assertIn("PositionX", raw["Event"][0])
        self.assertNotIn("PositionX", clean["Event"][0])
        self.assertEqual(clean["Event"][1]["PositionX"], 0)


if __name__ == "__main__":
    unittest.main()
