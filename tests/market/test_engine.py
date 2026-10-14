"""Accounting and matching invariants for the paper exchange."""

import copy
import random
import unittest

from app.market.engine import Exchange


class ExchangeTests(unittest.TestCase):
    def setUp(self):
        self.exchange = Exchange()

    def assert_invariants(self, exchange=None):
        exchange = exchange or self.exchange
        snapshot = exchange.snapshot(mark=50)
        self.assertEqual(sum(a["cash"] for a in snapshot["accounts"].values()), 300_000)
        self.assertEqual(sum(a["position"] for a in snapshot["accounts"].values()), 0)
        self.assertEqual(sum(a["pnl"] for a in snapshot["accounts"].values()), 0)
        for owner, account in snapshot["accounts"].items():
            self.assertGreaterEqual(account["available"], 0)
            self.assertGreaterEqual(account["cash"], 0)
            own = [o for o in snapshot["orders"] if o["owner"] == owner]
            buys = sum(o["remaining"] for o in own if o["side"] == "buy")
            sells = sum(o["remaining"] for o in own if o["side"] == "sell")
            self.assertLessEqual(account["position"] + buys, 100)
            self.assertGreaterEqual(account["position"] - sells, -100)

    def test_price_time_priority_partial_and_multilevel(self):
        first = self.exchange.submit("anchor", "sell", 60, 4)["order"]
        second = self.exchange.submit("pulse", "sell", 60, 5)["order"]
        third = self.exchange.submit("anchor", "sell", 63, 6)["order"]
        result = self.exchange.submit("user", "buy", 65, 12)
        self.assertEqual([(f["price"], f["size"], f["maker"]) for f in result["fills"]],
                         [(60, 4, "anchor"), (60, 5, "pulse"), (63, 3, "anchor")])
        self.assertEqual(self.exchange.orders[first["id"]].status, "filled")
        self.assertEqual(self.exchange.orders[second["id"]].status, "filled")
        self.assertEqual(self.exchange.orders[third["id"]].remaining, 3)
        self.assertEqual(self.exchange.snapshot()["accounts"]["user"]["cash"], 99_271)
        self.assert_invariants()

    def test_sell_taker_gets_highest_bid_maker_price(self):
        self.exchange.submit("anchor", "buy", 58, 5)
        self.exchange.submit("pulse", "buy", 62, 4)
        result = self.exchange.submit("user", "sell", 57, 7)
        self.assertEqual([(f["price"], f["size"]) for f in result["fills"]], [(62, 4), (58, 3)])
        self.assertEqual(result["order"]["status"], "filled")
        self.assert_invariants()

    def test_gtc_remainder_ioc_and_post_only(self):
        self.exchange.submit("anchor", "sell", 62, 5, "post_only")
        before = copy.deepcopy(self.exchange.__dict__)
        with self.assertRaises(ValueError):
            self.exchange.submit("user", "buy", 62, 8, "post_only")
        self.assertEqual(before, self.exchange.__dict__)
        result = self.exchange.submit("user", "buy", 62, 8, "IOC")
        self.assertEqual(result["order"]["remaining"], 3)
        self.assertEqual(result["order"]["status"], "cancelled")
        self.assertEqual(self.exchange.snapshot()["accounts"]["user"]["reserved"], 0)
        empty = self.exchange.submit("user", "buy", 61, 2, "IOC")
        self.assertEqual(empty["fills"], [])
        self.assertEqual(empty["order"]["status"], "cancelled")
        resting = self.exchange.submit("pulse", "buy", 61, 7)
        self.assertEqual(resting["order"]["status"], "open")
        self.assert_invariants()

    def test_self_match_cancels_incoming_remainder(self):
        self.exchange.submit("pulse", "sell", 59, 2)
        own = self.exchange.submit("user", "sell", 60, 3)["order"]
        self.exchange.submit("anchor", "sell", 61, 4)
        result = self.exchange.submit("user", "buy", 65, 8)
        self.assertEqual(len(result["fills"]), 1)
        self.assertEqual(result["fills"][0]["size"], 2)
        self.assertEqual(result["order"]["status"], "cancelled")
        self.assertEqual(result["order"]["remaining"], 6)
        self.assertEqual(self.exchange.orders[own["id"]].remaining, 3)
        self.assert_invariants()

    def test_pending_position_limits_and_cancel_ownership(self):
        order = self.exchange.submit("user", "buy", 55, 100)["order"]
        for side in ("buy",):
            with self.assertRaises(ValueError):
                self.exchange.submit("user", side, 54, 1)
        with self.assertRaises(ValueError):
            self.exchange.cancel("anchor", order["id"])
        self.assertEqual(self.exchange.orders[order["id"]].status, "open")
        self.exchange.cancel("user", order["id"])
        self.assertEqual(self.exchange.snapshot()["accounts"]["user"]["reserved"], 0)
        self.exchange.submit("user", "sell", 60, 100)
        with self.assertRaises(ValueError):
            self.exchange.submit("user", "sell", 61, 1)
        self.assert_invariants()

    def test_collateral_and_pending_reservations(self):
        # Lower balances exercise the same ledger rules near available cash.
        self.exchange.accounts["user"].cash = 1_000
        self.exchange.accounts["anchor"].cash += 99_000
        self.exchange.submit("anchor", "buy", 62, 10)
        self.exchange.submit("user", "sell", 62, 10)
        account = self.exchange.snapshot(mark=62)["accounts"]["user"]
        self.assertEqual((account["cash"], account["collateral"], account["available"]), (1_620, 1_000, 620))
        order = self.exchange.submit("user", "sell", 50, 12)["order"]
        self.assertEqual(self.exchange.snapshot()["accounts"]["user"]["reserved"], 600)
        before = copy.deepcopy(self.exchange.__dict__)
        with self.assertRaises(ValueError):
            self.exchange.submit("user", "buy", 21, 1)
        self.assertEqual(before, self.exchange.__dict__)
        self.exchange.cancel("user", order["id"])
        self.assertEqual(self.exchange.snapshot()["accounts"]["user"]["available"], 620)
        self.assert_invariants()

    def test_both_settlements_reconcile_and_are_idempotent(self):
        for outcome in (0, 1):
            with self.subTest(outcome=outcome):
                exchange = Exchange()
                exchange.submit("anchor", "sell", 62, 10)
                exchange.submit("user", "buy", 62, 10)
                self.assert_invariants(exchange)
                exchange.submit("pulse", "buy", 40, 4)
                settled = exchange.settle(outcome)
                user_cash = 99_380 + 1_000 * outcome
                self.assertEqual(settled["accounts"]["user"]["cash"], user_cash)
                self.assertEqual(settled["orders"], [])
                self.assertTrue(settled["settled"])
                self.assertEqual(settled["outcome"], outcome)
                before = copy.deepcopy(exchange.__dict__)
                self.assertEqual(exchange.settle(outcome), settled)
                self.assertEqual(before, exchange.__dict__)
                with self.assertRaises(ValueError):
                    exchange.settle(1 - outcome)
                with self.assertRaises(ValueError):
                    exchange.submit("user", "buy", 50, 1)
                with self.assertRaises(ValueError):
                    exchange.cancel_all()
                self.assert_invariants(exchange)

    def test_invalid_parameters_do_not_mutate(self):
        invalid = [
            ("user", "buy", True, 1, "GTC"), ("user", "buy", 50.0, 1, "GTC"),
            ("user", "buy", 0, 1, "GTC"), ("user", "buy", 100, 1, "GTC"),
            ("user", "buy", 50, True, "GTC"), ("user", "buy", 50, 1.0, "GTC"),
            ("user", "buy", 50, 0, "GTC"), ("user", "buy", 50, -1, "GTC"),
            ("unknown", "buy", 50, 1, "GTC"), ("user", "NO", 50, 1, "GTC"),
            ("user", "buy", 50, 1, "DAY"),
        ]
        before = copy.deepcopy(self.exchange.__dict__)
        for args in invalid:
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.exchange.submit(*args)
            self.assertEqual(before, self.exchange.__dict__)
        for y in (True, False, 0.0, 2, None):
            with self.subTest(outcome=y), self.assertRaises(ValueError):
                self.exchange.settle(y)
            self.assertEqual(before, self.exchange.__dict__)

    def test_snapshots_are_detached_and_marked_in_cents(self):
        self.exchange.submit("anchor", "buy", 58, 2)
        self.exchange.submit("pulse", "sell", 63, 3)
        self.assertIsNone(Exchange().snapshot()["accounts"]["user"]["equity"])
        self.exchange.submit("user", "buy", 63, 1)
        snapshot = self.exchange.snapshot()
        self.assertEqual(snapshot["accounts"]["user"]["equity"], 99_997.5)
        snapshot["asks"][0]["remaining"] = 0
        snapshot["trades"][0]["size"] = 100
        self.assertEqual(self.exchange.snapshot()["asks"][0]["remaining"], 2)
        self.assertEqual(self.exchange.trades[0]["size"], 1)
        for mark in (True, float("nan"), float("inf"), -1, 101):
            with self.assertRaises(ValueError):
                self.exchange.snapshot(mark)

    def test_randomized_orders_preserve_invariants_after_every_command(self):
        rng = random.Random(321)
        for _ in range(600):
            owner = rng.choice(list(self.exchange.accounts))
            if rng.random() < 0.18:
                self.exchange.cancel_all(owner)
            else:
                try:
                    self.exchange.submit(owner, rng.choice(("buy", "sell")), rng.randint(1, 99),
                                         rng.randint(1, 35), rng.choice(("GTC", "IOC", "post_only")))
                except ValueError:
                    pass
            self.assert_invariants()
        for outcome in (0, 1):
            exchange = copy.deepcopy(self.exchange)
            exchange.settle(outcome)
            self.assert_invariants(exchange)


if __name__ == "__main__":
    unittest.main()
