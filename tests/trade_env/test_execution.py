"""Acceptance cases for fee provenance, liquidity roles and cash accounting."""

import importlib.util
import unittest


class ExecutionTests(unittest.TestCase):
    def test_market_specific_fee_curve_and_rebate_eligibility(self):
        self.assertIsNotNone(
            importlib.util.find_spec("app.trade_env.execution"),
            "maker/taker execution module is missing",
        )
        from app.trade_env.execution import FeeSchedule

        f = FeeSchedule(
            "m", 0, 100, 0, 0.0, 0.05, "historical_verified", "official fixture"
        )
        self.assertEqual(f.charges("taker", 0.5, 100), (1.25, 0.0))
        self.assertEqual(f.charges("maker", 0.5, 100), (0.0, 0.0))
        rebate = FeeSchedule(
            "m",
            0,
            100,
            0,
            0,
            0.05,
            "historical_verified",
            "fixture",
            maker_rebate_rate=0.01,
            rebate_evidence="eligible and calculation verified",
        )
        self.assertEqual(rebate.charges("maker", 0.5, 100), (0, 0.25))
        unsupported = FeeSchedule(
            "m",
            0,
            100,
            0,
            0,
            0.05,
            "historical_verified",
            "fixture",
            maker_rebate_rate=0.01,
        )
        self.assertEqual(unsupported.charges("maker", 0.5, 100), (0, 0))

    def test_current_snapshot_cannot_price_historical_execution(self):
        from app.trade_env.execution import FeeSchedule, FeeRegistry, UnknownFees

        f = FeeSchedule(
            "m", 100, None, 100, 0, 0.05, "current_snapshot", "current docs"
        )
        registry = FeeRegistry([f])
        with self.assertRaises(UnknownFees):
            registry.at("m", 99)
        self.assertEqual(registry.at("m", 100), f)

    def test_post_only_rejects_cross_and_limit_cross_is_taker(self):
        from app.trade_env.execution import FeeSchedule, Book, Order, ExecutionEngine

        fee = FeeSchedule("m", 0, None, 0, 0, 0.05, "historical_verified", "fixture")
        book = Book(1, ((0.49, 10),), ((0.51, 10),), tick_size=0.01, min_size=1)
        engine = ExecutionEngine([fee])
        post = engine.execute(
            Order("x", "m", "buy", 2, 0.51, 0, expiry_s=10, post_only=True),
            [book],
            [],
            scenario="observed",
        )
        self.assertEqual(post["status"], "post_only_rejected")
        self.assertEqual(post["filled_quantity"], 0)
        cross = engine.execute(
            Order("y", "m", "buy", 2, 0.51, 0, expiry_s=10),
            [book],
            [],
            scenario="observed",
        )
        self.assertEqual(cross["fills"][0]["liquidity_role"], "taker")
        self.assertAlmostEqual(cross["fills"][0]["spread_cost"], 0.02)

    def test_touch_is_not_fill_and_queue_requires_aggressor_volume(self):
        from app.trade_env.execution import (
            FeeSchedule,
            Book,
            Order,
            Trade,
            ExecutionEngine,
        )

        fee = FeeSchedule("m", 0, None, 0, 0, 0.05, "historical_verified", "fixture")
        engine = ExecutionEngine([fee])
        book = Book(1, ((0.49, 3),), ((0.51, 5),))
        order = Order("a", "m", "buy", 2, 0.49, 0, expiry_s=10, post_only=True)
        untouched = engine.execute(
            order, [book], [], scenario="observed", queue_ahead=3
        )
        self.assertEqual(untouched["filled_quantity"], 0)
        result = engine.execute(
            order,
            [book],
            [Trade(2, 0.49, 4, "sell")],
            scenario="observed",
            queue_ahead=3,
        )
        self.assertEqual(result["filled_quantity"], 1)
        self.assertEqual(result["unfilled_quantity"], 1)
        self.assertEqual(result["fills"][0]["liquidity_role"], "maker")
        self.assertEqual(result["status"], "expired_partial")

    def test_actual_bid_ask_is_not_charged_a_second_spread(self):
        from app.trade_env.execution import (
            FeeSchedule,
            Book,
            Order,
            ExecutionEngine,
            Account,
        )

        fee = FeeSchedule("m", 0, None, 0, 0, 0.05, "historical_verified", "fixture")
        r = ExecutionEngine([fee]).execute(
            Order("x", "m", "buy", 2, 0.6, 0, expiry_s=10),
            [Book(1, ((0.49, 10),), ((0.51, 1), (0.52, 1)))],
            [],
            scenario="observed",
        )
        a = Account()
        a.apply("home", "buy", r["fills"])
        a.settle("home")
        self.assertAlmostEqual(a.net_pnl, 2 - 1.03 - 0.024975)
        self.assertAlmostEqual(a.net_pnl, a.gross_pnl - a.fees + a.rebates)
        self.assertAlmostEqual(
            a.midpoint_pnl - a.spread_slippage - a.fees + a.rebates, a.net_pnl
        )

    def test_unknown_schedule_blocks_even_when_edge_is_large(self):
        from app.trade_env.execution import Book, Order, ExecutionEngine

        r = ExecutionEngine([]).execute(
            Order("x", "m", "buy", 1, 0.9, 0, expiry_s=10),
            [Book(1, ((0.4, 10),), ((0.5, 10),))],
            [],
            scenario="observed",
        )
        self.assertEqual(r["status"], "unknown_fee_schedule")
        self.assertEqual(r["filled_quantity"], 0)


