from __future__ import annotations

import unittest
from pathlib import Path

from tq_app.backtesting.engine import BacktestConfig, BacktestEngine, PreparedBacktestStudy
from tq_app.backtesting.strategies import BacktestSignal
from tq_app.live_trading import LiveTradingConfig


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class _ReentryStrategy:
    name = "stc_1d_1h_reentry"
    signal_strategy_name = "stc_extreme_contrarian_1d_1h_reentry"
    primary_htf_duration_seconds = 86400
    reentry_confirmation_duration_seconds = 3600

    def evaluate(self, snapshot):
        raise AssertionError("测试使用预计算信号，不应重新求值")


class BacktestReentryTest(unittest.TestCase):
    def _run(self, *, reentry_allowed: bool):
        config = BacktestConfig(
            symbol="BTCUSDT",
            provider="bitget",
            duration_seconds=300,
            warmup_bars=1,
            risk_exits_enabled=True,
            disaster_stop_points=-1,
            output_dir=Path("backtest_outputs/test_reentry"),
        )
        strategy = _ReentryStrategy()
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
        times = [1_000, 1_300, 1_600, 1_900, 2_200]
        candles = [
            {"time": times[0], "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0},
            {"time": times[1], "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0},
            {"time": times[2], "open": 100.0, "high": 100.0, "low": 98.0, "close": 98.0},
            {"time": times[3], "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0},
            {"time": times[4], "open": 100.0, "high": 100.0, "low": 98.0, "close": 98.0},
        ]
        lock_key = "BTCUSDT:buy:htf:86400:1000"
        signals = [
            None,
            BacktestSignal(side="buy", reason="首次 5m 多信号", htf_lock_key=lock_key),
            None,
            BacktestSignal(
                side="buy",
                reason="后续 5m 多信号",
                htf_lock_key=lock_key,
                htf_reentry_allowed=reentry_allowed,
                htf_reentry_context={
                    "trend": "buy" if reentry_allowed else "sell",
                    "reason": "1H Hull/STC 与 buy 同向，允许重复开仓" if reentry_allowed else "1H Hull/STC 未同向",
                },
            ),
            None,
        ]
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
                False,
                1,
            ),
        )
        return engine.run(
            _bars_placeholder(len(candles)),
            prepared_study=study,
        )

    def test_same_1d_segment_reopens_after_1h_same_direction_confirmation(self) -> None:
        result = self._run(reentry_allowed=True)

        self.assertEqual(len(result.trades), 2)
        self.assertEqual([trade.entry_time for trade in result.trades], [1_300, 1_900])
        self.assertIn("同一 1D Hull 阶段重复开仓", result.trades[1].signal_reason)
        self.assertIn("1H Hull/STC 与 buy 同向", result.trades[1].signal_reason)
        self.assertEqual(sum(marker["text"] == "OPEN BUY" for marker in result.markers), 2)

    def test_same_1d_segment_blocks_reentry_without_1h_confirmation(self) -> None:
        result = self._run(reentry_allowed=False)

        self.assertEqual(len(result.trades), 1)
        self.assertEqual(sum(marker["text"] == "OPEN BUY" for marker in result.markers), 1)


def _bars_placeholder(length: int):
    import pandas as pd

    return pd.DataFrame({"placeholder": range(length)})


if __name__ == "__main__":
    unittest.main()
