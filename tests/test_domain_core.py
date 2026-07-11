from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tq_app.domain import (
    RiskPolicy,
    RiskPolicyConfig,
    RiskState,
    SignalConfig,
    SignalEvaluator,
    StrategyContext,
    StrategyResult,
    TradeDecision,
    load_custom_strategies,
    register_strategy,
)
from tq_app.live_trading import LiveTradingConfig, LiveTradingEngine


def _snapshot() -> dict:
    return {
        "symbol": "BTCUSDT",
        "duration_seconds": 300,
        "last_close": 105.0,
        "candles": [
            {"time": 1, "open": 100.0, "high": 106.0, "low": 99.0, "close": 105.0},
        ],
        "time_labels": {"1": "2026-01-01 00:00:00"},
        "indicators": [],
    }


def _indicator_series(
    indicator_id: str,
    series_id: str,
    time_value: int,
    value: float,
    *,
    color: str = "",
    marker: str = "",
) -> dict:
    point = {"time": time_value, "value": value}
    if color:
        point["color"] = color
    options = {}
    if marker:
        options["candleMarkers"] = [{"time": time_value, "text": marker}]
    return {
        "id": indicator_id,
        "series": [{"id": series_id, "data": [point], "options": options}],
    }


def _merge_indicators(*indicators: dict) -> list[dict]:
    merged: dict[str, dict] = {}
    for indicator in indicators:
        target = merged.setdefault(indicator["id"], {"id": indicator["id"], "series": []})
        target["series"].extend(indicator["series"])
    return list(merged.values())


def _hull_snapshot(
    duration_seconds: int,
    side: str,
    *,
    include_stc: bool = True,
    stc_side: str | None = None,
) -> dict:
    time_value = 10
    series_suffix = "up" if side == "buy" else "down"
    hull_value = 90.0 if side == "buy" else 110.0
    indicators = [
        _indicator_series("merged_dkx_hull_ut", f"mhull_{series_suffix}", time_value, hull_value),
        _indicator_series("merged_dkx_hull_ut", f"shull_{series_suffix}", time_value, hull_value + (1 if side == "buy" else -1)),
    ]
    if include_stc:
        effective_stc_side = stc_side or side
        indicators.append(
            _indicator_series(
                "stc",
                "stc",
                time_value,
                50.0,
                color="#089981" if effective_stc_side == "buy" else "#f23645",
            )
        )
    return {
        "symbol": "BTCUSDT",
        "duration_seconds": duration_seconds,
        "candles": [
            {"time": time_value, "open": 100.0, "high": 105.0, "low": 95.0, "close": 101.0},
            {"time": 20, "open": 101.0, "high": 106.0, "low": 96.0, "close": 102.0},
        ],
        "time_labels": {str(time_value): "2026-01-01 00:00:00"},
        "indicators": _merge_indicators(*indicators),
    }


def _reentry_strategy_snapshot(reentry_side: str, *, reentry_stc_side: str | None = None) -> dict:
    snapshot = {
        "symbol": "BTCUSDT",
        "duration_seconds": 300,
        "last_close": 105.0,
        "candles": [
            {"time": 1, "open": 100.0, "high": 106.0, "low": 99.0, "close": 105.0},
            {"time": 2, "open": 105.0, "high": 107.0, "low": 104.0, "close": 106.0},
        ],
        "time_labels": {"1": "2026-01-01 00:00:00"},
        "indicators": _merge_indicators(
            _indicator_series("merged_dkx_hull_ut", "mhull_up", 1, 90.0, marker="Buy"),
            _indicator_series("merged_dkx_hull_ut", "shull_up", 1, 91.0),
            _indicator_series("stc", "stc", 1, 20.0, color="#089981"),
        ),
    }
    snapshot["higher_timeframe"] = _hull_snapshot(86400, "buy")
    snapshot["reentry_higher_timeframe"] = _hull_snapshot(
        3600,
        reentry_side,
        stc_side=reentry_stc_side,
    )
    return snapshot


class AlwaysBuyStrategy:
    name = "test_always_buy"

    def evaluate(self, context: StrategyContext) -> StrategyResult:
        return StrategyResult("buy", "registered strategy")


class DomainCoreTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        register_strategy("test_always_buy", AlwaysBuyStrategy)

    def test_registered_strategy_is_used_without_exchange_engine(self) -> None:
        evaluator = SignalEvaluator(
            SignalConfig(
                strategy="test_always_buy",
                use_closed_bar=False,
                htf_hull_filter_enabled=False,
            )
        )

        decision = evaluator.evaluate(_snapshot())

        self.assertEqual(decision.action, "place_order")
        self.assertEqual(decision.side, "buy")
        self.assertIn("registered strategy", decision.reason)

    def test_custom_strategy_module_registers_without_exchange_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "custom_strategies.py").write_text(
                """
from tq_app.domain import StrategyResult

class CustomSell:
    name = "custom_sell"
    def evaluate(self, context):
        return StrategyResult("sell", "custom module")

def register_strategies(registry):
    registry.register("custom_sell", CustomSell)
""".strip(),
                encoding="utf-8",
            )
            load_custom_strategies(root)
            decision = SignalEvaluator(
                SignalConfig(
                    strategy="custom_sell",
                    use_closed_bar=False,
                    htf_hull_filter_enabled=False,
                )
            ).evaluate(_snapshot())

        self.assertEqual(decision.side, "sell")
        self.assertIn("custom module", decision.reason)

    def test_live_engine_delegates_to_the_same_signal_evaluator(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = LiveTradingConfig(
                strategy="test_always_buy",
                use_closed_bar=False,
                htf_hull_filter_enabled=False,
                email_enabled=False,
                state_path=root / "state.json",
                log_path=root / "live.log",
                order_log_path=root / "orders.jsonl",
            )
            direct = SignalEvaluator(SignalConfig.from_object(config)).evaluate(_snapshot())
            through_live = LiveTradingEngine(root, config).evaluate_snapshot(_snapshot())

        self.assertEqual(asdict(through_live), asdict(direct))

    def test_1d_1h_reentry_strategy_declares_its_timeframes(self) -> None:
        evaluator = SignalEvaluator(
            SignalConfig(strategy="stc_1d_1h_reentry", htf_hull_duration_seconds=14400)
        )

        self.assertEqual(evaluator.primary_htf_duration_seconds, 86400)
        self.assertEqual(evaluator.reentry_confirmation_duration_seconds, 3600)

    def test_base_strategy_high_timeframe_requires_stc_color_but_not_extreme_value(self) -> None:
        evaluator = SignalEvaluator(
            SignalConfig(
                strategy="stc_extreme_contrarian",
                htf_hull_duration_seconds=86400,
            )
        )
        snapshot = _reentry_strategy_snapshot("buy")

        decision = evaluator.evaluate(snapshot)

        self.assertEqual(decision.action, "place_order")
        self.assertEqual(decision.side, "buy")
        self.assertIn(":htf:86400:", decision.htf_lock_key or "")

    def test_base_strategy_rejects_opposite_high_timeframe_stc_color(self) -> None:
        evaluator = SignalEvaluator(
            SignalConfig(
                strategy="stc_extreme_contrarian",
                htf_hull_duration_seconds=86400,
            )
        )
        snapshot = _reentry_strategy_snapshot("buy")
        stc = next(
            item for item in snapshot["higher_timeframe"]["indicators"] if item["id"] == "stc"
        )
        stc["series"][0]["data"][0]["color"] = "#f23645"

        decision = evaluator.evaluate(snapshot)

        self.assertEqual(decision.action, "skip")
        self.assertIsNone(decision.side)
        self.assertIn("STC 不是同向色", decision.reason)

    def test_1d_1h_reentry_strategy_allows_same_direction_1h_hull(self) -> None:
        evaluator = SignalEvaluator(SignalConfig(strategy="stc_1d_1h_reentry"))

        decision = evaluator.evaluate(_reentry_strategy_snapshot("buy"))

        self.assertEqual(decision.action, "place_order")
        self.assertEqual(decision.side, "buy")
        self.assertTrue(decision.htf_reentry_allowed)
        self.assertEqual(decision.htf_reentry_context["trend"], "buy")
        self.assertIn(":htf:86400:", decision.htf_lock_key or "")

    def test_1d_1h_reentry_strategy_rejects_opposite_1h_hull_for_reentry(self) -> None:
        evaluator = SignalEvaluator(SignalConfig(strategy="stc_1d_1h_reentry"))

        decision = evaluator.evaluate(_reentry_strategy_snapshot("sell"))

        self.assertEqual(decision.action, "place_order")
        self.assertEqual(decision.side, "buy")
        self.assertFalse(decision.htf_reentry_allowed)
        self.assertEqual(decision.htf_reentry_context["trend"], "sell")

    def test_1d_1h_reentry_strategy_rejects_opposite_1h_stc_color(self) -> None:
        evaluator = SignalEvaluator(SignalConfig(strategy="stc_1d_1h_reentry"))

        decision = evaluator.evaluate(
            _reentry_strategy_snapshot("buy", reentry_stc_side="sell")
        )

        self.assertEqual(decision.action, "place_order")
        self.assertEqual(decision.side, "buy")
        self.assertFalse(decision.htf_reentry_allowed)
        self.assertIsNone(decision.htf_reentry_context["trend"])
        self.assertEqual(decision.htf_reentry_context["stc_trend"], "sell")
        self.assertIn("STC 不是同向色", decision.htf_reentry_context["reason"])

    def test_live_lock_is_bypassed_only_with_reentry_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = LiveTradingEngine(
                root,
                LiveTradingConfig(
                    mode="email",
                    log_only=True,
                    email_enabled=False,
                    state_path=root / "state.json",
                    log_path=root / "live.log",
                    order_log_path=root / "orders.jsonl",
                ),
            )

            def execute(allowed: bool):
                decision = TradeDecision(
                    action="place_order",
                    symbol="BTCUSDT",
                    side="buy",
                    bar_time=1,
                    client_oid=f"oid-{allowed}",
                    htf_lock_key="BTCUSDT:buy:htf:86400:1",
                    htf_reentry_allowed=allowed,
                    htf_reentry_context={"reason": "1H Hull 与 buy 同向" if allowed else "1H Hull 反向"},
                )
                with (
                    patch.object(engine, "_already_executed", return_value=False),
                    patch.object(engine, "_htf_entry_lock", return_value={"entry_count": 1}),
                    patch.object(engine, "sync_local_positions_with_exchange"),
                    patch.object(engine, "_local_same_side_position", return_value=None),
                    patch.object(engine, "_same_side_position_if_available", return_value=None),
                    patch.object(engine, "_record_execution"),
                    patch.object(engine, "_log_result"),
                    patch.object(engine, "_send_email"),
                ):
                    return engine.execute_decision(decision)

            blocked = execute(False)
            allowed = execute(True)

        self.assertTrue(blocked.response.get("htfEntryLocked"))
        self.assertFalse(allowed.response.get("htfEntryLocked", False))
        self.assertTrue(allowed.response.get("logOnly"))

    def test_tick_and_bar_paths_share_disaster_rule(self) -> None:
        policy = RiskPolicy(
            RiskPolicyConfig(
                disaster_stop_points=Decimal("-10"),
                breakeven_trigger_points=Decimal("100"),
                trailing_rules=(),
            )
        )

        tick = policy.evaluate_tick(
            RiskState(), side="buy", entry_price=100, current_price=89
        )
        bar = policy.evaluate_bar(
            RiskState(),
            side="buy",
            entry_price=100,
            high=101,
            low=89,
            close=90,
            bars_since_entry=1,
        )

        self.assertEqual(tick.trigger, "disaster")
        self.assertEqual(bar.trigger, "disaster")
        self.assertEqual(tick.exit_points, Decimal("-10"))
        self.assertEqual(bar.exit_points, Decimal("-10"))

    def test_protected_stop_is_monotonic(self) -> None:
        policy = RiskPolicy(
            RiskPolicyConfig(
                breakeven_trigger_points=Decimal("10"),
                breakeven_stop_points=Decimal("1"),
                trailing_rules=((Decimal("20"), Decimal("0.5")),),
            )
        )

        first = policy.evaluate_tick(
            RiskState(), side="buy", entry_price=100, current_price=125
        )
        second = policy.evaluate_tick(
            first.state, side="buy", entry_price=100, current_price=115
        )

        self.assertEqual(first.state.protected_stop_points, Decimal("12.5"))
        self.assertEqual(second.state.protected_stop_points, Decimal("12.5"))
        self.assertIsNone(second.trigger)


if __name__ == "__main__":
    unittest.main()
