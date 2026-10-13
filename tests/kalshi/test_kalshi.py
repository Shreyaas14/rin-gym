from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import pyarrow as pa
import pyarrow.parquet as pq

from pipelines.fifa.client import AcquisitionError
from pipelines.kalshi.client import KalshiClient
from pipelines.kalshi.history import candles, trades, becker_trades
from pipelines.kalshi.resolve import resolve
from pipelines.kalshi.ingest import run
from pipelines.kalshi.verify import verify as verify_kalshi

START = datetime(2026, 5, 24, 15, tzinfo=timezone.utc)
WINDOW = dict(
    start_ts=START.timestamp() - 60,
    end_ts=START.timestamp() + 120,
    kickoff=START.timestamp(),
    terminal=START.timestamp() + 60,
)


def match():
    return dict(
        match_id="game",
        competition_id="2000000000",
        home_team_name="Liverpool",
        away_team_name="Brentford",
        scheduled_kickoff_utc=START,
    )


def contract(side="Liverpool", series="KXEPLGAME", date="May 24, 2026"):
    ticker = series + "-26MAY24LFCBRE-" + side.upper()
    return dict(
        series=series,
        endpoint="historical/markets",
        catalog_sha256="catalog",
        market=dict(
            ticker=ticker,
            event_ticker=series + "-26MAY24LFCBRE",
            market_type="binary",
            exchange_index=0,
            title="Liverpool vs Brentford Winner?",
            yes_sub_title=side,
            volume_fp="10.00",
            rules_primary=f"If {side} wins the Liverpool vs Brentford professional EPL soccer game originally scheduled for {date} after 90 minutes plus stoppage time (does not include extra time or penalties), then the market resolves to Yes.",
        ),
    )


def triplet():
    return [contract(s) for s in ("Liverpool", "Tie", "Brentford")]


class ResolutionTests(unittest.TestCase):
    def test_exact_pair_competition_date_and_all_three_sides(self):
        linked, audit = resolve([match()], triplet())
        self.assertEqual({i["hda_outcome"] for i in linked}, {"home", "draw", "away"})
        self.assertTrue(all(a["status"] == "mapped" for a in audit))
        for change in [
            dict(competition_id="17"),
            dict(away_team_name="Brentford Women"),
            dict(scheduled_kickoff_utc=datetime(2026, 5, 25, 15, tzinfo=timezone.utc)),
        ]:
            self.assertFalse(resolve([match() | change], triplet())[0])

    def test_wrong_subject_extra_time_and_duplicate_contracts_are_excluded(self):
        for field, value in [
            ("yes_sub_title", "Arsenal"),
            ("rules_primary", "If Liverpool wins including extra time"),
            ("event_ticker", "UNTRUSTED-26MAY24LFCBRE"),
        ]:
            entries = triplet()
            entries[0]["market"][field] = value
            linked, _ = resolve([match()], entries)
            self.assertFalse(linked)
        entries = triplet()
        duplicate = json.loads(json.dumps(entries[0]))
        duplicate["market"]["ticker"] += "-COPY"
        linked, audit = resolve([match()], entries + [duplicate])
        self.assertFalse(linked)
        self.assertIn("duplicate_proposition", {a["reason"] for a in audit})

    def test_ambiguous_fixtures_and_conflicting_copies_are_quarantined(self):
        self.assertFalse(
            resolve([match(), match() | {"match_id": "other"}], triplet())[0]
        )
        entries = triplet()
        copy = json.loads(json.dumps(entries[0]))
        copy["market"]["yes_sub_title"] = "Arsenal"
        self.assertFalse(resolve([match()], entries + [copy])[0])

    def test_props_keep_distinct_rules_and_at_orientation(self):
        entries = []
        for goals in (2.5, 3.5):
            e = contract("Over " + str(goals), "KXEPLTOTAL")
            e["market"]["rules_primary"] = (
                f"If over {goals} goals are scored in the Brentford at Liverpool professional EPL soccer game originally scheduled for May 24, 2026 after 90 minutes plus stoppage time, then the market resolves to Yes."
            )
            entries.append(e)
        linked, _ = resolve([match()], entries)
        self.assertEqual(len(linked), 2)
        self.assertTrue(all(i["hda_outcome"] is None for i in linked))

    def test_threshold_and_period_words_do_not_become_team_names(self):
        entries = []
        for series, prefix in [
            (
                "KXEPLGOAL",
                "If a player records at least 1 goal during the entire game (regulation, stoppage and any extra time periods) of",
            ),
            (
                "KXEPL1HSCORE",
                "If the score at the end of the 1st Half is Liverpool 0, Brentford 0 in",
            ),
        ]:
            e = contract(series=series)
            e["market"]["rules_primary"] = (
                prefix
                + " the Liverpool vs Brentford professional EPL soccer game originally scheduled for May 24, 2026, then the market resolves to Yes."
            )
            entries.append(e)
        single = contract(series="KXEPLBTTS")
        single["market"]["ticker"] = single["market"]["event_ticker"]
        entries.append(single)
        linked, _ = resolve([match()], entries)
        self.assertEqual(len(linked), 3)
        entries[0]["market"]["rules_primary"] = entries[0]["market"][
            "rules_primary"
        ].replace("May 24", "May 99")
        self.assertEqual(len(resolve([match()], entries)[0]), 2)

    def test_stage_qualified_fixture_remains_a_prop(self):
        e = contract(series="KXWCMOV")
        e["market"]["rules_primary"] = (
            "If Liverpool advances past Brentford in the Liverpool vs Brentford FIFA World Cup Round Of 32 soccer tie originally scheduled for May 24, 2026 via victory in Extra Time, then the market resolves to Yes."
        )
        linked, _ = resolve([match() | {"competition_id": "17"}], [e])
        self.assertEqual(len(linked), 1)
        self.assertIsNone(linked[0]["hda_outcome"])
        e["market"]["event_ticker"] = "KXWCMOV-26MAY99LFCBRE"
        e["market"]["ticker"] = e["market"]["event_ticker"] + "-EXTRA"
        linked, audit = resolve([match() | {"competition_id": "17"}], [e])
        self.assertFalse(linked)
        self.assertEqual(audit[0]["reason"], "ticker_rule_date_mismatch")


