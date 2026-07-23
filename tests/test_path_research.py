from __future__ import annotations

import copy
import gzip
import tempfile
import unittest
from pathlib import Path

from tq_app.backtesting.experiments import build_path_experiment_spec
from tq_app.backtesting.path_research import (
    PathReplayConfig,
    SignalPathDataset,
    replay_signal_paths,
    run_path_matrix,
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
        "same_side_signal_ids": [],
    }
    return SignalPathDataset(
        manifest={
            "schema_version": 1,
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
