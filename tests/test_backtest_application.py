from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from tq_app.backtesting.application import BacktestApplication, BacktestRunRequest
from tq_app.backtesting.engine import BacktestEngine
from tq_app.backtesting.runtime import PreparedBacktestMarket


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class BacktestApplicationTest(unittest.TestCase):
    def test_named_profile_and_explicit_overrides_resolve_once(self) -> None:
        request = BacktestRunRequest(
            profile="btc_5m_range_cached",
            symbol="ethusdt",
            start_time="2026-06-01 00:00:00",
            end_time="2026-06-02 00:00:00",
            output_dir=Path("backtest_outputs/test_application"),
        )

        resolved = BacktestApplication(PROJECT_ROOT).resolve(request)

        self.assertEqual(resolved.config.symbol, "ETHUSDT")
        self.assertEqual(resolved.live_config.strategy, "stc_extreme_contrarian")
        self.assertEqual(resolved.market_request.data_length, 289)
        self.assertEqual(resolved.config.run_context["profile"], "btc_5m_range_cached")
        self.assertEqual(resolved.config.output_dir, Path("backtest_outputs/test_application"))

    def test_indicator_parameters_and_artifact_policy_are_forwarded_to_engine(self) -> None:
        frame = pd.DataFrame(
            {
                "datetime": pd.date_range("2026-01-01", periods=90, freq="5min", tz="UTC"),
                "open": [1.0] * 90,
                "high": [1.0] * 90,
                "low": [1.0] * 90,
                "close": [1.0] * 90,
                "volume": [1.0] * 90,
            }
        )
        prepared = PreparedBacktestMarket(frame, None, None, None, None)
        request = BacktestRunRequest(
            profile="latest_month",
            indicator_params={"stc": {"length": 60}},
        )

        with patch("tq_app.backtesting.application.BacktestEngine") as engine_type:
            engine_type.return_value.run.return_value = object()
            BacktestApplication(PROJECT_ROOT).run(request, prepared_market=prepared, write_artifacts=False)

        kwargs = engine_type.call_args.kwargs
        self.assertEqual(kwargs["indicator_params"], {"stc": {"length": 60}})
        self.assertFalse(kwargs["write_artifacts"])

    def test_invalid_time_window_is_rejected_before_market_access(self) -> None:
        request = BacktestRunRequest(
            profile="latest_month",
            start_time="2026-06-02 00:00:00",
            end_time="2026-06-01 00:00:00",
        )
        with self.assertRaisesRegex(ValueError, "必须早于"):
            BacktestApplication(PROJECT_ROOT).resolve(request)

    def test_prepared_study_preserves_engine_results(self) -> None:
        length = 180
        values = [100 + ((index % 30) - 15) * 0.4 for index in range(length)]
        frame = pd.DataFrame(
            {
                "datetime": pd.date_range("2026-01-01", periods=length, freq="5min", tz="UTC"),
                "open": values,
                "high": [value + 1 for value in values],
                "low": [value - 1 for value in values],
                "close": values[1:] + values[-1:],
                "volume": [1.0] * length,
            }
        )
        request = BacktestRunRequest(profile="latest_month", warmup_bars=80)
        resolved = BacktestApplication(PROJECT_ROOT).resolve(request)
        direct_engine = BacktestEngine(
            project_root=PROJECT_ROOT,
            config=resolved.config,
            live_config=resolved.live_config,
            strategy=resolved.strategy,
            write_artifacts=False,
        )
        direct = direct_engine.run(frame)
        prepared_engine = BacktestEngine(
            project_root=PROJECT_ROOT,
            config=resolved.config,
            live_config=resolved.live_config,
            strategy=resolved.strategy,
            write_artifacts=False,
        )
        study = prepared_engine.prepare_study(frame)
        with patch.object(prepared_engine.strategy, "evaluate", side_effect=AssertionError("signal recomputed")):
            reused = prepared_engine.run(frame, prepared_study=study)

        self.assertEqual(direct.metrics, reused.metrics)
        self.assertEqual(direct.markers, reused.markers)
        self.assertEqual([item.net_pnl for item in direct.trades], [item.net_pnl for item in reused.trades])


if __name__ == "__main__":
    unittest.main()