class HistoryTests(unittest.TestCase):
    def candle(self, t=None, price="0.4200", volume="1.00"):
        return dict(
            end_period_ts=t or int(START.timestamp()),
            price={"close": price, "previous": "0.8000"},
            volume=volume,
            yes_bid={"close": "0.41"},
            yes_ask={"close": "0.43"},
        )

    def test_candle_no_forward_fill_and_period_end_availability(self):
        data = {
            "candlesticks": [
                self.candle(),
                self.candle(START.timestamp() + 60, None),
                self.candle(START.timestamp() + 120, "0.42", "0.00"),
            ]
        }
        rows = candles(data, "ticker", WINDOW, "hash")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["timestamp"], START.timestamp())
        self.assertAlmostEqual(rows[0]["yes"], 0.42)
        self.assertAlmostEqual(rows[0]["no"], 0.58)
        self.assertEqual(rows[0]["price_type"], "trade_close_1m")

    def test_bad_prices_timestamps_and_crossed_quotes_fail(self):
        for price in ["NaN", "1.1", "-0.1"]:
            with self.assertRaises(ValueError):
                candles({"candlesticks": [self.candle(price=price)]}, "t", WINDOW, "h")
        row = self.candle()
        row["yes_bid"]["close"] = "0.9"
        with self.assertRaisesRegex(ValueError, "Crossed"):
            candles({"candlesticks": [row]}, "t", WINDOW, "h")
        with self.assertRaises(ValueError):
            candles(
                {"candlesticks": [self.candle(START.timestamp() + 0.5)]},
                "t",
                WINDOW,
                "h",
            )

    def test_live_cents_and_explicit_dollars_have_identical_units(self):
        old = self.candle(price=42)
        old["yes_bid"]["close"] = 41
        old["yes_ask"]["close"] = 43
        modern = self.candle(price=99)
        modern["price"]["close_dollars"] = "0.42"
        modern["volume_fp"] = modern.pop("volume")
        expected = candles({"candlesticks": [self.candle()]}, "t", WINDOW, "h")
        self.assertEqual(candles({"candlesticks": [old]}, "t", WINDOW, "h"), expected)
        self.assertEqual(
            candles({"candlesticks": [modern]}, "t", WINDOW, "h"), expected
        )

    def trade(self, **extra):
        return dict(
            ticker="ticker",
            trade_id="id",
            created_time="2026-05-24T15:00:00.123Z",
            yes_price=42,
            no_price=58,
            count=2,
            **extra,
        )

    def test_becker_cents_and_api_dollars_agree(self):
        old = self.trade()
        new = {
            k: v for k, v in old.items() if k not in ("yes_price", "no_price", "count")
        }
        new.update(
            yes_price_dollars="0.4200", no_price_dollars="0.5800", count_fp="2.00"
        )
        self.assertEqual(
            trades([(old, "h")], "ticker", WINDOW),
            trades([(new, "h")], "ticker", WINDOW),
        )
        with tempfile.TemporaryDirectory() as tmp:
            pq.write_table(
                pa.Table.from_pylist(
                    [old, old | {"ticker": "other", "trade_id": "other"}]
                ),
                Path(tmp) / "trades.parquet",
            )
            grouped, sources = becker_trades(tmp, ["ticker"])
            self.assertEqual(set(grouped), {"ticker"})
            self.assertEqual(len(sources), 1)
            self.assertEqual(
                trades(grouped["ticker"], "ticker", WINDOW)[0]["yes"], 0.42
            )

    def test_trade_conflicts_and_ambiguous_simultaneous_prices(self):
        row = self.trade()
        self.assertEqual(len(trades([(row, "h"), (row, "h")], "ticker", WINDOW)), 1)
        bad = row | {"yes_price": 50, "no_price": 50}
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            trades([(row, "h"), (bad, "h")], "ticker", WINDOW)
        self.assertEqual(
            trades([(row, "h"), (bad | {"trade_id": "other"}, "h")], "ticker", WINDOW),
            [],
        )
        with self.assertRaises(ValueError):
            trades([(row | {"ticker": "other"}, "h")], "ticker", WINDOW)


