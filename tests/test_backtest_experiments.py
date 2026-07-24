from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from flask import Flask

from tq_app.backtesting.application import BacktestApplication
from tq_app.backtesting.experiments import (
    BacktestExperimentManager,
    build_experiment_spec,
    cache_coverage,
)
from tq_app.backtesting.web import create_backtest_blueprint
from tq_app.web import create_app


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class _FakeManager:
    def list(self, limit=30):
        return []

    def submit(self, payload):
        return {"run_id": "test", "status": "queued", "total_combinations": 1}

    def get(self, run_id):
        return {"run_id": run_id, "status": "succeeded"}

    def result(self, run_id):
        return {"run_id": run_id, "type": "single", "rows": []}

    def report(self, run_id):
        return {"summary": {"title": run_id}}

    def chart_preview(self, run_id):
        return {"symbol": "BTCUSDT", "candles": []}

    def request_payload(self, run_id):
        return {"profile": "latest_month"}

    def artifacts(self, run_id):
        return [{"name": "matrix.csv", "label": "矩阵完整结果", "size_bytes": 10}]

    def deletion_info(self, run_id):
        return {
            "run_id": run_id,
            "can_delete": True,
            "file_count": 3,
            "size_bytes": 100,
            "dependent_run_ids": [],
        }

    def delete_files(self, run_id, **confirmation):
        return {
            "deleted": True,
            "run_id": run_id,
            "confirmation": confirmation,
        }

    def artifact_path(self, run_id, artifact_name):
        raise FileNotFoundError("研究产物不存在。")