class PolicyTests(unittest.TestCase):
    def fixture(self):
        from app.trade_env.execution import Book, FeeSchedule, ExecutionEngine

        fee = FeeSchedule("m", 0, None, 0, 0, 0.05, "historical_verified", "fixture")
        books = [
            Book(0, ((0.49, 10),), ((0.51, 10),)),
            Book(1, ((0.49, 10),), ((0.51, 10),)),
            Book(10, ((0.49, 10),), ((0.50, 10),)),
        ]
        return ExecutionEngine([fee]), books

    def test_hybrid_crosses_only_remaining_quantity_after_cancel_and_edge_check(self):
        from app.trade_env.execution import (
            ExecutionPolicy,
            execute_policy,
            Order,
            Trade,
        )

        engine, books = self.fixture()
        r = execute_policy(
            engine,
            Order("hy", "m", "buy", 2, 0.7, 0, expiry_s=10),
            0.6,
            books,
            [Trade(2, 0.49, 0.5, "sell")],
            ExecutionPolicy("hybrid", uncertainty_margin=0.02),
            scenario="observed",
            queue_ahead=0,
        )
        self.assertEqual(r["filled_quantity"], 2)
        self.assertEqual([f["liquidity_role"] for f in r["fills"]], ["maker", "taker"])
        self.assertEqual(r["fills"][1]["quantity"], 1.5)
        self.assertGreaterEqual(r["fills"][1]["timestamp"], 10)

    def test_hybrid_does_not_cross_without_remaining_after_fee_edge(self):
        from app.trade_env.execution import ExecutionPolicy, execute_policy, Order

        engine, books = self.fixture()
        r = execute_policy(
            engine,
            Order("hy", "m", "buy", 2, 0.7, 0, expiry_s=10),
            0.51,
            books,
            [],
            ExecutionPolicy("hybrid", uncertainty_margin=0.02),
            scenario="observed",
            queue_ahead=0,
        )
        self.assertEqual(r["filled_quantity"], 0)
        self.assertIn(
            r["orders"][-1]["status"], ["edge_below_margin", "signal_expired"]
        )

    def test_fee_aware_sell_quotes_and_signal_expiry(self):
        from app.trade_env.execution import (
            ExecutionPolicy,
            execute_policy,
            Order,
            expected_cost,
        )

        engine, books = self.fixture()
        fee = engine.fees.at("m", 0)
        q = expected_cost(0.6, "sell", 2, books[0], fee, "taker")
        self.assertAlmostEqual(q["net_edge_per_share"], 0.49 - 0.012495 - 0.6)
        r = execute_policy(
            engine,
            Order("h", "m", "buy", 2, 0.7, 0, expiry_s=10),
            0.7,
            books,
            [],
            ExecutionPolicy("hybrid", signal_valid_s=5),
            scenario="observed",
            queue_ahead=0,
        )
        self.assertEqual(r["orders"][-1]["status"], "signal_expired")


class BoundaryTests(unittest.TestCase):
    def test_taker_does_not_hide_unknown_fee_schedule_at_fill_time(self):
        from app.trade_env.execution import (
            FeeSchedule,
            ExecutionEngine,
            ExecutionPolicy,
            Book,
            Order,
            execute_policy,
        )

        fee = FeeSchedule("m", 0, 1, 0, 0, 0.05, "historical_verified", "fixture")
        books = [
            Book(0, ((0.49, 10),), ((0.51, 10),)),
            Book(1, ((0.49, 10),), ((0.51, 10),)),
        ]
        r = execute_policy(
            ExecutionEngine([fee]),
            Order("a", "m", "buy", 1, 1, 0),
            0.8,
            books,
            [],
            ExecutionPolicy("taker"),
            scenario="observed",
        )
        self.assertEqual(r["orders"][0]["status"], "unknown_fee_schedule")

    def test_minimum_size_stops_one_share_with_recorded_five_share_rule(self):
        from app.trade_env.execution import FeeSchedule, Book, Order, ExecutionEngine

        fee = FeeSchedule("m", 0, None, 0, 0, 0.03, "historical_verified", "fixture")
        r = ExecutionEngine([fee]).execute(
            Order("a", "m", "buy", 1, 0.6, 0),
            [Book(1, ((0.49, 10),), ((0.51, 10),), min_size=5)],
            [],
            scenario="observed",
        )
        self.assertEqual(r["status"], "below_minimum_size")
        self.assertEqual(r["filled_quantity"], 0)

    def test_fee_rounding_is_configurable_separately_from_exact_legacy(self):
        from app.trade_env.execution import FeeSchedule

        current = FeeSchedule(
            "m", 0, None, 0, 0, 0.05, "historical_verified", "fixture", precision=5
        )
        self.assertEqual(current.charges("taker", 0.51, 1), (0.0125, 0))
        self.assertEqual(current.charges("taker", 0.00001, 0.01), (0, 0))

    def test_sell_accounting_rejects_overfill_and_settlement_reuse(self):
        from app.trade_env.execution import Account

        a = Account()
        with self.assertRaises(ValueError):
            a.apply("home", "sell", [{"quantity": 1}])
        a.settle("home")
        with self.assertRaises(ValueError):
            a.settle("home")


