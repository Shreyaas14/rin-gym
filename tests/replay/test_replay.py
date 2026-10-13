from copy import deepcopy
from contextlib import chdir
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import shutil
import unittest

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from app.replay.model import Replay, has_location
from app.replay.render import Renderer, render
from app.replay.store import import_database, list_matches, load_match

START = datetime(2026, 6, 1, 12, tzinfo=timezone.utc)


def fixture():
    def event(i, minutes, kind, period, score=(0, 0), loc="missing"):
        return dict(
            match_id="game",
            event_uid=str(i),
            source_index=i,
            event_time_utc=START + timedelta(minutes=minutes),
            event_kind=kind,
            event_type_label=kind.replace("_", " ").title(),
            event_description="A recorded action",
            team_id="home",
            period_code=period,
            home_score=score[0],
            away_score=score[1],
            home_shootout_score=0,
            away_shootout_score=0,
            match_minute_raw=str(minutes) + "'",
            x_raw=70.0 if loc != "missing" else None,
            y_raw=30.0 if loc != "missing" else None,
            location_status=loc,
        )

    events = [
        event(0, 0, "period_start", 3),
        event(1, 10, "goal", 3, (1, 0), "present_unverified"),
        event(2, 10, "assist", 3, (0, 0)),
        event(3, 46, "period_end", 3, (1, 0)),
        event(4, 62, "period_start", 5, (1, 0)),
        event(5, 110, "period_end", 5, (1, 0)),
        event(6, 110, "match_end", 10, (1, 0)),
    ]
    match = dict(
        source="fifa",
        match_id="game",
        home_team_id="home",
        away_team_id="away",
        home_team_name="A Very Long Home Team Name",
        away_team_name="Visitors",
        competition_name="Example League",
        season_name="2026",
        stage_name="Final",
        scheduled_kickoff_utc=START,
        final_home_score=5,
        final_away_score=2,
        event_count=len(events),
        timeline_status="ok",
        dataset_version="test-v1",
    )
    markets = {
        side: dict(
            market_id=side,
            token_id=side + "-yes",
            prices=[
                dict(t=START.timestamp() - 30, p=0.3),
                dict(t=START.timestamp() + 600, p=0.8),
            ],
        )
        for side in ("home", "draw", "away")
    }
    return dict(
        match=match,
        events=events,
        markets=markets,
        odds_status="Mapped",
        database="test.duckdb",
    )


