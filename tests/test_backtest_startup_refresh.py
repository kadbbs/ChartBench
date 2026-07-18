from __future__ import annotations

import unittest
from pathlib import Path

import pandas as pd

from tq_app.backtesting.engine import BacktestConfig, BacktestEngine, PreparedBacktestStudy
from tq_app.backtesting.strategies import BacktestSignal
from tq_app.live_trading import LiveTradingConfig


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class _StartupRefreshStrategy:
    primary_htf_duration_seconds = 86400
    reentry_confirmation_duration_seconds = 3600

    def __init__(self, *, refresh_enabled: bool) -> None:
        self.refresh_startup_on_same_side_signal = refresh_enabled
        if refresh_enabled:
            self.name = "stc_1d_1h_reentry_24bar_refresh"
            self.signal_strategy_name = (
                "stc_extreme_contrarian_1d_1h_reentry_24bar_refresh"
            )
        else:
            self.name = "stc_1d_1h_reentry"
            self.signal_strategy_name = "stc_extreme_contrarian_1d_1h_reentry"

    def evaluate(self, snapshot):
        raise AssertionError("测试使用预计算信号，不应重新求值")


class BacktestStartupRefreshTest(unittest.TestCase):
    def _run(
        self,
        *,
        refresh_enabled: bool,
        refresh_signal_index: int,
        reentry_allowed: bool = True,
    ):
        config = BacktestConfig(
            symbol="BTCUSDT",
            provider="bitget",
            duration_seconds=300,
            warmup_bars=1,
            risk_exits_enabled=True,
            startup_check_bars_5m=3,
            startup_max_favorable_points=300,
            startup_current_points=-1,
            disaster_stop_points=-1_000,
            output_dir=Path("backtest_outputs/test_startup_refresh"),
        )
        strategy = _StartupRefreshStrategy(refresh_enabled=refresh_enabled)
        engine = BacktestEngine(
            project_root=PROJECT_ROOT,
            config=config,
            live_config=LiveTradingConfig(
                strategy=strategy.signal_strategy_name,
                htf_hull_filter_enabled=True,
                htf_hull_duration_seconds=86400,
            ),
            strategy=strategy,
            write_artifacts=False,
        )

        times = [1_000 + 300 * index for index in range(8)]
        candles = [
            {
                "time": time_value,
                "open": 100.0,
                "high": 100.0,
                "low": 98.0 if index in {4, 6} else 100.0,
                "close": 98.0 if index in {4, 6} else 100.0,
            }
            for index, time_value in enumerate(times)
        ]
        signals: list[BacktestSignal | None] = [None] * len(candles)
        signals[1] = BacktestSignal(side="buy", reason="首次有效多信号")
        signals[refresh_signal_index] = BacktestSignal(
            side="buy",
            reason="持仓中的再次有效多信号",
            htf_reentry_allowed=reentry_allowed,
            htf_reentry_context={"trend": "buy", "reason": "1H Hull/STC 同向"},
            refresh_startup_on_same_side_signal=refresh_enabled,
        )
        snapshot = {
            "symbol": "BTCUSDT",
            "provider": "bitget",
            "duration_seconds": 300,
            "candles": candles,
            "volume": [],
            "indicators": [],
            "time_labels": {str(value): str(value) for value in times},
        }
        study = PreparedBacktestStudy(
            full_snapshot=snapshot,
            indicator_parameters={},
            htf_snapshot=None,
            reentry_htf_snapshot=None,
            signals=signals,
            bar_count=len(candles),
            strategy_signature=(
                strategy.name,
                strategy.signal_strategy_name,
                86400,
                3600,
                refresh_enabled,
                1,
            ),
        )
        return engine.run(
            pd.DataFrame({"placeholder": range(len(candles))}),
            prepared_study=study,
        )

    def test_valid_held_signal_refreshes_window_without_adding_position(self) -> None:
        result = self._run(refresh_enabled=True, refresh_signal_index=3)

        self.assertEqual(len(result.trades), 1)
        trade = result.trades[0]
        self.assertEqual(trade.entry_time, 1_300)
        self.assertEqual(trade.exit_time, 2_800)
        self.assertEqual(trade.exit_reason, "startup_failure_stop")
        self.assertEqual(len(trade.startup_refreshes), 1)
        self.assertEqual(trade.startup_refreshes[0]["elapsed_bars"], 2)
        self.assertEqual(trade.startup_refreshes[0]["window_bars"], 3)
        self.assertEqual(trade.startup_refreshes[0]["previous_anchor_time"], 1_300)
        self.assertEqual(trade.startup_refreshes[0]["new_anchor_time"], 1_900)
        self.assertEqual(
            sum(marker["text"] == "OPEN BUY" for marker in result.markers),
            1,
        )
        self.assertEqual(
            sum(marker["text"] == "REFRESH 3B" for marker in result.markers),
            1,
        )
        self.assertTrue(
            result.config["signal_config"]["refresh_startup_on_same_side_signal"]
        )

    def test_signal_at_exact_window_boundary_does_not_refresh(self) -> None:
        result = self._run(refresh_enabled=True, refresh_signal_index=4)

        self.assertEqual(len(result.trades), 1)
        trade = result.trades[0]
        self.assertEqual(trade.exit_time, 2_200)
        self.assertEqual(trade.exit_reason, "startup_failure_stop")
        self.assertEqual(trade.startup_refreshes, [])
        self.assertFalse(any(marker["text"] == "REFRESH 3B" for marker in result.markers))

    def test_original_strategy_keeps_original_startup_deadline(self) -> None:
        result = self._run(refresh_enabled=False, refresh_signal_index=3)

        self.assertEqual(len(result.trades), 1)
        trade = result.trades[0]
        self.assertEqual(trade.exit_time, 2_200)
        self.assertEqual(trade.exit_reason, "startup_failure_stop")
        self.assertEqual(trade.startup_refreshes, [])
        self.assertEqual(
            sum(marker["text"] == "OPEN BUY" for marker in result.markers),
            1,
        )
        self.assertFalse(any(marker["text"] == "REFRESH 3B" for marker in result.markers))
        self.assertFalse(
            result.config["signal_config"]["refresh_startup_on_same_side_signal"]
        )

    def test_refresh_strategy_still_requires_1h_confirmation(self) -> None:
        result = self._run(
            refresh_enabled=True,
            refresh_signal_index=3,
            reentry_allowed=False,
        )

        self.assertEqual(len(result.trades), 1)
        self.assertEqual(result.trades[0].exit_time, 2_200)
        self.assertEqual(result.trades[0].startup_refreshes, [])
        self.assertFalse(any(marker["text"] == "REFRESH 3B" for marker in result.markers))


if __name__ == "__main__":
    unittest.main()