class ConstraintUnitsTests(unittest.TestCase):
    def test_explicit_notional_minimum_cannot_be_treated_as_shares(self):
        from app.trade_env.execution import Book, Order, FeeSchedule, ExecutionEngine
        from dataclasses import fields

        self.assertIn(
            "min_notional",
            [f.name for f in fields(Book)],
            "USD minimum support missing",
        )
        fee = FeeSchedule("m", 0, None, 0, 0, 0.03, "historical_verified", "fixture")
        book = Book(1, ((0.49, 20),), ((0.51, 20),), min_notional=5)
        result = ExecutionEngine([fee]).execute(
            Order("a", "m", "buy", 6, 0.51, 0), [book], [], scenario="observed"
        )
        self.assertEqual(result["status"], "below_minimum_notional")


class ReceiptTests(unittest.TestCase):
    def test_taker_receipts_are_strict_json_even_with_unbounded_queue(self):
        import json
        import math
        from app.trade_env.execution import FeeSchedule, Book, Order, ExecutionEngine

        fee = FeeSchedule("m", 0, None, 0, 0, 0.05, "historical_verified", "fixture")
        r = ExecutionEngine([fee]).execute(
            Order("a", "m", "buy", 1, 0.6, 0),
            [Book(1, ((0.49, 2),), ((0.51, 2),))],
            [],
            scenario="observed",
            queue_ahead=math.inf,
        )
        self.assertEqual(r["queue_ahead_assumed"], "unbounded")
        json.dumps(r, allow_nan=False)


class ReviewedRegressionTests(unittest.TestCase):
    def test_resting_maker_is_cancelled_at_signal_deadline(self):
        from app.trade_env.execution import (
            FeeSchedule,
            Book,
            Order,
            Trade,
            ExecutionEngine,
            ExecutionPolicy,
            execute_policy,
        )

        fee = FeeSchedule("m", 0, None, 0, 0, 0.05, "historical_verified", "fixture")
        books = [
            Book(0, ((0.49, 10),), ((0.51, 10),)),
            Book(1, ((0.49, 10),), ((0.51, 10),)),
        ]
        r = execute_policy(
            ExecutionEngine([fee]),
            Order("m", "m", "buy", 1, 1, 0, expiry_s=10),
            0.8,
            books,
            [Trade(7, 0.49, 1, "sell")],
            ExecutionPolicy("maker", signal_valid_s=5),
            scenario="observed",
            queue_ahead=0,
        )
        self.assertEqual(r["filled_quantity"], 0)
        self.assertEqual(r["orders"][0]["expiry_timestamp"], 5)

    def test_announced_fee_transition_is_included_in_edge_estimate(self):
        from app.trade_env.execution import (
            FeeSchedule,
            Book,
            Order,
            ExecutionEngine,
            ExecutionPolicy,
            execute_policy,
        )

        fees = [
            FeeSchedule("m", 0, 1, 0, 0, 0, "historical_verified", "published fixture"),
            FeeSchedule(
                "m", 1, None, 0, 0, 0.05, "historical_verified", "published fixture"
            ),
        ]
        books = [
            Book(0, ((0.49, 10),), ((0.50, 10),)),
            Book(1, ((0.49, 10),), ((0.50, 10),)),
        ]
        r = execute_policy(
            ExecutionEngine(fees),
            Order("x", "m", "buy", 1, 1, 0),
            0.525,
            books,
            [],
            ExecutionPolicy("taker", uncertainty_margin=0.02),
            scenario="observed",
        )
        self.assertEqual(r["filled_quantity"], 0)
        self.assertEqual(r["orders"][0]["status"], "edge_below_margin")

    def test_zero_depth_levels_do_not_hide_executable_quote(self):
        from app.trade_env.execution import (
            FeeSchedule,
            Book,
            Order,
            ExecutionEngine,
            ExecutionPolicy,
            execute_policy,
        )

        fee = FeeSchedule("m", 0, None, 0, 0, 0.05, "historical_verified", "fixture")
        book = Book(0, ((0.39, 10),), ((0.40, 0), (0.42, 10)))
        r = execute_policy(
            ExecutionEngine([fee], latency_s=0),
            Order("x", "m", "buy", 1, 1, 0),
            0.7,
            [book],
            [],
            ExecutionPolicy("taker"),
            scenario="observed",
        )
        self.assertEqual(r["filled_quantity"], 1)
        self.assertEqual(r["fills"][0]["price"], 0.42)


if __name__ == "__main__":
    unittest.main()
