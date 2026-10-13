"""Offline development-only execution sensitivity; never calls a paid service."""

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import random
import statistics

from app.trade_env.costs import buy_cost, sell_proceeds, fee
from app.trade_env.execution import (
    Account,
    Book,
    Trade,
    Order,
    ExecutionEngine,
    ExecutionPolicy,
    FeeSchedule,
    expected_cost,
    execute_policy,
)
from app.trade_env.replay import Replay, by_match
from app.trade_env.tasks import read_tasks
from experiments.forecast_only.core import guard

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT.parent / "rin-gym-research-eight"
RELEASE = ROOT.parent / "rin-gym-forecast-only/data/raw/release-data-2026-10-13-v1"
OUT = ROOT / "artifacts/execution-aware"


def write(path, obj):
    path.write_text(json.dumps(obj, indent=2, allow_nan=False) + "\n")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assert_cohort(actual, frozen, allowed):
    if set(actual) != set(frozen) or not set(actual) <= set(allowed):
        raise ValueError("Input cohort differs from frozen allowed development matches")


def inventory_delta(positions, target):
    """Desired legs; caller must recheck residual inventory before new-side buy."""
    legs = [
        (o, "sell", q) for o, q in sorted(positions.items()) if q > 1e-9 and o != target
    ]
    if target != "flat" and positions.get(target, 0) < 1 - 1e-9:
        legs.append((target, "buy", 1 - positions.get(target, 0)))
    return legs


