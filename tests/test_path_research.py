from __future__ import annotations

import copy
import gzip
import json
import tempfile
import unittest
from pathlib import Path

from tq_app.backtesting.experiments import (
    _upgrade_legacy_path_baseline_summary,
    build_path_experiment_spec,
)
from tq_app.backtesting.path_research import (
    PATH_BAR_COLUMNS,
    PATH_FEATURE_DEFINITIONS,
    PathReplayConfig,
    SignalPathDataset,
    _attach_closed_daily_features,
    baseline_summary,
    replay_signal_paths,
    run_path_matrix,
    write_path_matrix_csv,
    write_signal_path_artifacts,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class PathExperimentSpecTest(unittest.TestCase):
    def test_signal_path_profile_is_5m_daily_and_risk_free(self) -> None:
        spec = build_path_experiment_spec(
            PROJECT_ROOT,
            {
                "workflow": "signal_path",
                "action": "matrix",
                "profile": "btc_5m_signal_path",
                "stop_unit": "atr",
                "stop_values": "0.5:1.0:0.25",
                "take_values": [1, 2],
            },
        )

        self.assertEqual(spec.base_request.duration_seconds, 300)
        self.assertFalse(spec.base_request.risk_exits_enabled)
        self.assertEqual(spec.stop_values, [0.5, 0.75, 1.0])
        self.assertEqual(spec.take_values, [1.0, 2.0])
        self.assertEqual(spec.combination_count, 6)
        self.assertEqual(spec.base_request.indicator_params["stc"]["length"], 80)
        self.assertEqual(
            spec.base_request.indicator_params["merged_dkx_hull_ut"]["hull_variation"],
            "Hma",
        )

    def test_path_grid_rejects_invalid_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "大于 0"):
            build_path_experiment_spec(
                PROJECT_ROOT,
                {
                    "workflow": "signal_path",
                    "action": "matrix",
                    "profile": "btc_5m_signal_path",
                    "stop_values": [0, 1],
                    "take_values": [1],
                },
            )


