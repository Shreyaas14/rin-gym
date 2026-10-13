"""Evaluation must count eligible matches, retain repeats and protect the ledger."""

import unittest
from experiments.execution_aware.evaluate import (
    summarize,
    paired_interval,
    inventory_delta,
    assert_cohort,
)


class EvaluationTests(unittest.TestCase):
    def test_match_denominators_repeats_and_positions(self):
        rows = [
            dict(
                match_id="a",
                repeat=r,
                net_pnl=2.0,
                gross_pnl=2.1,
                fees=0.1,
                rebates=0.0,
                spread_slippage=0.2,
                midpoint_pnl=2.3,
                requested_quantity=2.0,
                filled_quantity=1.0,
                turnover=0.5,
                peak_exposure=0.5,
                mean_sampled_exposure=0.25,
                position_pnls=[2.0],
                orders=2,
                fills=1,
            )
            for r in range(3)
        ]
        rows += [
            dict(
                rows[0],
                match_id="b",
                repeat=r,
                net_pnl=0.0,
                gross_pnl=0.0,
                fees=0.0,
                spread_slippage=0.0,
                midpoint_pnl=0.0,
                filled_quantity=0.0,
                turnover=0.0,
                peak_exposure=0.0,
                mean_sampled_exposure=0.0,
                position_pnls=[],
                fills=0,
            )
            for r in range(3)
        ]
        s = summarize(rows)
        self.assertEqual(s["eligible_matches"], 2)
        self.assertEqual(s["profitable_match_rate_all_eligible"], 0.5)
        self.assertEqual(s["profitable_match_rate_traded"], 1.0)
        self.assertEqual(s["position_win_rate"], 1.0)
        self.assertEqual(s["fill_quantity_rate"], 0.25)
        self.assertEqual(s["mean_match_pnl"], 1.0)

    def test_paired_cluster_average_keeps_repeats(self):
        a = [
            dict(match_id=m, repeat=r, net_pnl=1 + r)
            for m in ["a", "b"]
            for r in range(3)
        ]
        b = [dict(x, net_pnl=x["net_pnl"] + 2) for x in a]
        s = paired_interval(a, b, seed=7, draws=50)
        self.assertEqual(s["mean_difference"], 2.0)
        self.assertEqual(s["ci95"], [2.0, 2.0])
        self.assertEqual(s["cluster_count"], 2)

    def test_partial_target_inventory_never_oversells(self):
        self.assertEqual(
            inventory_delta({"home": 0.25, "away": 0.1}, "away"),
            [("home", "sell", 0.25), ("away", "buy", 0.9)],
        )
        self.assertEqual(
            inventory_delta({"home": 0.25}, "flat"), [("home", "sell", 0.25)]
        )

    def test_cohort_rejects_protected_or_unfrozen_ids(self):
        with self.assertRaises(ValueError):
            assert_cohort(["protected"], ["allowed"], ["allowed"])
        with self.assertRaises(ValueError):
            assert_cohort(["allowed"], ["allowed", "missing"], ["allowed", "missing"])
        self.assertIsNone(assert_cohort(["allowed"], ["allowed"], ["allowed"]))