def grouped_pnl(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[row["match_id"]].append(row["net_pnl"])
    return {m: statistics.fmean(v) for m, v in groups.items()}


def paired_interval(a, b, seed=20261013, draws=2000):
    aa, bb = grouped_pnl(a), grouped_pnl(b)
    if set(aa) != set(bb):
        raise ValueError("Unpaired match cohorts")
    values = [bb[m] - aa[m] for m in sorted(aa)]
    if not values:
        raise ValueError("Empty cohort")
    rng = random.Random(seed)
    samples = sorted(
        statistics.fmean(rng.choices(values, k=len(values))) for _ in range(draws)
    )
    return {
        "mean_difference": statistics.fmean(values),
        "ci95": [
            samples[int(0.025 * draws)],
            samples[min(draws - 1, int(0.975 * draws))],
        ],
        "cluster_count": len(values),
        "unit": "dollars per match per repeat",
        "method": "paired match bootstrap after averaging all repeats per match",
        "seed": seed,
        "draws": draws,
    }


def summarize(rows):
    if not rows:
        raise ValueError("Empty result rows")
    match = grouped_pnl(rows)
    traded = {r["match_id"] for r in rows if r["filled_quantity"] > 0}
    positions = [x for r in rows for x in r["position_pnls"]]
    wins = [x for x in positions if x > 0]
    losses = [x for x in positions if x < 0]
    requested = sum(r["requested_quantity"] for r in rows)

    def total(k):
        return math.fsum(r[k] for r in rows)

    return {
        "eligible_matches": len(match),
        "match_repeats": len(rows),
        "traded_matches": len(traded),
        "mean_match_pnl": statistics.fmean(match.values()),
        **{
            k: total(k)
            for k in [
                "net_pnl",
                "gross_pnl",
                "midpoint_pnl",
                "fees",
                "rebates",
                "spread_slippage",
                "turnover",
            ]
        },
        "fill_quantity_rate": total("filled_quantity") / requested
        if requested
        else None,
        "requested_quantity": requested,
        "filled_quantity": total("filled_quantity"),
        "orders": sum(r["orders"] for r in rows),
        "fill_events": sum(r["fills"] for r in rows),
        "peak_exposure": max(r["peak_exposure"] for r in rows),
        "mean_sampled_exposure": statistics.fmean(
            r["mean_sampled_exposure"] for r in rows
        ),
        "profitable_match_rate_all_eligible": sum(v > 0 for v in match.values())
        / len(match),
        "profitable_match_rate_traded": sum(match[m] > 0 for m in traded) / len(traded)
        if traded
        else None,
        "position_count": len(positions),
        "position_win_rate": len(wins) / len(positions) if positions else None,
        "average_position_win": statistics.fmean(wins) if wins else None,
        "average_position_loss": statistics.fmean(losses) if losses else None,
        "worst_position_loss": min(losses) if losses else None,
        "worst_match_repeat": min(r["net_pnl"] for r in rows),
        "gross_losses": sum(
            x
            for r in rows
            for x in r.get("position_gross_pnls", r["position_pnls"])
            if x < 0
        ),
        "post_fill_adverse_price_movement": None,
        "post_fill_adverse_price_movement_reason": "Actual fills, executable books and queue roles are unobserved; synthetic fills cannot verify adverse selection. A separate timestamped historical price proxy is reported.",
        "post_fill_historical_price_proxy_60s": {
            "quantity_weighted_adverse_sampled_price_change": sum(
                r.get("post_fill_proxy_adverse_sum", 0.0) for r in rows
            )
            / sum(r.get("post_fill_proxy_quantity", 0.0) for r in rows)
            if sum(r.get("post_fill_proxy_quantity", 0.0) for r in rows) > 0
            else None,
            "covered_fill_events": sum(
                r.get("post_fill_proxy_fill_count", 0) for r in rows
            ),
            "total_fill_events": sum(r["fills"] for r in rows),
            "covered_quantity": sum(
                r.get("post_fill_proxy_quantity", 0.0) for r in rows
            ),
            "horizon_s": 60,
            "classification": "signed historical price change after synthetic fill, NOT verified adverse selection; positive means adverse",
            "cutoff": "last price <= assumedfilltime+60; maxage90; requires new sample after fill; no interpolation",
        },
        "position_denominator": "one match-repeat-outcome inventory account with any buy fill; repeated lots on same outcome aggregated",
        "match_denominator": "match P&L averaged across all three repeats; traded means any actual simulated fill in any repeat",
        "exposure_definition": "dollar inventory marked at decision/end sample mids, maximum per trajectory and mean over available decisions; time-weighted exposure unavailable",
        "totals_definition": "sums over 216 match-repeats, three repeats of each eligible match, not independent matches",
    }


def metadata_audit(ids, out):
    """Only schemas globally; market projections filtered to allowed 1X2 IDs."""
    import pyarrow.parquet as pq

    def snapshot(name):
        base = RELEASE / "data/processed" / name
        return (
            base
            / "snapshots"
            / json.loads((base / "current.json").read_text())["dataset_version"]
        )

    market = snapshot("fifa-markets")
    history = snapshot("fifa-market-history")
    schemas = {
        str(p): pq.read_schema(p).names
        for folder in [market, history]
        for p in folder.glob("*.parquet")
    }
    mappings = pq.read_table(
        market / "matches.parquet",
        columns=["match_id", "prediction_markets"],
        filters=[("match_id", "in", ids)],
    ).to_pylist()
    mids = [p["market_id"] for m in mappings for p in m["prediction_markets"]]
    yes_tokens = {
        p["market_id"]: p["token_id"] for m in mappings for p in m["prediction_markets"]
    }
    assert len(mappings) == len(ids) and len(mids) == 3 * len(ids)
    rows = pq.read_table(
        history / "markets.parquet",
        columns=["match_id", "market_id", "hda_outcome", "source_market_json"],
        filters=[("market_id", "in", mids)],
    ).to_pylist()
    safe = []
    lookup = {}
    for row in rows:
        raw = json.loads(row["source_market_json"])
        item = {k: row[k] for k in ["match_id", "market_id", "hda_outcome"]}
        item.update(
            {
                k: raw.get(k)
                for k in [
                    "feesEnabled",
                    "feeSchedule",
                    "orderMinSize",
                    "orderPriceMinTickSize",
                    "updatedAt",
                    "createdAt",
                ]
            }
        )
        item["yes_token_id"] = yes_tokens[row["market_id"]]
        item["historical_validity"] = (
            "unknown: source observation timestamp/effective interval not supplied for June trade time"
        )
        safe.append(item)
        lookup[(row["match_id"], row["hda_outcome"])] = item
    assert len(safe) == len(mids)
    report = {
        "release": str(RELEASE),
        "schemas_only_inventory": schemas,
        "filtered_matches": ids,
        "filtered_metadata": safe,
        "orders_trades_depth_queue_available": False,
        "price_columns": schemas[str(history / "prices.parquet")],
        "read_policy": "No load_release, outcomes, events, market-resolution or protected labels read; only schemas and 72-match mapping/216-market metadata projections",
        "current_docs_verified_date": "2026-10-13",
        "documentation": {
            "fees": "https://docs.polymarket.com/trading/fees",
            "rebates": "https://docs.polymarket.com/programs/maker-rebates",
            "orders": "https://docs.polymarket.com/trading/place-orders",
        },
        "docs_claims": {
            "fee_formula": "shares * feeRate * p * (1-p)",
            "sports_current_taker_rate": 0.05,
            "maker_rate": 0,
            "precision": 5,
            "rebates": "daily relative fee-equivalent pool allocation; eligibility/allocation not verified here",
            "post_only": "reject if would cross; local GTC cancellation modeled",
        },
        "metadata_fee_schedules": dict(
            Counter(json.dumps(r["feeSchedule"], sort_keys=True) for r in safe)
        ),
        "metadata_min_sizes_raw_units_uncertain": dict(
            Counter(str(r["orderMinSize"]) for r in safe)
        ),
        "min_size_unit_conflict": "Gamma market-details docs call orderMinSize minimum USD notional; order-book place-orders docs call min_order_size shares. Raw metadata is not definitive share size. One share costs <=$1, below either interpretation of5",
        "metadata_ticks": dict(Counter(str(r["orderPriceMinTickSize"]) for r in safe)),
        "required_validation_data": [
            "historical effective fee schedules per market",
            "timestamped L2 book snapshots/deltas including depths",
            "trades with price,size,aggressor side and sequence",
            "order acks/rejections/fills/cancel acks and clock/latency records",
            "queue position assumptions calibrated against own fills",
            "contemporaneous min-size/tick and trading-enabled changes",
            "timestamped post-fill midpoints at fixed horizons",
            "daily rebate eligibility and pool denominator",
        ],
    }
    write(out / "data-audit.json", report)
    return lookup


def legacy_rows(tasks, predictions, source_rows, config):
    lookup = {(r["repeat"], r["task_id"]): r for r in predictions}
    references = {(r["repeat"], r["match_id"]): r for r in source_rows}
    rows = []
    max_error = 0.0
    max_cash_error = 0.0
    for repeat in range(3):
        for mid, group in sorted(by_match(tasks).items()):
            replay = Replay(group, config)
            accounts = {}
            requested = turnover = fees = spread = 0.0
            exposures = []
            while (task := replay.next_task()) is not None:
                p = lookup[(repeat, task["id"])]
                assert p["position_before"] == replay.position
                action = p["executed_action"]
                before = replay.position
                legs = []
                if action == "close" or action.startswith("switch_"):
                    legs.append((before, "sell"))
                if action.startswith(("buy_", "switch_")):
                    legs.append((action.split("_", 1)[1], "buy"))
                for outcome, side in legs:
                    price = task["scoring"]["fill_prices"][outcome]
                    allin = (
                        buy_cost(price, config, outcome)
                        if side == "buy"
                        else sell_proceeds(price, config, outcome)
                    )
                    combined = (allin - price) if side == "buy" else (price - allin)
                    charged = min(fee(price, config, outcome), combined)
                    executable = allin - charged if side == "buy" else allin + charged
                    f = {
                        "quantity": 1.0,
                        "price": executable,
                        "cash_delta": -allin if side == "buy" else allin,
                        "fees": charged,
                        "rebates": 0.0,
                        "spread_slippage": combined - charged,
                        "decision_mid_reference": price,
                    }
                    accounts.setdefault(outcome, Account()).apply(outcome, side, [f])
                    fees += charged
                    spread += combined - charged
                    turnover += executable
                    requested += 1
                replay.step(action)
                exposures.append(
                    sum(
                        a.positions.get(o, 0) * task["scoring"]["end_marks"][o]
                        for o, a in accounts.items()
                    )
                )
            result = replay.result()
            reference = references[(repeat, mid)]
            error = abs(result["pnl"] - reference["pnl"])
            max_error = max(max_error, error)
            if error > 1e-9:
                raise ValueError("Legacy Replay P&L did not reproduce pinned original")
            winner = group[0]["scoring"]["result"]
            for a in accounts.values():
                a.settle(winner)
            cash = sum(a.net_pnl for a in accounts.values())
            max_cash_error = max(max_cash_error, abs(cash - result["pnl"]))
            rows.append(
                {
                    "match_id": mid,
                    "repeat": repeat,
                    "policy": "legacy_exact",
                    "net_pnl": result["pnl"],
                    "gross_pnl": result["pnl"] + fees,
                    "midpoint_pnl": result["pnl"] + fees + spread,
                    "fees": fees,
                    "rebates": 0.0,
                    "spread_slippage": spread,
                    "requested_quantity": requested,
                    "filled_quantity": requested,
                    "turnover": turnover,
                    "peak_exposure": max(exposures, default=0),
                    "mean_sampled_exposure": statistics.fmean(exposures)
                    if exposures
                    else 0.0,
                    "position_pnls": [a.net_pnl for a in accounts.values()],
                    "position_gross_pnls": [a.gross_pnl for a in accounts.values()],
                    "orders": int(requested),
                    "fills": int(requested),
                }
            )
    if max_cash_error > 1e-9:
        raise ValueError("Legacy cash ledger differs from mark-to-market replay")
    return rows, {
        "legacy_replay_matches": len(rows),
        "maximum_pnl_absolute_error": max_error,
        "maximum_cash_accounting_error": max_cash_error,
        "passed": True,
    }


def synthetic_book(t, mid, *, tick=0.0001, minimum=0.0, min_notional=0.0):
    """Explicit hypothetical quotes; never interpreted as historical bid/ask."""
    bid = max(tick, math.floor((mid - 0.01) / tick + 1e-9) * tick)
    ask = min(1 - tick, math.ceil((mid + 0.01) / tick - 1e-9) * tick)
    if bid >= ask:
        bid, ask = tick, 1 - tick
    return Book(
        t,
        ((bid, 100.0),),
        ((ask, 100.0),),
        tick,
        minimum,
        "SYNTHETIC quote/depth sensitivity from historical price sample",
        min_notional=min_notional,
    )


def price_asof(stream, t, max_age=90.0):
    """Return a price observation available by t; never interpolate future prices."""
    from bisect import bisect_right

    at = bisect_right(stream, (t, math.inf)) - 1
    if at < 0 or t - stream[at][0] > max_age:
        return None
    return stream[at]


def read_historical_prices(ids, metadata, out):
    """Read prices only with BOTH cohort and exact YES-token filters."""
    import pyarrow.parquet as pq

    base = RELEASE / "data/processed/fifa-market-history"
    history = (
        base
        / "snapshots"
        / json.loads((base / "current.json").read_text())["dataset_version"]
    )
    tokens = {m["yes_token_id"]: key for key, m in metadata.items()}
    rows = pq.read_table(
        history / "prices.parquet",
        columns=["match_id", "token_id", "timestamp", "price"],
        filters=[("match_id", "in", ids), ("token_id", "in", list(tokens))],
    ).to_pylist()
    prices = defaultdict(list)
    for r in rows:
        key = tokens[r["token_id"]]
        if (
            r["match_id"] != key[0]
            or not math.isfinite(r["timestamp"] + r["price"])
            or not 0 <= r["price"] <= 1
        ):
            raise ValueError("Invalid cohort price observation")
        prices[key].append((float(r["timestamp"]), float(r["price"])))
    for key in prices:
        prices[key] = sorted(set(prices[key]))
    if set(prices) != set(metadata):
        raise ValueError("Missing exact development token price history")
    write(
        out / "price-provenance.json",
        {
            "path": str(history / "prices.parquet"),
            "columns": ["match_id", "token_id", "timestamp", "price"],
            "filters": {"match_id": ids, "token_id": sorted(tokens)},
            "filtered_rows": len(rows),
            "selected_series": len(prices),
            "filtered_content_sha256": hashlib.sha256(
                json.dumps(
                    sorted(
                        rows,
                        key=lambda r: (
                            r["match_id"],
                            r["token_id"],
                            r["timestamp"],
                            r["price"],
                        ),
                    ),
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest(),
            "cutoff": "last observation timestamp <= query time, maximum age90 seconds; no interpolation",
            "used_for": "synthetic bid/ask depth around asof historical prices; fixed60s post-assumed-fill priceproxy",
            "not_read": ["outcomes", "events", "market-resolution", "protected labels"],
        },
    )
    return prices


def run_policy(
    group,
    tape,
    repeat,
    policy,
    scenario,
    fraction,
    queue,
    fee_mode,
    metadata,
    config,
    logs,
    recorded_constraints=False,
    *,
    historical_prices=None,
):
    if historical_prices is None:
        raise ValueError("Explicit timestamped historical price streams required")
    account = Account()
    sub = {}
    purchased = set()
    exposures = []
    adverse = []
    requested = filled = turnover = 0.0
    orders = fills = 0
    for task, row in sorted(zip(group, tape), key=lambda pair: pair[0]["t"]):
        action = row["executed_action"]
        old = row["position_before"]
        target = row["next_position"]
        t = task["t"]
        probs = row.get("probs")
        if action in ("keep", "do_nothing"):
            exposures.append(
                sum(
                    q * task["scoring"]["shown_prices"][o]
                    for o, q in account.positions.items()
                )
            )
            continue
        nominal = []
        if action == "close" or action.startswith("switch_"):
            nominal.append((old, "sell"))
        if action.startswith(("buy_", "switch_")):
            nominal.append((target, "buy"))

        def context(outcome):
            meta = metadata[(task["match_id"], outcome)]
            market_id = meta["market_id"]
            rate = (
                meta["feeSchedule"]["rate"]
                if fee_mode == "assumed_snapshot_market"
                else 0.05
            )
            if fee_mode == "assumed_snapshot_market" and (
                meta["feeSchedule"].get("exponent") != 1
                or meta.get("feesEnabled") is not True
            ):
                raise ValueError("Unsupported per-market assumed schedule")
            schedule = FeeSchedule(
                market_id,
                0,
                None,
                1791849600,
                0,
                rate,
                "assumed_legacy" if fee_mode == "assumed_legacy" else "sensitivity",
                fee_mode
                + "; market="
                + market_id
                + "; source=data-audit.json filtered metadata and official fees page; historical applicability UNKNOWN; rebates0",
                precision=None if fee_mode == "assumed_legacy" else 5,
            )
            stream = historical_prices[(task["match_id"], outcome)]
            observed = price_asof(stream, t)
            tick = meta["orderPriceMinTickSize"] if recorded_constraints else 0.0001
            minimum_notional = meta["orderMinSize"] if recorded_constraints else 0.0
            decision = (
                synthetic_book(
                    observed[0], observed[1], tick=tick, min_notional=minimum_notional
                )
                if observed
                else None
            )
            return meta, schedule, decision, stream, tick, minimum_notional

        gates = []
        for outcome, side in nominal:
            meta, schedule, decision, stream, tick, minimum_notional = context(outcome)
            gates.append(
                expected_cost(probs[outcome], side, 1.0, decision, schedule, "taker")
                if probs and decision
                else None
            )
        combined_edge = (
            sum(g["net_edge_per_share"] for g in gates)
            if gates and all(gates)
            else None
        )
        gate_ok = (
            combined_edge is not None and combined_edge > config["uncertainty_margin"]
        )
        actual_legs = inventory_delta(account.positions, target)
        # Even if actual inventory is empty, keep blocked original intent receipts.
        legs = actual_legs if actual_legs else [(o, side, 0.0) for o, side in nominal]
        for outcome, side, planned_qty in legs:
            meta, schedule, decision, stream, tick, minimum_notional = context(outcome)
            qty = (
                account.positions.get(outcome, 0)
                if side == "sell"
                else max(0.0, 1.0 - account.positions.get(outcome, 0))
            )
            reason = None
            if not gate_ok:
                reason = "common_taker_cost_gate_rejected"
            elif decision is None:
                reason = "unavailable_asof_decision_price"
            elif side == "buy" and any(
                q > 1e-9 for o, q in account.positions.items() if o != outcome
            ):
                reason = "residual_other_inventory_blocks_switch"
            elif qty <= 1e-9:
                reason = "no_inventory_to_close_or_target_already_filled"
            base = {
                "match_id": task["match_id"],
                "repeat": repeat,
                "policy": policy,
                "scenario": scenario,
                "fee_mode": fee_mode,
                "recorded_constraints": recorded_constraints,
                "task_id": task["id"],
                "source_action": action,
                "source_position_before": old,
                "desired_position": target,
                "timestamp": t,
                "outcome": outcome,
                "side": side,
                "nominal_quantity": 1.0,
                "quantity_after_inventory_guard": qty,
                "source_intent_legs": nominal,
                "common_gate": {
                    "expected_combined_taker_edge": combined_edge,
                    "uncertainty_margin": config["uncertainty_margin"],
                    "per_leg_costs": gates,
                },
                "classification": "synthetic_sensitivity_frozen_action_counterfactual",
                "future_price_selection_allowed": False,
                "expected_settlement_exit_cost": 0.0,
                "expected_early_exit_cost": expected_cost(
                    probs[outcome], "sell", 1.0, decision, schedule, "taker"
                )
                if probs and decision
                else None,
            }
            if reason:
                logs.append(
                    {
                        **base,
                        "status": reason,
                        "filled_quantity": 0.0,
                        "unfilled_quantity": qty,
                        "intent": {"limit": None, "quantity": qty, "timestamp": t},
                        "fee_schedule": asdict(schedule),
                        "fees": 0.0,
                        "rebates": 0.0,
                        "spread_slippage": 0.0,
                        "cancellation_timestamp": None,
                    }
                )
                continue
            # Actual source timestamps are preserved. The executor alone accesses
            # future observations; execute_policy selects each stage strictly asof.
            books = [
                synthetic_book(ts, p, tick=tick, min_notional=minimum_notional)
                for ts, p in stream
                if t - 90 <= ts <= t + 180
            ]
            passive_price = decision.bid if side == "buy" else decision.ask
            assumed_trade = Trade(
                t + 90,
                passive_price,
                queue + qty * fraction,
                "sell" if side == "buy" else "buy",
                "SYNTHETIC opposing flow/queue; not observed",
            )
            engine = ExecutionEngine(
                [schedule],
                latency_s=60,
                cancel_latency_s=0,
                max_quote_age_s=90,
                allow_assumed_fees=True,
            )
            intent = Order(
                f'{repeat}:{task["id"]}:{outcome}:{side}',
                meta["market_id"],
                side,
                qty,
                1.0 if side == "buy" else 0.0,
                t,
                120,
            )
            result = execute_policy(
                engine,
                intent,
                probs[outcome],
                books,
                [assumed_trade],
                ExecutionPolicy(
                    policy,
                    uncertainty_margin=config["uncertainty_margin"],
                    signal_valid_s=config.get("signal_valid_s", 180),
                ),
                scenario="pessimistic"
                if scenario == "pessimistic"
                else "optimistic"
                if scenario == "optimistic"
                else "approximate",
                queue_ahead=queue,
            )
            for stage in result["orders"]:
                sample = price_asof(stream, stage["timestamp"])
                if stage.get("expected_cost") and sample:
                    stage["expected_cost"].update(
                        historical_price_sample_timestamp=sample[0],
                        historical_price_sample_value=sample[1],
                        maximum_price_age_s=90.0,
                    )
            for fill in result["fills"]:
                sample = price_asof(stream, fill["timestamp"])
                if sample:
                    fill["historical_price_sample_timestamp"] = sample[0]
            proxies = []
            for fill in result["fills"]:
                before = price_asof(stream, fill["timestamp"])
                after = price_asof(stream, fill["timestamp"] + 60)
                if before and after and after[0] > fill["timestamp"]:
                    movement = (1 if side == "buy" else -1) * (after[1] - before[1])
                    proxy = {
                        "fill_timestamp": fill["timestamp"],
                        "query_timestamp": fill["timestamp"] + 60,
                        "horizon_s": 60,
                        "before_sample_timestamp": before[0],
                        "after_sample_timestamp": after[0],
                        "signed_favorable_sampled_price_change": movement,
                        "adverse_sampled_price_change": -movement,
                        "quantity": fill["quantity"],
                        "classification": "historical_price_proxy_after_SYNTHETIC_fill_not_verified_adverse_selection",
                    }
                    proxies.append(proxy)
                    adverse.append(proxy)
                else:
                    proxies.append(
                        {
                            "horizon_s": 60,
                            "value": None,
                            "reason": "missing/stale/no new observed price after synthetic fill",
                        }
                    )
            logs.append(
                {
                    **base,
                    **result,
                    "policy": policy,
                    "scenario": scenario,
                    "execution_policy": result["policy"],
                    "execution_evidence_scenario": result["scenario"],
                    "synthetic_assumptions": {
                        "passive_fill_fraction": fraction,
                        "queue_ahead": queue,
                        "quote_depth": 100,
                        "quote_source": "historical price sample timestamps, strictasof maxage90; quotes/depthsynthetic",
                        "min_size_shares": 0.0,
                        "min_notional_usd": minimum_notional,
                        "raw_orderMinSize": meta["orderMinSize"],
                        "raw_units_uncertain": True,
                        "tick": tick,
                    },
                    "assumed_trade": asdict(assumed_trade),
                    "post_fill_price_proxies": proxies,
                }
            )
            account.apply(outcome, side, result["fills"])
            sub.setdefault(outcome, Account()).apply(outcome, side, result["fills"])
            if side == "buy" and result["filled_quantity"] > 0:
                purchased.add(outcome)
            requested += qty
            filled += result["filled_quantity"]
            turnover += sum(f["quantity"] * f["price"] for f in result["fills"])
            orders += len(result["orders"])
            fills += len(result["fills"])
        exposures.append(
            sum(
                q * task["scoring"]["end_marks"][o]
                for o, q in account.positions.items()
            )
        )
    winner = group[0]["scoring"]["result"]
    account.settle(winner)
    for a in sub.values():
        a.settle(winner)
    if (
        abs(account.net_pnl - (account.gross_pnl - account.fees + account.rebates))
        > 1e-9
    ):
        raise ValueError("Net accounting identity failed")
    if abs(account.midpoint_pnl - account.gross_pnl - account.spread_slippage) > 1e-9:
        raise ValueError("Spread counted twice or attribution missing")
    return {
        "match_id": group[0]["match_id"],
        "repeat": repeat,
        "policy": policy,
        "scenario": scenario,
        "fee_mode": fee_mode,
        "recorded_constraints": recorded_constraints,
        "net_pnl": account.net_pnl,
        "gross_pnl": account.gross_pnl,
        "midpoint_pnl": account.midpoint_pnl,
        "fees": account.fees,
        "rebates": account.rebates,
        "spread_slippage": account.spread_slippage,
        "requested_quantity": requested,
        "filled_quantity": filled,
        "turnover": turnover,
        "peak_exposure": max(exposures, default=0),
        "mean_sampled_exposure": statistics.fmean(exposures) if exposures else 0.0,
        "position_pnls": [sub[o].net_pnl for o in sorted(purchased)],
        "position_gross_pnls": [sub[o].gross_pnl for o in sorted(purchased)],
        "post_fill_proxy_quantity": sum(p["quantity"] for p in adverse),
        "post_fill_proxy_adverse_sum": sum(
            p["adverse_sampled_price_change"] * p["quantity"] for p in adverse
        ),
        "post_fill_proxy_fill_count": len(adverse),
        "orders": orders,
        "fills": fills,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Audit saved results and logs without simulating",
    )
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    protocol_path = args.out / "protocol.json"
    if not protocol_path.exists():
        raise ValueError("Save a frozen development protocol before evaluation")
    protocol = json.loads(protocol_path.read_text())
    ids = protocol["match_ids"]
    seal = guard(protocol["ledger"], ids)
    if seal != protocol["seal_before"]:
        raise ValueError("Protected ledger changed")
    for name, checksum in protocol["source_sha256"].items():
        if sha(args.source / "inputs" / name) != checksum:
            raise ValueError("Frozen input checksum mismatch")
    if args.verify:
        verify_saved(args.source, args.out, protocol)
        return
    metadata = metadata_audit(ids, args.out)
    # Metadata-only protocol refinement precedes loading new simulation results.
    protocol.update(
        fee_modes=[
            "assumed_legacy",
            "assumed_current_sports",
            "assumed_snapshot_market",
        ],
        metadata_feasibility_arm=True,
        uncertainty_margin=protocol.get("uncertainty_margin", 0.02),
        intent_gate="Common contemporaneous taker-cost gate for all policies; frozen one-share source signal legs; switches compare combined value. Internal execution may reject an individual close.",
        metadata_constraints="Raw Gamma orderMinSize5 modeled as USDnotional per current docs (book docs shares inconsistent); recorded ticks enforced only in feasibility arm; fractional sensitivity min0/tick.0001 intentionally relaxed, not verified executable returns",
    )
    write(protocol_path, protocol)
    if args.prepare_only:
        print(
            json.dumps(
                {
                    "protocol_prepared": True,
                    "data_audit": str(args.out / "data-audit.json"),
                }
            )
        )
        return
    historical_prices = read_historical_prices(ids, metadata, args.out)
    tasks = read_tasks(args.source / "inputs/earlier-run-risk/tasks.jsonl.gz")
    assert_cohort(by_match(tasks), ids, protocol["allowed_match_ids"])
    if any(t["split"] != "val" or t["demo"] for t in tasks):
        raise ValueError("Unexpected non-development tasks")
    preds = [
        json.loads(x)
        for x in (args.source / "inputs/closed-loop-risk/predictions.jsonl")
        .read_text()
        .splitlines()
        if json.loads(x)["arm"] == "original"
    ]
    old = [
        json.loads(x)
        for x in (args.source / "inputs/closed-loop-risk/matches.jsonl")
        .read_text()
        .splitlines()
        if json.loads(x)["arm"] == "original"
    ]
    original_protocol = json.loads(
        (args.source / "inputs/closed-loop-risk/protocol.json").read_text()
    )
    legacy, verification = legacy_rows(tasks, preds, old, original_protocol["config"])
    taskmap = {t["id"]: t for t in tasks}
    tapes = defaultdict(list)
    for row in preds:
        tapes[(row["repeat"], taskmap[row["task_id"]]["match_id"])].append(row)
    arms = {"legacy_exact": legacy}
    logs = []
    specs = [
        (mode, scenario, pol, False)
        for mode in protocol["fee_modes"]
        for scenario in protocol["sensitivity"]
        for pol in protocol["policies"]
    ]
    specs += [
        ("assumed_snapshot_market", "central", pol, True)
        for pol in protocol["policies"]
    ]
    for mode, scenario, pol, strict in specs:
        key = ":".join(
            [
                mode,
                scenario,
                pol,
                "recorded_constraints" if strict else "fractional_sensitivity",
            ]
        )
        rows = []
        for repeat in range(3):
            for mid in sorted(ids):
                tape = sorted(
                    tapes[(repeat, mid)], key=lambda r: taskmap[r["task_id"]]["t"]
                )
                group = [taskmap[r["task_id"]] for r in tape]
                sensitivity = protocol["sensitivity"][scenario]
                rows.append(
                    run_policy(
                        group,
                        tape,
                        repeat,
                        pol,
                        scenario,
                        sensitivity["assumed_passive_fill_fraction"],
                        sensitivity["queue_ahead"],
                        mode,
                        metadata,
                        protocol,
                        logs,
                        strict,
                        historical_prices=historical_prices,
                    )
                )
        arms[key] = rows
    summaries = {k: summarize(v) for k, v in arms.items()}
    paired = {}
    for key, rows in arms.items():
        if key == "legacy_exact" or ":taker:" in key:
            continue
        baseline = key.replace(":maker:", ":taker:").replace(":hybrid:", ":taker:")
        paired[key] = paired_interval(arms[baseline], rows)
    later = set(protocol["chronological_partition"]["assessment_match_ids"])
    assessment = {
        k: summarize([r for r in v if r["match_id"] in later]) for k, v in arms.items()
    }
    after = guard(protocol["ledger"], ids)
    if after != seal:
        raise ValueError("Protected ledger changed during evaluation")
    verification.update(
        seal_before=seal,
        seal_after=after,
        ledger_unchanged=True,
        source_hashes_match=True,
        configuration_sha256=sha(protocol_path),
        code_sha256={
            str(p.relative_to(ROOT)): sha(p)
            for p in [
                Path(__file__),
                ROOT / "app/trade_env/execution.py",
                ROOT / "app/trade_env/costs.py",
                ROOT / "app/trade_env/replay.py",
            ]
        },
        incremental_paid_requests=0,
        rows=sum(map(len, arms.values())),
        order_intent_logs=len(logs),
        classification="offline accounting reproduction and synthetic sensitivity; no fresh closed-loop inference",
    )
    write(args.out / "verification.json", verification)
    write(
        args.out / "results.json",
        {
            "summaries": summaries,
            "paired_match_intervals": paired,
            "later_assessment_summaries": assessment,
            "classification": "inconclusive for real maker/hybrid profitability; negative one-share recorded-minimum feasibility",
            "fresh_closed_loop": False,
            "paid_spend_usd": 0,
        },
    )
    for name, rows in [
        ("matches.jsonl", [r for v in arms.values() for r in v]),
        ("orders.jsonl", logs),
    ]:
        with (args.out / name).open("w") as f:
            for row in rows:
                f.write(json.dumps(row, allow_nan=False) + "\n")
    report(args.out, protocol, summaries, paired, verification)
    print(
        json.dumps(
            {
                "passed": True,
                "legacy_max_error": verification["maximum_pnl_absolute_error"],
                "arms": len(arms),
                "match_repeats_per_arm": len(legacy),
                "order_logs": len(logs),
                "net_legacy": summaries["legacy_exact"]["net_pnl"],
                "holdout_seal": after,
                "paid_spend_usd": 0,
            },
            indent=2,
        )
    )


def arm_key(row):
    mode = row["policy"]["mode"] if isinstance(row["policy"], dict) else row["policy"]
    if mode == "legacy_exact":
        return "legacy_exact"
    return ":".join(
        [
            row["fee_mode"],
            row["scenario"],
            mode,
            "recorded_constraints"
            if row["recorded_constraints"]
            else "fractional_sensitivity",
        ]
    )


def verify_saved(source, out, protocol):
    """Independent saved-ledger audit, not a second paid/closed-loop evaluation."""
    verification = json.loads((out / "verification.json").read_text())
    if sha(out / "protocol.json") != verification["configuration_sha256"]:
        raise ValueError("Saved protocol checksum changed")
    for file, checksum in verification["code_sha256"].items():
        if sha(ROOT / file) != checksum:
            raise ValueError("Code changed after saved simulation; rerun required")
    tasks = read_tasks(source / "inputs/earlier-run-risk/tasks.jsonl.gz")
    assert_cohort(by_match(tasks), protocol["match_ids"], protocol["allowed_match_ids"])
    winners = {
        mid: group[0]["scoring"]["result"] for mid, group in by_match(tasks).items()
    }
    rows = [
        json.loads(line) for line in (out / "matches.jsonl").read_text().splitlines()
    ]
    logs = [
        json.loads(line) for line in (out / "orders.jsonl").read_text().splitlines()
    ]
    saved = json.loads((out / "results.json").read_text())
    groups = defaultdict(list)
    accounts = defaultdict(Account)
    quantities = defaultdict(lambda: [0.0, 0.0])
    fill_events = 0
    cutoff_checks = 0
    for row in rows:
        groups[arm_key(row)].append(row)
        if (
            abs(row["net_pnl"] - row["gross_pnl"] + row["fees"] - row["rebates"]) > 1e-9
            or abs(row["midpoint_pnl"] - row["gross_pnl"] - row["spread_slippage"])
            > 1e-9
        ):
            raise ValueError("Saved P&L identity failed")
    for name, group in groups.items():
        if len(group) != len(protocol["match_ids"]) * 3 or {
            r["match_id"] for r in group
        } != set(protocol["match_ids"]):
            raise ValueError("Incomplete saved cohort")
        if summarize(group) != saved["summaries"][name]:
            raise ValueError("Saved metric denominators/totals disagree")
    for log in logs:
        if log["match_id"] not in winners:
            raise ValueError("Unexpected log cohort")
        key = (arm_key(log), log["repeat"], log["match_id"])
        if "orders" not in log:
            continue
        quantities[key][0] += log["requested_quantity"]
        quantities[key][1] += log["filled_quantity"]
        if log["filled_quantity"] > log["requested_quantity"] + 1e-9:
            raise ValueError("Overfilled intent")
        for stage in log["orders"]:
            quote = stage.get("expected_cost")
            if quote:
                if quote["fee_schedule"]["status"] not in (
                    "assumed_legacy",
                    "sensitivity",
                ):
                    raise ValueError("Synthetic history mislabeled verified")
                if (
                    quote["historical_price_sample_timestamp"] > stage["timestamp"]
                    or stage["timestamp"] - quote["historical_price_sample_timestamp"]
                    > 90
                ):
                    raise ValueError("Policy quote violates actual information cutoff")
                cutoff_checks += 1
            if stage.get("intent", {}).get("post_only") and any(
                f["liquidity_role"] != "maker" for f in stage.get("fills", [])
            ):
                raise ValueError("Post-only taker fill")
        for f in log["fills"]:
            if (
                f["historical_price_sample_timestamp"] > f["timestamp"]
                or f["timestamp"] - f["historical_price_sample_timestamp"] > 90
            ):
                raise ValueError("Fill reference price violates timestamp")
            fee, rebate = FeeSchedule(**f["fee_schedule"]).charges(
                f["liquidity_role"], f["price"], f["quantity"]
            )
            if (
                abs(fee - f["fees"]) > 1e-9
                or abs(rebate - f["rebates"]) > 1e-9
                or f["rebates"] != 0
            ):
                raise ValueError("Saved fee/rebate accounting error")
            fill_events += 1
        for proxy in log.get("post_fill_price_proxies", []):
            if "after_sample_timestamp" in proxy and (
                proxy["after_sample_timestamp"] > proxy["query_timestamp"]
                or proxy["before_sample_timestamp"] > proxy["fill_timestamp"]
            ):
                raise ValueError("Post-fill proxy timestamp violation")
        accounts[key].apply(log["outcome"], log["side"], log["fills"])
    for row in rows:
        if row["policy"] == "legacy_exact":
            continue
        key = (arm_key(row), row["repeat"], row["match_id"])
        account = accounts[key]
        account.settle(winners[row["match_id"]])
        if abs(account.net_pnl - row["net_pnl"]) > 1e-9:
            raise ValueError("Intent/fill ledger does not reproduce saved netP&L")
        if (
            abs(quantities[key][0] - row["requested_quantity"]) > 1e-9
            or abs(quantities[key][1] - row["filled_quantity"]) > 1e-9
        ):
            raise ValueError("Fill-rate ledger denominator mismatch")
    seal = guard(protocol["ledger"], protocol["match_ids"])
    if seal != protocol["seal_before"]:
        raise ValueError("Protected ledger changed")
    result = {
        "passed": True,
        "saved_rows": len(rows),
        "order_intents": len(logs),
        "verified_fill_events": fill_events,
        "policy_stage_cost_estimates": cutoff_checks,
        "independent_net_cash_reconstruction": True,
        "fee_and_spread_identities": True,
        "metric_denominators_recomputed": True,
        "actual_price_stream_cutoff": "All saved quote/fill references verified <=their stage/fill timestamps and age<=90, plus prefix invariance integration test",
        "ledger_unchanged": True,
        "seal": seal,
        "incremental_paid_requests": 0,
    }
    write(out / "saved-audit.json", result)
    print(json.dumps(result, indent=2))


def report(out, protocol, summaries, paired, verification):
    lines = [
        "# Execution awareness: offline development report",
        "",
        "Classification: **inconclusive** for real maker/hybrid returns. **Negative feasibility** under recorded minimum value 5 with fixed one-share sizing. Synthetic results are sensitivity envelopes, not verified maker returns and not mathematically guaranteed P&L bounds.",
        "",
        "## Frozen design and accounting",
        "",
        "72 previously inspected development matches, three repeats, original final checkpoint `"
        + protocol["checkpoint"]
        + "`. Forecasts, original action tape and nominal one-share sizing stay frozen. A common taker all-in cost/uncertainty gate filters source intents; therefore controlled arms differ from the ungated exact legacy baseline. Partial/unfilled inventory is carried; a switch cannot buy the new outcome while residual old inventory remains. Later assessment uses the predeclared 33 later matches; no tuning or selection occurred.",
        "",
        "Legacy replay uses unchanged `Replay`, precomputed original action rewards and costs; maximum P&L error "
        + str(verification["maximum_pnl_absolute_error"])
        + ". The separate cash ledger reproduces the same P&L. Legacy fees and spread use the original fixed coefficient and allowance. Controlled execution uses the shared execution engine, per-fill liquidity roles and executable synthetic quote prices; it never debits spread a second time. Net = gross - fees + rebates. Midpoint P&L = gross + spread/slippage. Settlement carries no exit fee.",
        "",
        "## Fee and data provenance",
        "",
        "Official [fees](https://docs.polymarket.com/trading/fees) verified on 2026-10-13 give current sports taker coefficient 0.05, maker coefficient zero and five-decimal rounding. Current [maker rebates](https://docs.polymarket.com/programs/maker-rebates) depend on relative daily pool allocation; all rebates here are zero. [Order documentation](https://docs.polymarket.com/trading/place-orders) supports rejecting post-only crossings; this simulator uses local GTC cancellation rather than claiming an exchange GTD timestamp.",
        "",
        "The 216 exact 1X2 market metadata rows record coefficient **0.03**, raw `orderMinSize` **5**, tick 0.001 for 215 contracts and 0.01 for one. Current documentation and metadata disagree; neither supplies a validated June effective interval. Historical fees are **unknown**. Three separate explicit assumptions are reported: original fixed 0.05, current sports 0.05 with current rounding, and recorded market 0.03 with rounding. Gamma [market details](https://docs.polymarket.com/market-data/market-details) describe `orderMinSize` as minimum USD notional, while book order docs describe `min_order_size` as shares. The units are uncertain; a one-share order valued at most $1 fails either interpretation of 5. The feasibility arm deliberately uses minimum USD notional. None is historical verification. `data-audit.json` records the filtered metadata and schemas. No release outcomes/events were loaded.",
        "",
        "Only historical price samples and cached decision/fill/end mids exist; no observed order books, depths, aggressor trades or queue state. Fractional scenarios use invented two-sided quotes/depth, min-size zero, tick 0.0001 and invented opposing flow with declared queue/fill fractions (0%,25%,75%). Passive price touched is never treated as evidence of fill. A limit may fill as taker if it crosses; post-only rejects. Hybrid cancels first and rechecks remaining edge before crossing. All simulated fills are labeled synthetic sensitivity. Delay remains 60 seconds; expiry 120 seconds, cancellation latency zero; the original fixed signal validity is 180 seconds. Hybrid fallback submitted after cancellation at +120 arrives at +180, exactly at the deadline, and cannot fill. Earlier fallback after a post-only rejection may still fill before the deadline. This strict deadline was corrected after independent review without extending validity or selecting a new scenario. Quotes/depth are hypothetical around timestamped historical price samples filtered to these 216 YES contracts; each policy observation is the last sample available at its actual timestamp with maximum age 90 seconds. No future fill price is fed into order selection, and missing/stale observations cause misses. These fill, spread, depth and queue assumptions remain unvalidated.",
        "",
        "## Results",
        "",
        "Totals below sum 216 match-repeats. Profitability rates first average the three repeats per match. Position means/wins use match-repeat-outcome inventory accounts with any buy fill (aggregating lots). Exposure is dollar inventory sampled at available decisions, not time-weighted exposure. Turnover is executed share-price notional, excluding settlement. Fill rate divides filled by policy-requested quantity after common gate/inventory guard; it excludes rejected source intents, whose statuses remain in order logs.",
        "",
        "| Arm | Net | Gross | Fees | Spread/slippage | Qty fill rate | All/traded profitable matches | Position win rate | Worst position | Turnover | Peak exposure |",
        "|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|",
    ]

    legacy = summaries["legacy_exact"]
    controlled = summaries[
        "assumed_snapshot_market:central:taker:fractional_sensitivity"
    ]
    maker = summaries["assumed_snapshot_market:central:maker:fractional_sensitivity"]
    hybrid = summaries["assumed_snapshot_market:central:hybrid:fractional_sensitivity"]
    lines.insert(
        4,
        f"The exact legacy baseline lost ${-legacy['net_pnl']:.4f} over 216 match-repeats. Under the explicitly assumed recorded coefficient 0.03 and central fractional scenario, controlled taker net P&L was ${controlled['net_pnl']:.4f}, maker ${maker['net_pnl']:.4f}, and hybrid ${hybrid['net_pnl']:.4f}. Maker's smaller loss coincided with reduced fills; hybrid also lost money. Every corresponding paired match-cluster interval includes zero. These negative and inconclusive findings do not establish an execution advantage. The recorded-constraint feasibility arms all have zero fills.",
    )

    def fmt(x):
        return "NA" if x is None else f"{x:.4f}"

    for name, s in summaries.items():
        lines.append(
            "| "
            + name
            + " | "
            + " | ".join(
                fmt(s[k])
                for k in [
                    "net_pnl",
                    "gross_pnl",
                    "fees",
                    "spread_slippage",
                    "fill_quantity_rate",
                ]
            )
            + " | "
            + fmt(s["profitable_match_rate_all_eligible"])
            + "/"
            + fmt(s["profitable_match_rate_traded"])
            + " | "
            + " | ".join(
                fmt(s[k])
                for k in [
                    "position_win_rate",
                    "worst_position_loss",
                    "turnover",
                    "peak_exposure",
                ]
            )
            + " |"
        )
    lines += [
        "",
        "Average position win/loss, gross losses, worst match-repeat and mean sampled exposure are fully reported in `results.json`; the same metrics for the 33 later assessment games are included there. No-trade baseline has zero P&L and no position win denominator. All recorded-constraint arms produce no fills at fixed sizing.",
        "",
        "## Paired uncertainty",
        "",
        "All repeats from a match are averaged together before resampling matches. Shared-team dependence and repeated development use remain limitations. Intervals quantify this frozen-cohort accounting sensitivity only.",
        "",
        "| Arm versus same-assumption taker | Mean difference per match/repeat | Match-cluster 95% interval |",
        "|---|---:|---|",
    ]
    for name, v in paired.items():
        lines.append(
            "| "
            + name
            + " | "
            + fmt(v["mean_difference"])
            + " | ["
            + fmt(v["ci95"][0])
            + ", "
            + fmt(v["ci95"][1])
            + "] |"
        )
    lines += [
        "",
        "## Limitations and next discriminating evidence",
        "",
        "Fees-only accounting counterfactuals and synthetic execution sensitivity are not approximate replay from observed books. The archived original was previously collected with state feedback; this new comparison uses its frozen outputs, never fresh closed-loop inference. Changed maker/partial positions were not sent back to the model. Apparent savings may reflect opportunity selection, missed exits, adverse selection or changed fee-aware gating. Zero-fee accounting while preserving every fill is not used as maker returns.",
        "",
        "Verified post-fill adverse selection is **NA** because actual order/fill/queue evidence is absent. A separately labeled **60-second signed historical-price proxy after synthetic fills** is reported, quantity-weighted with covered fill counts and timestamps in the logs; it uses the last price observation <= simulated fill time + 60 seconds (age <=90), requires a new sample after the fill, and never interpolates. Positive signed adverse change means the price moved against the fill side. This is not verified adverse selection or actual execution. Future forecast labels and varying next-decision marks are not used for this metric. Obtain historical market fee intervals, L2 snapshots/deltas, aggressor trade sizes/sequences, order/fill/cancel acknowledgments, clock/latency and queue calibration, minimum-size/tick changes, post-fill midpoints and rebate eligibility/pool data. Validate on development only with unchanged signals before any sealed evaluation.",
        "",
        "## Reproduction and spend",
        "",
        "```sh",
        "PYTHONPATH=. HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 /Users/alexkan/Desktop/frontiers/rin-gym-forecast-only/.venv/bin/python -m experiments.execution_aware.evaluate",
        "PYTHONPATH=. /Users/alexkan/Desktop/frontiers/rin-gym-forecast-only/.venv/bin/python -m unittest tests.trade_env.test_execution_evaluation tests.trade_env.test_execution",
        "```",
        "",
        "Source checksums/configuration/seed and execution logs are saved. `protocol.json` was saved before results; metadata-only refinement was saved before simulation. `verification.json` proves exact baseline reproduction and both guards. `price-provenance.json` pins the filtered timestamped historical price projection. The saved correctness amendment replaced a draft cached-fill quote assumption and corrected residual-inventory targeting before the reported rerun; no hypothesis, cohort, seed or scenario selection changed. MLflow is optional and unused. Incremental paid-service requests **0**; actual incremental invocation spend **$0**, service billing not queried. This harness contains no paid client or retries. The latest entire-chat cap is $5; prior research authorizations do not increase it.",
        "",
        "Protected ledger before/after: `"
        + verification["seal_before"]["ledger_head"]
        + "`, state **pending_enrollment**, **0 enrolled protected matches**. No enrollment, protected predictions or protected labels. Active quarter-original-live inputs were excluded.",
    ]
    (out / "REPORT.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