class PathReplayTest(unittest.TestCase):
    def test_take_profit_and_conservative_ambiguous_bar(self) -> None:
        dataset = _dataset()
        take = replay_signal_paths(
            dataset,
            PathReplayConfig(
                stop_unit="percent",
                stop_value=1,
                take_value=2,
                intrabar_policy="take_first",
                reveal_test=True,
            ),
        )
        conservative = replay_signal_paths(
            dataset,
            PathReplayConfig(
                stop_unit="percent",
                stop_value=1,
                take_value=2,
                intrabar_policy="stop_first",
                reveal_test=True,
            ),
        )

        self.assertEqual(take["take_profit_count"], 1)
        self.assertEqual(conservative["stop_loss_count"], 1)
        self.assertEqual(conservative["ambiguous_bar_count"], 1)
        self.assertLess(conservative["net_profit"], take["net_profit"])

    def test_matrix_has_exact_two_dimensional_cells_and_hides_test(self) -> None:
        result = run_path_matrix(
            _dataset(),
            stop_unit="percent",
            stop_values=[0.5, 1.0],
            take_values=[1.0, 2.0, 3.0],
            reveal_test=False,
        )

        coordinates = {
            (
                row["parameters"]["stop_loss"],
                row["parameters"]["take_profit"],
            )
            for row in result["rows"]
        }
        self.assertEqual(result["combination_count"], 6)
        self.assertEqual(len(coordinates), 6)
        self.assertTrue(all(row["test_return_pct"] is None for row in result["rows"]))
        self.assertEqual(result["heatmap"]["x_key"], "stop_loss")
        self.assertEqual(result["heatmap"]["y_key"], "take_profit")
        self.assertIsNone(result["baseline"]["test_return_pct"])
        for row in result["rows"]:
            self.assertEqual(
                row["baseline_return_pct"],
                result["baseline"]["return_pct"],
            )
            self.assertAlmostEqual(
                row["return_pct_delta_vs_baseline"],
                row["return_pct"] - result["baseline"]["return_pct"],
            )
            self.assertIn("sharpe_ratio", row)
            self.assertIn("deflated_sharpe_ratio_pct", row)
            self.assertNotIn("_daily_returns", row)
        self.assertIn("pbo_pct", result["statistical_validation"])
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "matrix.csv"
            write_path_matrix_csv(path, result["rows"])
            header = path.read_text(encoding="utf-8").splitlines()[0]
            self.assertIn("baseline_net_profit", header)
            self.assertIn("net_profit_delta_vs_baseline", header)

    def test_replay_reports_profit_loss_and_trade_breakdown(self) -> None:
        result = replay_signal_paths(
            _dataset(),
            PathReplayConfig(reveal_test=True),
        )

        self.assertEqual(result["gross_profit"], 300.0)
        self.assertEqual(result["gross_loss"], 0.0)
        self.assertEqual(result["net_profit"], 300.0)
        self.assertEqual(result["winning_trade_count"], 1)
        self.assertEqual(result["losing_trade_count"], 0)
        self.assertEqual(result["breakeven_trade_count"], 0)
        self.assertEqual(result["average_trade_pnl"], 300.0)
        self.assertEqual(result["average_holding_bars"], 2.0)

    def test_baseline_path_statistics_exclude_hidden_test_episodes(self) -> None:
        dataset = _dataset()
        hidden = copy.deepcopy(dataset.episodes[0])
        hidden.update(
            id=2,
            split="test",
            holding_bars=999,
            mfe_atr=999.0,
            mae_atr=-999.0,
        )
        dataset.episodes.append(hidden)

        summary = baseline_summary(dataset)["best"]

        self.assertEqual(summary["closed_episode_count"], 2)
        self.assertEqual(summary["visible_closed_episode_count"], 1)
        self.assertEqual(summary["median_holding_bars"], 2)
        self.assertEqual(summary["median_mfe_atr"], 4.0)
        self.assertEqual(summary["median_mae_atr"], -2.0)
        self.assertEqual(summary["path_stat_scope"], "research_and_validation")

    def test_hidden_test_does_not_change_primary_metrics(self) -> None:
        dataset = _dataset()
        test_episode = copy.deepcopy(dataset.episodes[0])
        test_episode["id"] = 2
        test_episode["split"] = "test"
        dataset.episodes.append(test_episode)

        hidden = replay_signal_paths(
            dataset,
            PathReplayConfig(
                stop_unit="percent",
                stop_value=1,
                take_value=2,
                intrabar_policy="take_first",
                reveal_test=False,
            ),
        )
        revealed = replay_signal_paths(
            dataset,
            PathReplayConfig(
                stop_unit="percent",
                stop_value=1,
                take_value=2,
                intrabar_policy="take_first",
                reveal_test=True,
            ),
        )

        self.assertEqual(hidden["trade_count"], 1)
        self.assertIsNone(hidden["test_return_pct"])
        self.assertEqual(revealed["trade_count"], 2)
        self.assertGreater(revealed["return_pct"], hidden["return_pct"])

    def test_cross_boundary_position_is_marked_without_future_exit_leakage(self) -> None:
        dataset = _dataset()
        dataset.bars[1]["close"] = 101.0
        dataset.manifest["splits"] = {
            "research_end": dataset.bars[0]["time"],
            "validation_end": dataset.bars[1]["time"],
        }

        hidden = replay_signal_paths(
            dataset,
            PathReplayConfig(reveal_test=False),
        )
        revealed = replay_signal_paths(
            dataset,
            PathReplayConfig(reveal_test=True),
        )

        self.assertEqual(hidden["window_mark_count"], 1)
        self.assertEqual(hidden["net_profit"], 100.0)
        self.assertEqual(hidden["return_pct"], 0.5)
        self.assertAlmostEqual(
            hidden["return_pct"],
            hidden["performance_period_return_pct"],
        )
        self.assertEqual(revealed["window_mark_count"], 0)
        self.assertEqual(revealed["net_profit"], 300.0)
        self.assertIsNone(hidden["test_return_pct"])

    def test_atr_distance_uses_closed_signal_bar_not_execution_bar(self) -> None:
        dataset = _dataset()
        dataset.bars[0]["atr"] = 1.0
        dataset.bars[1]["atr"] = 100.0
        dataset.signals[0]["signal_index"] = 0
        dataset.signals[0]["execution_index"] = 1
        dataset.signals[0]["execution_time"] = dataset.bars[1]["time"]
        episode = dataset.episodes[0]
        episode["signal_index"] = 0
        episode["entry_index"] = 1
        episode["entry_time"] = dataset.bars[1]["time"]
        episode["entry_price"] = dataset.bars[1]["open"]

        result = replay_signal_paths(
            dataset,
            PathReplayConfig(
                stop_unit="atr",
                stop_value=1,
                take_value=100,
                reveal_test=True,
            ),
        )

        self.assertEqual(result["stop_loss_count"], 1)
        self.assertEqual(result["reverse_exit_count"], 0)

    def test_gap_at_open_is_resolved_before_intrabar_priority(self) -> None:
        dataset = _dataset()
        dataset.bars[0].update(high=101.0, low=99.5)
        dataset.bars[1].update(open=103.0, high=104.0, low=98.0)

        result = replay_signal_paths(
            dataset,
            PathReplayConfig(
                stop_unit="percent",
                stop_value=1,
                take_value=2,
                intrabar_policy="stop_first",
                reveal_test=True,
            ),
        )

        self.assertEqual(result["take_profit_count"], 1)
        self.assertEqual(result["ambiguous_bar_count"], 0)

    def test_artifacts_export_research_only_llm_samples(self) -> None:
        dataset = _dataset()
        with tempfile.TemporaryDirectory() as temp_dir:
            artifacts = write_signal_path_artifacts(dataset, Path(temp_dir))
            names = {item["name"] for item in artifacts}

            self.assertIn("dataset/episodes.csv", names)
            self.assertIn("dataset/bars.csv.gz", names)
            self.assertIn("dataset/llm_research_samples.jsonl.gz", names)
            self.assertTrue(
                (Path(temp_dir) / "dataset" / "replay.json.gz").is_file()
            )

    def test_llm_sample_declares_full_indicator_feature_schema(self) -> None:
        dataset = _dataset()
        dataset.episodes[0]["split"] = "research"
        dataset.episodes[0]["context_start_index"] = 0
        dataset.episodes[0]["path_end_index"] = 2
        dataset.manifest["splits"] = {"research_end": dataset.bars[-1]["time"]}
        with tempfile.TemporaryDirectory() as temp_dir:
            write_signal_path_artifacts(dataset, Path(temp_dir))
            with gzip.open(
                Path(temp_dir) / "dataset" / "llm_research_samples.jsonl.gz",
                "rt",
                encoding="utf-8",
            ) as file:
                payload = json.loads(file.readline())

        self.assertEqual(payload["bar_columns"], list(PATH_BAR_COLUMNS))
        self.assertEqual(payload["feature_definitions"], PATH_FEATURE_DEFINITIONS)
        self.assertEqual(set(PATH_FEATURE_DEFINITIONS), set(PATH_BAR_COLUMNS[7:]))
        self.assertIn("ut_trailing_stop", payload["bar_columns"])
        self.assertIn("d1_stc", payload["bar_columns"])

    def test_daily_features_use_latest_fully_closed_candle(self) -> None:
        daily_times = [0, 86_400, 172_800]
        daily_snapshot = {
            "candles": [{"time": time_value} for time_value in daily_times],
            "indicators": [
                {
                    "id": "stc",
                    "series": [
                        {
                            "id": "stc",
                            "data": [
                                {"time": 0, "value": 20.0, "signal_color": "#089981"},
                                {"time": 86_400, "value": 80.0, "signal_color": "#f23645"},
                                {"time": 172_800, "value": 30.0, "signal_color": "#089981"},
                            ],
                        }
                    ],
                },
                {
                    "id": "merged_dkx_hull_ut",
                    "series": [],
                    "features": {
                        "time": daily_times,
                        "mhull": [10.0, 20.0, 30.0],
                        "shull": [9.0, 21.0, 29.0],
                        "hull_direction": [1, -1, 1],
                    },
                },
            ],
        }
        bars = [
            {"time": 86_100},
            {"time": 86_400},
            {"time": 172_500},
            {"time": 172_800},
        ]

        _attach_closed_daily_features(
            bars,
            daily_snapshot,
            low_duration_seconds=300,
        )

        self.assertEqual(bars[0]["d1_bar_time"], 0)
        self.assertEqual(bars[1]["d1_bar_time"], 0)
        self.assertEqual(bars[2]["d1_bar_time"], 86_400)
        self.assertEqual(bars[0]["d1_trend"], 1)
        self.assertEqual(bars[2]["d1_trend"], -1)
        self.assertNotEqual(bars[1]["d1_stc"], 80.0)

    def test_legacy_baseline_summary_is_rebuilt_from_internal_dataset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir)
            write_signal_path_artifacts(_dataset(), run_dir)
            upgraded = _upgrade_legacy_path_baseline_summary(
                run_dir,
                {
                    "type": "signal_path_baseline",
                    "run_id": "legacy",
                    "name": "旧基准",
                    "best": {"net_profit": 300.0},
                },
            )

            self.assertEqual(upgraded["run_id"], "legacy")
            self.assertEqual(upgraded["name"], "旧基准")
            self.assertEqual(upgraded["best"]["gross_profit"], 300.0)
            self.assertEqual(upgraded["best"]["winning_trade_count"], 1)
            self.assertTrue((run_dir / "summary.json").is_file())

    def test_llm_export_excludes_episode_crossing_into_validation(self) -> None:
        dataset = _dataset()
        dataset.episodes[0]["split"] = "research"
        dataset.manifest["splits"] = {
            "research_end": dataset.bars[1]["time"],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            write_signal_path_artifacts(dataset, Path(temp_dir))
            with gzip.open(
                Path(temp_dir) / "dataset" / "llm_research_samples.jsonl.gz",
                "rt",
                encoding="utf-8",
            ) as file:
                self.assertEqual(file.read(), "")


def _dataset() -> SignalPathDataset:
    prices = [
        (100.0, 103.0, 98.0, 101.0),
        (101.0, 104.0, 100.0, 103.0),
        (103.0, 104.0, 102.0, 103.0),
    ]
    bars = [
        {
            "index": index,
            "time": 1_700_000_000 + index * 300,
            "time_label": f"bar-{index}",
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": 1.0,
            "atr": 1.0,
            "stc": 20.0,
            "stc_direction": 1,
            "mhull_up": 99.0,
            "shull_up": 98.5,
            "mhull_down": None,
            "shull_down": None,
        }
        for index, (open_price, high, low, close) in enumerate(prices)
    ]
    signals = [
        {
            "id": 1,
            "side": "buy",
            "relation": "entry",
            "signal_index": 0,
            "execution_index": 0,
            "signal_time": bars[0]["time"],
            "execution_time": bars[0]["time"],
        },
        {
            "id": 2,
            "side": "sell",
            "relation": "reverse",
            "signal_index": 1,
            "execution_index": 2,
            "signal_time": bars[1]["time"],
            "execution_time": bars[2]["time"],
        },
    ]
    episode = {
        "id": 1,
        "side": "buy",
        "status": "closed",
        "split": "validation",
        "entry_signal_id": 1,
        "signal_index": 0,
        "signal_time": bars[0]["time"],
        "entry_index": 0,
        "entry_time": bars[0]["time"],
        "entry_price": 100.0,
        "exit_index": 2,
        "exit_time": bars[2]["time"],
        "exit_price": 103.0,
        "holding_bars": 2,
        "mfe_atr": 4.0,
        "mae_atr": -2.0,
        "same_side_signal_ids": [],
    }
    return SignalPathDataset(
        manifest={
            "schema_version": 2,
            "dataset_id": "test",
            "symbol": "BTCUSDT",
            "execution": {
                "initial_equity": 20_000,
                "margin_amount": 1_000,
                "margin_ratio_per_trade": 0,
                "leverage": 10,
                "fee_rate": 0,
                "slippage_rate": 0,
            },
        },
        bars=bars,
        signals=signals,
        episodes=[episode],
    )


if __name__ == "__main__":
    unittest.main()