class StateTests(unittest.TestCase):
    def test_no_future_score_or_event_at_opening(self):
        r = Replay(fixture())
        state = r.state(START.timestamp() - 1)
        self.assertIsNone(state["current"])
        self.assertEqual(state["score"], (0, 0))
        self.assertEqual(state["phase"], "BEFORE KICKOFF")
        self.assertEqual(r.state(START.timestamp() + 1)["score"], (0, 0))

    def test_same_time_stale_assist_does_not_undo_goal(self):
        state = Replay(fixture()).state(START.timestamp() + 600)
        self.assertEqual(state["score"], (1, 0))
        self.assertEqual(state["current"]["event_kind"], "goal")

    def test_price_strict_precedence_staleness_and_regulation_cutoff(self):
        r = Replay(fixture())
        t = START.timestamp()
        self.assertEqual(r.odds("home", t)[0]["p"], 0.3)
        self.assertIsNone(r.odds("home", t + 600)[0])  # same-time .8 cannot be used
        self.assertEqual(r.odds("home", t + 601)[0]["p"], 0.8)
        self.assertIsNone(r.odds("home", t + 691)[0])
        self.assertIn("ended", r.odds("home", t + 6601)[1])

    def test_missing_or_ambiguous_location_suppressed(self):
        e = fixture()["events"][1]
        self.assertTrue(has_location(e))
        for status in ("missing", "placeholder", "ambiguous_zero", "out_of_range"):
            self.assertFalse(has_location(e | {"location_status": status}))
        self.assertFalse(has_location(e | {"x_raw": float("nan")}))
        self.assertFalse(has_location(e | {"x_raw": 101}))

    def test_extra_time_shootout_and_penalties_not_regulation(self):
        data = fixture()
        data["events"].pop()
        for i, (minute, kind, period) in enumerate(
            [
                (120, "period_start", 7),
                (135, "period_end", 7),
                (138, "period_start", 9),
                (154, "period_end", 9),
                (160, "period_start", 11),
                (165, "goal", 11),
                (166, "match_end", 10),
            ],
            10,
        ):
            e = deepcopy(data["events"][-1])
            e.update(
                event_uid=str(i),
                source_index=i,
                event_time_utc=START + timedelta(minutes=minute),
                event_kind=kind,
                period_code=period,
            )
            if minute >= 165:
                e.update(home_shootout_score=4, away_shootout_score=3)
            data["events"].append(e)
        r = Replay(data)
        self.assertEqual(
            r.phase((START + timedelta(minutes=125)).timestamp()),
            "EXTRA TIME · FIRST HALF",
        )
        self.assertEqual(
            r.phase((START + timedelta(minutes=162)).timestamp()), "PENALTY SHOOTOUT"
        )
        state = r.state((START + timedelta(minutes=166)).timestamp())
        self.assertEqual(state["phase"], "FULL TIME")
        self.assertEqual(state["penalties"], (4, 3))
        self.assertIsNone(
            r.odds("home", (START + timedelta(minutes=125)).timestamp())[0]
        )

    def test_preview_zero_goals_without_odds_and_reinitialization(self):
        data = fixture()
        data["markets"] = {}
        data["events"][1]["event_kind"] = "foul"
        with tempfile.TemporaryDirectory() as tmp:
            manifest = render(Replay(data), Path(tmp), preview=True)
            self.assertEqual(manifest["match_id"], "game")
            self.assertTrue((Path(tmp) / "final.png").exists())
            self.assertTrue((Path(tmp) / "render-manifest.json").exists())
            self.assertFalse(list(Path(tmp).glob("preview-goal*")))
        second = Renderer(Replay(fixture()))
        self.assertEqual(second.frame(0).size, (1600, 900))


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        data = fixture()
        self.data = data
        self.write_snapshot(
            self.source, {"matches": [data["match"]], "events": data["events"]}
        )
        self.database = self.root / "db.duckdb"

    def write_snapshot(self, path, tables, **extra):
        path.mkdir(exist_ok=True)
        files = {}
        for name, rows in tables.items():
            file = path / (name + ".parquet")
            pq.write_table(pa.Table.from_pylist(rows), file)
            files[name] = dict(
                sha256=hashlib.sha256(file.read_bytes()).hexdigest(), rows=len(rows)
            )
        (path / "manifest.json").write_text(json.dumps(dict(files=files, **extra)))

    def test_import_query_and_replay_after_parquet_removed(self):
        result = import_database(self.source, self.database)
        self.assertEqual(result["matches"], 1)
        self.assertEqual(list_matches(self.database, "visitors")[0]["match_id"], "game")
        self.assertEqual(list_matches(self.database, "' OR 1=1 --"), [])
        for file in self.source.glob("*.parquet"):
            file.unlink()
        data = load_match(self.database, "game")
        self.assertEqual(len(data["events"]), 7)
        self.assertFalse(data["markets"])
        self.assertEqual(Replay(data).state(START.timestamp() + 601)["score"], (1, 0))

    def test_checksum_failure_and_no_clobber(self):
        import_database(self.source, self.database)
        before = hashlib.sha256(self.database.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, "exists"):
            import_database(self.source, self.database)
        (self.source / "events.parquet").write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "checksum"):
            import_database(self.source, self.database, replace=True)
        self.assertEqual(hashlib.sha256(self.database.read_bytes()).hexdigest(), before)

    def test_orphan_or_duplicate_events_not_published(self):
        events = deepcopy(self.data["events"])
        events[0]["match_id"] = "wrong"
        self.write_snapshot(
            self.source, {"matches": [self.data["match"]], "events": events}
        )
        with self.assertRaisesRegex(ValueError, "Orphan"):
            import_database(self.source, self.database)
        self.assertFalse(self.database.exists())
        events = deepcopy(self.data["events"])
        events[1]["event_uid"] = events[0]["event_uid"]
        self.write_snapshot(
            self.source, {"matches": [self.data["match"]], "events": events}
        )
        with self.assertRaisesRegex(ValueError, "not unique"):
            import_database(self.source, self.database)

    def write_market_snapshot(self, market_source, reference):
        contracts = []
        prices = []
        for side, market in self.data["markets"].items():
            contracts.append(
                dict(
                    match_id="game",
                    market_id=side,
                    market_family="moneyline",
                    hda_outcome=side,
                    source_market_json="{}",
                    tokens=[
                        dict(
                            token_id=market["token_id"],
                            outcome="Yes",
                            status="included",
                        )
                    ],
                )
            )
            prices += [
                dict(
                    match_id="game",
                    market_id=side,
                    token_id=market["token_id"],
                    timestamp=p["t"],
                    price=p["p"],
                )
                for p in market["prices"]
            ]
        self.write_snapshot(
            market_source,
            {"markets": contracts, "prices": prices},
            source_fifa_snapshot=reference,
        )

    def test_history_tokens_and_ambiguous_contracts(self):
        market_source = self.root / "markets"
        self.write_market_snapshot(market_source, "../source")
        import_database(self.source, self.database, market_source)
        self.assertEqual(
            set(load_match(self.database, "game")["markets"]), {"home", "draw", "away"}
        )
        with duckdb.connect(str(self.database)) as con:
            con.execute(
                "INSERT INTO markets SELECT match_id,'duplicate',market_family,hda_outcome,source_market_json,tokens FROM markets WHERE market_id='home'"
            )
        data = load_match(self.database, "game")
        self.assertFalse(data["markets"])
        self.assertIn("Ambiguous", data["odds_status"])

    def test_relocated_bundles_import_from_unrelated_working_directory(self):
        producer = self.root / "producer"
        fifa = producer / "data/processed/fifa/snapshots/test-v1"
        fifa.parent.mkdir(parents=True)
        shutil.copytree(self.source, fifa)
        markets = producer / "data/processed/fifa-market-history/snapshots/odds-v1"
        markets.parent.mkdir(parents=True)
        references = [
            str(fifa),
            "data/processed/fifa/snapshots/test-v1",
            "../../../fifa/snapshots/test-v1",
        ]
        relocated = self.root / "teammate"
        unrelated = self.root / "elsewhere"
        unrelated.mkdir()
        for index, reference in enumerate(references):
            with self.subTest(reference=reference):
                self.write_market_snapshot(markets, reference)
                for family, version in (
                    ("fifa", "test-v1"),
                    ("fifa-market-history", "odds-v1"),
                ):
                    (producer / "data/processed" / family / "current.json").write_text(
                        json.dumps({"dataset_version": version})
                    )
                shutil.copytree(producer, relocated)
                # The producer is unavailable, even for the legacy absolute link.
                hidden = self.root / "hidden-producer"
                producer.rename(hidden)
                try:
                    with chdir(unrelated):
                        database = self.root / f"relocated-{index}.duckdb"
                        result = import_database(
                            relocated / "data/processed/fifa",
                            database,
                            relocated / "data/processed/fifa-market-history",
                        )
                    self.assertEqual(result["matches"], 1)
                    self.assertEqual(len(load_match(database, "game")["markets"]), 3)
                finally:
                    hidden.rename(producer)
                    shutil.rmtree(relocated)

    def test_wrong_linked_snapshot_preserves_existing_database(self):
        import_database(self.source, self.database)
        before = hashlib.sha256(self.database.read_bytes()).hexdigest()
        wrong_source = self.root / "wrong-source"
        wrong_match = self.data["match"] | {"home_team_name": "Different fixture"}
        self.write_snapshot(
            wrong_source, {"matches": [wrong_match], "events": self.data["events"]}
        )
        market_source = self.root / "markets"
        self.write_market_snapshot(market_source, "../wrong-source")
        with self.assertRaisesRegex(ValueError, "different FIFA snapshot"):
            import_database(self.source, self.database, market_source, replace=True)
        self.assertEqual(hashlib.sha256(self.database.read_bytes()).hexdigest(), before)

    def test_missing_linked_snapshot_does_not_skip_validation(self):
        market_source = self.root / "markets"
        self.write_market_snapshot(market_source, "../missing-source")
        with self.assertRaises(FileNotFoundError):
            import_database(self.source, self.database, market_source)
        self.assertFalse(self.database.exists())

    def test_unknown_match_is_clear(self):
        import_database(self.source, self.database)
        with self.assertRaisesRegex(ValueError, "Unknown FIFA match"):
            load_match(self.database, "not-a-game")


if __name__ == "__main__":
    unittest.main()