class BacktestExperimentTest(unittest.TestCase):
    def payload(self):
        return {
            "name": "三年稳定性",
            "profile": "btc_5m_range_cached",
            "overrides": {
                "start_time": "2026-06-01 00:00:00",
                "end_time": "2026-06-02 00:00:00",
            },
            "grid": {
                "disaster_stop_points": [-1400, -1800],
                "indicator.stc.length": [60, 80],
            },
        }

    def test_risk_and_indicator_parameters_form_a_cartesian_grid(self) -> None:
        spec = build_experiment_spec(PROJECT_ROOT, self.payload())

        self.assertEqual(len(spec.combinations), 4)
        self.assertEqual(spec.combinations[0]["disaster_stop_points"], -1400.0)
        self.assertEqual(spec.combinations[-1]["indicator.stc.length"], 80)
        self.assertTrue(spec.base_request.cache_enabled)

    def test_1d_1h_reentry_profile_selects_fixed_timeframes(self) -> None:
        spec = build_experiment_spec(
            PROJECT_ROOT,
            {"profile": "btc_5m_range_cached_1d_1h_reentry", "grid": {}},
        )

        resolved = BacktestApplication(PROJECT_ROOT).resolve(spec.base_request)

        self.assertEqual(spec.base_request.strategy, "stc_1d_1h_reentry")
        self.assertEqual(resolved.strategy.primary_htf_duration_seconds, 86400)
        self.assertEqual(resolved.strategy.reentry_confirmation_duration_seconds, 3600)

    def test_24bar_refresh_profile_is_opt_in_and_keeps_three_year_risk_settings(self) -> None:
        spec = build_experiment_spec(
            PROJECT_ROOT,
            {
                "profile": "btc_5m_range_cached_1d_1h_reentry_24bar_refresh",
                "grid": {},
            },
        )

        resolved = BacktestApplication(PROJECT_ROOT).resolve(spec.base_request)

        self.assertEqual(
            spec.base_request.strategy,
            "stc_1d_1h_reentry_24bar_refresh",
        )
        self.assertEqual(spec.base_request.start_time, "2023-03-31 00:00:00")
        self.assertEqual(spec.base_request.end_time, "2026-06-20 00:00:00")
        self.assertEqual(spec.base_request.startup_current_points, -300)
        self.assertEqual(resolved.strategy.primary_htf_duration_seconds, 86400)
        self.assertEqual(resolved.strategy.reentry_confirmation_duration_seconds, 3600)
        self.assertTrue(resolved.strategy.refresh_startup_on_same_side_signal)

    def test_unknown_or_excessive_parameter_grid_is_rejected(self) -> None:
        payload = self.payload()
        payload["grid"] = {"not_a_parameter": [1, 2]}
        with self.assertRaisesRegex(ValueError, "暂不支持"):
            build_experiment_spec(PROJECT_ROOT, payload)

        payload["grid"] = {
            "disaster_stop_points": list(range(17)),
            "breakeven_trigger_points": list(range(17)),
        }
        with self.assertRaisesRegex(ValueError, "最多允许"):
            build_experiment_spec(PROJECT_ROOT, payload)

    def test_numeric_ranges_support_steps_floats_descending_and_mixed_values(self) -> None:
        payload = self.payload()
        payload["grid"] = {
            "startup_check_bars_5m": "18:24:3",
            "trailing_protect_1_ratio": "0.3:0.5:0.1",
            "startup_current_points": "-120:-180:-30",
            "disaster_stop_points": "-1400:-1800:-200,-2200",
        }

        spec = build_experiment_spec(PROJECT_ROOT, payload)

        self.assertEqual(spec.grid["startup_check_bars_5m"], [18, 21, 24])
        self.assertEqual(spec.grid["trailing_protect_1_ratio"], [0.3, 0.4, 0.5])
        self.assertEqual(spec.grid["startup_current_points"], [-120.0, -150.0, -180.0])
        self.assertEqual(spec.grid["disaster_stop_points"], [-1400.0, -1600.0, -1800.0, -2200.0])

    def test_invalid_step_ranges_are_rejected(self) -> None:
        payload = self.payload()
        payload["grid"] = {"breakeven_trigger_points": "600:1000:0"}
        with self.assertRaisesRegex(ValueError, "步长不能为 0"):
            build_experiment_spec(PROJECT_ROOT, payload)

        payload["grid"] = {"breakeven_trigger_points": "600:1000:-100"}
        with self.assertRaisesRegex(ValueError, "方向与范围不一致"):
            build_experiment_spec(PROJECT_ROOT, payload)

        payload["grid"] = {"startup_check_bars_5m": "18:24:0.5"}
        with self.assertRaisesRegex(ValueError, "必须都是整数"):
            build_experiment_spec(PROJECT_ROOT, payload)

    def test_cache_coverage_reports_existing_long_range_cache(self) -> None:
        coverage = cache_coverage(PROJECT_ROOT, self.payload())
        self.assertIn("coverage_pct", coverage)
        self.assertTrue(coverage["path"].endswith("USDT-FUTURES_BTCUSDT_300s_MARKET.csv"))

    def test_blueprint_is_namespaced_and_does_not_replace_chart_routes(self) -> None:
        app = Flask(__name__, template_folder=str(PROJECT_ROOT / "templates"))
        app.register_blueprint(create_backtest_blueprint(_FakeManager(), PROJECT_ROOT))
        client = app.test_client()

        self.assertEqual(client.get("/backtests").status_code, 200)
        catalog = client.get("/api/backtests/catalog").get_json()
        self.assertIn("btc_5m_range_cached", {item["name"] for item in catalog["profiles"]})
        reentry = next(item for item in catalog["strategies"] if item["name"].endswith("1d_1h_reentry"))
        self.assertEqual(reentry["details"]["primary_htf_duration_seconds"], 86400)
        self.assertGreaterEqual(len(reentry["details"]["sections"]), 3)
        estimate = client.post("/api/backtests/estimate", json=self.payload())
        self.assertEqual(estimate.status_code, 200)
        self.assertEqual(estimate.get_json()["combinations"], 4)
        path_estimate = client.post(
            "/api/backtests/estimate",
            json={
                "workflow": "signal_path",
                "action": "matrix",
                "profile": "btc_5m_signal_path",
                "stop_values": [1, 2],
                "take_values": [1, 2, 3],
            },
        )
        self.assertEqual(path_estimate.status_code, 200)
        self.assertEqual(path_estimate.get_json()["combinations"], 6)
        trailing_estimate = client.post(
            "/api/backtests/estimate",
            json={
                "workflow": "signal_path",
                "action": "percent_trailing",
                "profile": "btc_5m_signal_path",
                "hard_stop_pct": 3,
                "trailing_activation_pct": 8,
                "trailing_drawdown_pct": 10,
            },
        )
        self.assertEqual(trailing_estimate.status_code, 200)
        self.assertEqual(trailing_estimate.get_json()["combinations"], 1)
        self.assertIn(
            "固定百分比移动风控",
            trailing_estimate.get_json()["execution_class"],
        )
        self.assertEqual(
            client.get("/api/backtests/runs/test/artifacts").get_json()["artifacts"][0]["name"],
            "matrix.csv",
        )
        self.assertTrue(
            client.get("/api/backtests/runs/test/deletion")
            .get_json()["can_delete"]
        )
        deleted = client.delete(
            "/api/backtests/runs/test",
            json={
                "confirm_run_id": "test",
                "confirm_permanent": True,
            },
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertTrue(deleted.get_json()["deleted"])
        self.assertEqual(client.get("/").status_code, 404)

    def test_completed_run_files_require_exact_confirmation_and_dependency_ack(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            manager = BacktestExperimentManager(project_root)
            try:
                run_dir = manager.root / "baseline"
                run_dir.mkdir()
                (run_dir / "status.json").write_text(
                    json.dumps(
                        {
                            "run_id": "baseline",
                            "name": "基准",
                            "status": "succeeded",
                        }
                    ),
                    encoding="utf-8",
                )
                (run_dir / "artifact.bin").write_bytes(b"12345")
                dependent_dir = manager.root / "matrix"
                dependent_dir.mkdir()
                (dependent_dir / "status.json").write_text(
                    json.dumps(
                        {
                            "run_id": "matrix",
                            "name": "矩阵",
                            "status": "succeeded",
                        }
                    ),
                    encoding="utf-8",
                )
                (dependent_dir / "request.json").write_text(
                    json.dumps({"baseline_run_id": "baseline"}),
                    encoding="utf-8",
                )

                info = manager.deletion_info("baseline")

                self.assertTrue(info["can_delete"])
                self.assertEqual(info["dependent_run_ids"], ["matrix"])
                self.assertGreaterEqual(info["file_count"], 2)
                with self.assertRaisesRegex(ValueError, "确认信息不匹配"):
                    manager.delete_files(
                        "baseline",
                        confirm_run_id="wrong",
                        confirm_permanent=True,
                    )
                with self.assertRaisesRegex(ValueError, "仍被其他实验引用"):
                    manager.delete_files(
                        "baseline",
                        confirm_run_id="baseline",
                        confirm_permanent=True,
                    )

                deleted = manager.delete_files(
                    "baseline",
                    confirm_run_id="baseline",
                    confirm_permanent=True,
                    confirm_dependencies=True,
                )

                self.assertTrue(deleted["deleted"])
                self.assertFalse(deleted["recoverable"])
                self.assertFalse(run_dir.exists())
                self.assertTrue(dependent_dir.exists())
            finally:
                manager.shutdown()

    def test_running_run_cannot_be_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = BacktestExperimentManager(Path(temp_dir))
            try:
                run_dir = manager.root / "running"
                run_dir.mkdir()
                (run_dir / "status.json").write_text(
                    json.dumps(
                        {
                            "run_id": "running",
                            "name": "运行中",
                            "status": "running",
                        }
                    ),
                    encoding="utf-8",
                )

                self.assertFalse(manager.deletion_info("running")["can_delete"])
                with self.assertRaisesRegex(ValueError, "不能删除"):
                    manager.delete_files(
                        "running",
                        confirm_run_id="running",
                        confirm_permanent=True,
                    )
            finally:
                manager.shutdown()

    def test_chart_app_does_not_enable_backtest_routes_by_default(self) -> None:
        app = create_app(object(), PROJECT_ROOT)
        client = app.test_client()

        self.assertEqual(client.get("/").status_code, 200)
        self.assertEqual(client.get("/backtests").status_code, 404)
        self.assertEqual(client.get("/api/backtests/catalog").status_code, 404)


if __name__ == "__main__":
    unittest.main()