class AcquisitionTests(unittest.TestCase):
    def test_concurrent_rate_limits_share_one_backoff(self):
        c = KalshiClient("/tmp/unused-kalshi-test")
        with patch.object(
            c, "transport", side_effect=HTTPError("url", 429, "limit", {}, None)
        ), patch("pipelines.kalshi.client.time.monotonic", return_value=100), patch(
            "pipelines.kalshi.client.time.sleep"
        ):
            for _ in range(3):
                with self.assertRaises(HTTPError):
                    c.throttled_open("request")
        self.assertAlmostEqual(c.interval, 0.3)
        self.assertEqual(c.cooldown_until, 130)

    def test_offline_snapshot_verification_and_semantic_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "fifa"
            source.mkdir()
            pq.write_table(
                pa.Table.from_pylist(
                    [
                        match()
                        | {
                            "regulation_home_score": 1,
                            "regulation_away_score": 0,
                        }
                    ]
                ),
                source / "matches.parquet",
            )
            pq.write_table(
                pa.Table.from_pylist(
                    [
                        dict(
                            match_id="game",
                            event_kind="period_start",
                            period_code=3,
                            event_time_utc=START,
                        )
                    ]
                ),
                source / "events.parquet",
            )
            files = {
                name: {
                    "sha256": hashlib.sha256(
                        (source / (name + ".parquet")).read_bytes()
                    ).hexdigest()
                }
                for name in ("matches", "events")
            }
            (source / "manifest.json").write_text(json.dumps({"files": files}))
            args = SimpleNamespace(
                dataset=source,
                output=root / "out",
                cache=root / "raw",
                offline=True,
                refresh=False,
                catalog_only=False,
                history="candles",
                becker_trades=None,
                workers=1,
            )
            row = dict(
                timestamp=START.timestamp(),
                yes=0.42,
                no=0.58,
                bid=0.41,
                ask=0.43,
                volume=1,
                price_type="trade_close_1m",
                source_response_sha256="history",
            )
            with patch("pipelines.kalshi.ingest.verify"), patch(
                "pipelines.kalshi.ingest.catalog",
                return_value=(triplet(), [{"sha256": "catalog"}]),
            ), patch(
                "pipelines.kalshi.ingest.history",
                return_value=([row], [{"sha256": "history"}]),
            ):
                out = run(args)
            result = verify_kalshi(out, source)
            self.assertEqual((result["markets"], result["prices"]), (3, 6))
            self.assertEqual(result["matches_with_hda"], 1)
            prices = out / "prices.parquet"
            table = pq.read_table(prices)
            rows = table.to_pylist()
            rows[0]["token_id"] = "kalshi:wrong:yes"
            pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), prices)
            manifest = json.loads((out / "manifest.json").read_text())
            manifest["files"]["prices"]["sha256"] = hashlib.sha256(
                prices.read_bytes()
            ).hexdigest()
            (out / "manifest.json").write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "orphan prices"):
                verify_kalshi(out, source)

    def test_repeated_cursor_fails_instead_of_truncating(self):
        c = KalshiClient("/tmp/unused-kalshi-test")
        with patch.object(
            c,
            "request",
            return_value=(
                {"markets": [{"ticker": "a"}], "cursor": "same"},
                {"sha256": "h"},
            ),
        ):
            with self.assertRaises(AcquisitionError):
                list(c.pages("markets", "markets"))

    def test_transport_failure_never_replaces_current_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "fifa"
            source.mkdir()
            output = root / "out"
            output.mkdir()
            (output / "current.json").write_text('{"dataset_version":"previous"}')
            pq.write_table(pa.Table.from_pylist([match()]), source / "matches.parquet")
            pq.write_table(
                pa.Table.from_pylist(
                    [
                        dict(
                            match_id="game",
                            event_kind="period_start",
                            period_code=3,
                            event_time_utc=START,
                        )
                    ]
                ),
                source / "events.parquet",
            )
            (source / "manifest.json").write_text('{"files":{}}')
            args = SimpleNamespace(
                dataset=source,
                output=output,
                cache=root / "raw",
                offline=True,
                refresh=False,
                catalog_only=False,
                history="candles",
                becker_trades=None,
                workers=1,
            )
            with patch("pipelines.kalshi.ingest.verify"), patch(
                "pipelines.kalshi.ingest.catalog", return_value=(triplet(), [])
            ), patch(
                "pipelines.kalshi.ingest.history",
                side_effect=OSError("transport failed"),
            ):
                with self.assertRaises(OSError):
                    run(args)
            self.assertEqual(
                json.loads((output / "current.json").read_text())["dataset_version"],
                "previous",
            )


if __name__ == "__main__":
    unittest.main()
