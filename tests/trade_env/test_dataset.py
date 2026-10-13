"""Release adapter tests use tiny Parquet tables, never network or the full release."""

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

import pyarrow as pa
import pyarrow.parquet as pq

from app.trade_env.dataset import EVENT_FIELDS, load_release, market_rate
from app.trade_env.tasks import load_records


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.record = load_records(Path(__file__).parent / "fixtures/records.jsonl.gz")[
            0
        ]
        r = self.record

        def utc(t):
            return datetime.fromtimestamp(t, timezone.utc)

        self.mapping = self.root / "data/processed/fifa-markets"
        self.history = self.root / "data/processed/fifa-market-history"
        for base in (self.mapping, self.history):
            (base / "snapshots/v1").mkdir(parents=True)
            (base / "current.json").write_text(json.dumps({"dataset_version": "v1"}))
        contracts = [
            {"outcome": s, "market_id": str(i), "token_id": s, "slug": "match"}
            for i, s in enumerate(("home", "draw", "away"))
        ]
        match = {
            "match_id": r["match_id"],
            "source": r["source"],
            "competition_id": r["competition_id"],
            "stage_name": r["stage_name"],
            "home_team_id": r["home_team_id"],
            "away_team_id": r["away_team_id"],
            "home_team_name": r["home"],
            "away_team_name": r["away"],
            "scheduled_kickoff_utc": datetime.fromisoformat(
                r["scheduled_utc"].replace("Z", "+00:00")
            ),
            "regulation_home_score": r["regulation"]["home_goals"],
            "regulation_away_score": r["regulation"]["away_goals"],
            "went_to_extra_time": r["extra_time"],
            "timeline_status": "ok",
            "event_count": len(r["events"]),
            "prediction_markets": contracts,
        }
        events = [
            {
                **{v: e.get(k) for k, v in EVENT_FIELDS.items()},
                "match_id": r["match_id"],
                "event_time_utc": utc(e["t"]),
                "source_event_json": json.dumps({"Timestamp": e["utc"]}),
            }
            for e in r["events"]
        ]
        self.write(self.mapping, "matches", [match])
        self.write(self.mapping, "events", events)
        metadata = [
            {
                "match_id": r["match_id"],
                "market_id": c["market_id"],
                "hda_outcome": c["outcome"],
                "tokens": [{"outcome": "Yes", "token_id": c["token_id"]}],
                "source_market_json": json.dumps(
                    {
                        "id": c["market_id"],
                        "groupItemTitle": "Draw"
                        if c["outcome"] == "draw"
                        else r[c["outcome"]],
                        "outcomes": json.dumps(["Yes", "No"]),
                        "clobTokenIds": json.dumps(
                            [c["token_id"], "no-" + c["token_id"]]
                        ),
                        "feesEnabled": True,
                        "feeSchedule": {"rate": 0.03, "exponent": 1},
                    }
                ),
            }
            for c in contracts
        ]
        self.write(self.history, "markets", metadata)
        self.prices = [
            {"match_id": r["match_id"], "token_id": s, "timestamp": t, "price": p}
            for s, series in r["prices"].items()
            for t, p in series
        ]
        self.write(self.history, "prices", self.prices)

    def write(self, base, name, rows):
        pq.write_table(
            pa.Table.from_pylist(rows), base / f"snapshots/v1/{name}.parquet"
        )

    def test_projection_preserves_events_clocks_results_and_prices(self):
        (actual,) = load_release(self.root)
        for key in (
            "events",
            "periods",
            "regulation",
            "prices",
            "home",
            "away",
            "stage_name",
        ):
            self.assertEqual(actual[key], self.record[key], key)
        self.assertEqual(
            actual["fee_rates"], dict.fromkeys(("home", "draw", "away"), 0.03)
        )

    def test_duplicate_prices_are_rejected(self):
        self.write(self.history, "prices", self.prices + self.prices[:1])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            load_release(self.root)

    def test_wrong_fixture_prices_are_rejected(self):
        self.prices[0]["match_id"] = "wrong"
        self.write(self.history, "prices", self.prices)
        with self.assertRaisesRegex(ValueError, "Invalid release price"):
            load_release(self.root)

    def test_swapped_outcomes_are_rejected(self):
        path = self.mapping / "snapshots/v1/matches.parquet"
        rows = pq.read_table(path).to_pylist()
        home, _, away = rows[0]["prediction_markets"]
        home["outcome"], away["outcome"] = "away", "home"
        self.write(self.mapping, "matches", rows)
        with self.assertRaisesRegex(ValueError, "contract identity"):
            load_release(self.root)

    def test_consistently_swapped_labels_still_fail_on_contract_team(self):
        path = self.history / "snapshots/v1/markets.parquet"
        rows = pq.read_table(path).to_pylist()
        raw = json.loads(rows[0]["source_market_json"])
        raw["groupItemTitle"] = self.record["away"]
        rows[0]["source_market_json"] = json.dumps(raw)
        self.write(self.history, "markets", rows)
        with self.assertRaisesRegex(ValueError, "contract identity"):
            load_release(self.root)

    def test_wrong_market_and_missing_metadata_fail(self):
        path = self.history / "snapshots/v1/markets.parquet"
        rows = pq.read_table(path).to_pylist()
        self.write(self.history, "markets", rows[1:])
        with self.assertRaisesRegex(ValueError, "Missing release market metadata"):
            load_release(self.root)

    def test_fee_metadata_is_explicit(self):
        self.assertEqual(market_rate({"feesEnabled": False}), 0)
        for raw in (
            {},
            {"feesEnabled": True},
            {"feesEnabled": True, "feeSchedule": {"rate": 0.03, "exponent": 2}},
        ):
            with self.assertRaisesRegex(ValueError, "fee schedule"):
                market_rate(raw)


if __name__ == "__main__":
    unittest.main()