class CounterfactualTests(unittest.TestCase):
    def simulate(self, policy, fraction, strict=False, signal_valid_s=300):
        from experiments.execution_aware.evaluate import run_policy

        task = {
            "id": "m:decision:flat",
            "match_id": "m",
            "t": 100.0,
            "scoring": {
                "shown_prices": {"home": 0.5},
                "fill_prices": {"home": 0.5},
                "end_marks": {"home": 1.0},
                "result": "home",
            },
        }
        row = {
            "executed_action": "buy_home",
            "position_before": "flat",
            "next_position": "home",
            "probs": {"home": 0.8},
        }
        meta = {
            ("m", "home"): {
                "market_id": "market",
                "orderMinSize": 5.0,
                "feeSchedule": {"rate": 0.03, "exponent": 1},
                "feesEnabled": True,
                "orderPriceMinTickSize": 0.001,
            }
        }
        logs = []
        result = run_policy(
            [task],
            [row],
            0,
            policy,
            "central",
            fraction,
            0.25,
            "assumed_snapshot_market",
            meta,
            {"uncertainty_margin": 0.02, "signal_valid_s": signal_valid_s},
            logs,
            strict,
            historical_prices={
                ("m", "home"): [
                    (100.0, 0.5),
                    (160.0, 0.5),
                    (220.0, 0.5),
                    (280.0, 0.5),
                    (340.0, 0.5),
                ]
            },
        )
        return result, logs

    def test_saved_audit_reads_policy_object_in_order_receipts(self):
        from experiments.execution_aware.evaluate import arm_key

        self.assertEqual(
            arm_key(
                {
                    "policy": {"mode": "maker"},
                    "fee_mode": "assumed_snapshot_market",
                    "scenario": "central",
                    "recorded_constraints": False,
                }
            ),
            "assumed_snapshot_market:central:maker:fractional_sensitivity",
        )

    def test_partial_maker_is_not_fee_only_retained_fill(self):
        maker, logs = self.simulate("maker", 0.25)
        self.assertAlmostEqual(maker["filled_quantity"], 0.25)
        self.assertEqual(logs[0]["scenario"], "central")
        self.assertEqual(maker["fees"], 0.0)
        self.assertEqual(maker["position_pnls"], [maker["net_pnl"]])
        self.assertEqual(logs[0]["fills"][0]["liquidity_role"], "maker")
        self.assertAlmostEqual(
            maker["midpoint_pnl"], maker["gross_pnl"] + maker["spread_slippage"]
        )
        self.assertAlmostEqual(maker["net_pnl"], maker["gross_pnl"] - maker["fees"])

    def test_unfilled_has_no_position_win_denominator(self):
        maker, logs = self.simulate("maker", 0.0)
        self.assertEqual(maker["filled_quantity"], 0.0)
        self.assertEqual(maker["position_pnls"], [])
        self.assertIsNone(summarize([maker])["position_win_rate"])
        self.assertEqual(logs[0]["orders"][0]["status"], "expired_unfilled")

    def test_recorded_minimum_is_feasibility_sensitivity(self):
        maker, logs = self.simulate("maker", 0.75, strict=True)
        self.assertEqual(maker["filled_quantity"], 0.0)
        self.assertEqual(logs[0]["orders"][0]["status"], "below_minimum_notional")

    def test_frozen_180_second_deadline_prevents_late_hybrid_cross(self):
        hybrid, logs = self.simulate("hybrid", 0.25, signal_valid_s=180)
        self.assertAlmostEqual(hybrid["filled_quantity"], 0.25)
        self.assertEqual([f["liquidity_role"] for f in logs[0]["fills"]], ["maker"])
        self.assertEqual(
            logs[0]["orders"][1]["unfilled_reason"], "latency_exceeds_expiry"
        )

    def test_hybrid_has_partial_maker_and_taker_remainder(self):
        hybrid, logs = self.simulate("hybrid", 0.25)
        self.assertAlmostEqual(hybrid["filled_quantity"], 1.0)
        self.assertEqual(
            [f["liquidity_role"] for f in logs[0]["fills"]], ["maker", "taker"]
        )
        self.assertGreater(hybrid["fees"], 0.0)
        self.assertGreater(
            logs[0]["orders"][1]["timestamp"], logs[0]["orders"][0]["timestamp"]
        )


class HoldoutMetadataGuardTests(unittest.TestCase):
    def test_guard_refuses_overlap_without_outcome_access(self):
        import json
        import sqlite3
        import tempfile
        from pathlib import Path
        from experiments.forecast_only.core import digest, guard

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "test-ledger.sqlite3"
            db = sqlite3.connect(path)
            db.execute("CREATE TABLE events (seq INTEGER, body TEXT, sha256 TEXT)")
            previous = "0" * 64
            for i, (kind, payload) in enumerate(
                [("policy", {}), ("cohort", {"matches": [{"match_id": "sealed"}]})], 1
            ):
                event = {"kind": kind, "previous": previous, "payload": payload}
                checksum = digest(event)
                db.execute(
                    "INSERT INTO events VALUES (?,?,?)",
                    (i, json.dumps(event), checksum),
                )
                previous = checksum
            db.commit()
            db.close()
            with self.assertRaisesRegex(ValueError, "Protected cohort overlaps"):
                guard(path, ["sealed"])
            seal = guard(path, ["development"])
            self.assertEqual(seal["state"], "enrolled")
            self.assertEqual(seal["protected_matches"], 1)


