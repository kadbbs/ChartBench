from __future__ import annotations

import math
import unittest

from tq_app.backtesting.performance import (
    attach_deflated_sharpe,
    cscv_probability_of_backtest_overfitting,
    performance_metrics,
    probabilistic_sharpe_ratio,
    realized_daily_equity,
)


class PerformanceMetricsTest(unittest.TestCase):
    def test_daily_metrics_cover_efficiency_tail_and_drawdown(self) -> None:
        periodic_returns = (
            [0.012, -0.008, 0.006, -0.003, 0.004, 0.001] * 40
        )
        equity = [20_000.0]
        for value in periodic_returns:
            equity.append(equity[-1] * (1.0 + value))
        timestamps = [1_700_000_000 + index * 86_400 for index in range(len(equity))]

        metrics = performance_metrics(equity, timestamps)

        self.assertEqual(
            metrics["performance_observation_count"],
            len(periodic_returns),
        )
        self.assertGreater(metrics["cagr_pct"], 0)
        self.assertGreater(metrics["annualized_volatility_pct"], 0)
        self.assertGreater(metrics["sharpe_ratio"], 0)
        self.assertGreater(metrics["sortino_ratio"], 0)
        self.assertGreaterEqual(
            metrics["daily_expected_shortfall_95_pct"],
            metrics["daily_var_95_pct"],
        )
        self.assertGreater(metrics["max_drawdown_duration_days"], 0)
        self.assertGreater(metrics["return_excess_kurtosis"], -3)
        self.assertTrue(0 <= metrics["psr_zero_pct"] <= 100)
        self.assertIn("_daily_returns", metrics)

    def test_psr_rewards_more_evidence_and_penalizes_higher_benchmark(self) -> None:
        short = probabilistic_sharpe_ratio(
            observed_sharpe=0.08,
            benchmark_sharpe=0.0,
            observation_count=30,
            skewness=0.0,
            pearson_kurtosis=3.0,
        )
        long = probabilistic_sharpe_ratio(
            observed_sharpe=0.08,
            benchmark_sharpe=0.0,
            observation_count=300,
            skewness=0.0,
            pearson_kurtosis=3.0,
        )
        demanding = probabilistic_sharpe_ratio(
            observed_sharpe=0.08,
            benchmark_sharpe=0.05,
            observation_count=300,
            skewness=0.0,
            pearson_kurtosis=3.0,
        )

        self.assertIsNotNone(short)
        self.assertIsNotNone(long)
        self.assertIsNotNone(demanding)
        self.assertGreater(long, short)
        self.assertLess(demanding, long)

    def test_deflated_sharpe_and_cscv_are_matrix_level_diagnostics(self) -> None:
        rows = []
        return_series = []
        for candidate in range(4):
            values = [
                0.001
                + (candidate - 1.5) * 0.00008
                + math.sin(index / (3.0 + candidate)) * 0.006
                for index in range(120)
            ]
            equity = [20_000.0]
            for value in values:
                equity.append(equity[-1] * (1.0 + value))
            times = [
                1_700_000_000 + index * 86_400
                for index in range(len(equity))
            ]
            row = performance_metrics(equity, times)
            rows.append(row)
            return_series.append(row["_daily_returns"])

        multiple_testing = attach_deflated_sharpe(rows)
        overfitting = cscv_probability_of_backtest_overfitting(return_series)

        self.assertTrue(multiple_testing["deflated_sharpe_available"])
        self.assertGreaterEqual(
            multiple_testing["deflated_sharpe_benchmark"],
            0,
        )
        self.assertTrue(
            all(item["deflated_sharpe_ratio_pct"] is not None for item in rows)
        )
        self.assertTrue(overfitting["pbo_available"])
        self.assertEqual(overfitting["cscv_segment_count"], 8)
        self.assertEqual(overfitting["cscv_split_count"], 70)
        self.assertTrue(0 <= overfitting["pbo_pct"] <= 100)

    def test_realized_daily_equity_includes_zero_activity_days(self) -> None:
        start = 1_700_000_000
        times, equity = realized_daily_equity(
            initial_equity=20_000,
            pnl_events=[(start + 2 * 86_400, 500)],
            start_time=start,
            end_time=start + 4 * 86_400,
        )

        self.assertEqual(len(times), len(equity))
        self.assertGreaterEqual(len(equity), 5)
        self.assertEqual(equity[-1], 20_500)
        self.assertIn(20_000, equity[:-1])


if __name__ == "__main__":
    unittest.main()
