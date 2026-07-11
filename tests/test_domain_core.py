from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from tq_app.domain import (
    RiskPolicy,
    RiskPolicyConfig,
    RiskState,
    SignalConfig,
    SignalEvaluator,
    StrategyContext,
    StrategyResult,
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