class ExecutionCutoffAndResidualTests(unittest.TestCase):
    def test_source_close_closes_actual_residual_after_failed_switch(self):
        from experiments.execution_aware.evaluate import run_policy

        tasks = []
        for i, t in enumerate([100.0, 400.0, 700.0]):
            tasks.append(
                {
                    "id": f"m:{i}:flat",
                    "match_id": "m",
                    "t": t,
                    "scoring": {
                        "shown_prices": {"home": 0.5, "away": 0.5},
                        "fill_prices": {"home": 0.5, "away": 0.5},
                        "end_marks": {"home": 0.5, "away": 0.5},
                        "result": "away",
                    },
                }
            )
        tape = [
            {
                "executed_action": "buy_home",
                "position_before": "flat",
                "next_position": "home",
                "probs": {"home": 0.8, "away": 0.2},
            },
            {
                "executed_action": "switch_away",
                "position_before": "home",
                "next_position": "away",
                "probs": {"home": 0.8, "away": 0.99},
            },
            {
                "executed_action": "close",
                "position_before": "away",
                "next_position": "flat",
                "probs": {"home": 0.1, "away": 0.2},
            },
        ]
        meta = {
            ("m", o): {
                "market_id": o,
                "orderMinSize": 5.0,
                "orderPriceMinTickSize": 0.001,
                "feeSchedule": {"rate": 0.03, "exponent": 1},
                "feesEnabled": True,
            }
            for o in ["home", "away"]
        }
        logs = []
        run_policy(
            tasks,
            tape,
            0,
            "maker",
            "central",
            0.25,
            0.25,
            "assumed_snapshot_market",
            meta,
            {"uncertainty_margin": 0.02},
            logs,
            historical_prices={
                ("m", o): [
                    (t, 0.5)
                    for t in [
                        100.0,
                        160.0,
                        220.0,
                        280.0,
                        400.0,
                        460.0,
                        520.0,
                        580.0,
                        700.0,
                        760.0,
                        820.0,
                        880.0,
                    ]
                ]
                for o in ["home", "away"]
            },
        )
        actual_closes = [
            r for r in logs if r["source_action"] == "close" and r["side"] == "sell"
        ]
        self.assertEqual(actual_closes[0]["outcome"], "home")
        self.assertGreater(actual_closes[0]["filled_quantity"], 0.0)

    def test_future_price_suffix_cannot_change_earlier_intent(self):
        from experiments.execution_aware.evaluate import run_policy

        task = {
            "id": "m:flat",
            "match_id": "m",
            "t": 100.0,
            "scoring": {
                "shown_prices": {"home": 0.5},
                "fill_prices": {"home": 0.5},
                "end_marks": {"home": 1.0},
                "result": "home",
            },
        }
        tape = [
            {
                "executed_action": "buy_home",
                "position_before": "flat",
                "next_position": "home",
                "probs": {"home": 0.8},
            }
        ]
        meta = {
            ("m", "home"): {
                "market_id": "home",
                "orderMinSize": 5.0,
                "orderPriceMinTickSize": 0.001,
                "feeSchedule": {"rate": 0.03, "exponent": 1},
                "feesEnabled": True,
            }
        }
        streams = [
            {("m", "home"): [(100.0, 0.5), (160.0, 0.5), (220.0, 0.5), (280.0, 0.5)]},
            {("m", "home"): [(100.0, 0.5), (160.0, 0.9), (220.0, 0.95), (280.0, 0.99)]},
        ]
        logs = []
        for stream in streams:
            receipts = []
            run_policy(
                [task],
                tape,
                0,
                "hybrid",
                "central",
                0.25,
                0.25,
                "assumed_snapshot_market",
                meta,
                {"uncertainty_margin": 0.02},
                receipts,
                historical_prices=stream,
            )
            logs.append(receipts[0])
        self.assertEqual(logs[0]["common_gate"], logs[1]["common_gate"])
        self.assertEqual(
            logs[0]["orders"][0]["expected_cost"], logs[1]["orders"][0]["expected_cost"]
        )
        self.assertEqual(logs[0]["orders"][0]["intent"], logs[1]["orders"][0]["intent"])


if __name__ == "__main__":
    unittest.main()
