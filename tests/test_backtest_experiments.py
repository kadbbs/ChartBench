from __future__ import annotations

import unittest
from pathlib import Path

from flask import Flask

from tq_app.backtesting.experiments import build_experiment_spec, cache_coverage
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
        estimate = client.post("/api/backtests/estimate", json=self.payload())
        self.assertEqual(estimate.status_code, 200)
        self.assertEqual(estimate.get_json()["combinations"], 4)
        self.assertEqual(client.get("/").status_code, 404)

    def test_chart_app_does_not_enable_backtest_routes_by_default(self) -> None:
        app = create_app(object(), PROJECT_ROOT)
        client = app.test_client()

        self.assertEqual(client.get("/").status_code, 200)
        self.assertEqual(client.get("/backtests").status_code, 404)
        self.assertEqual(client.get("/api/backtests/catalog").status_code, 404)


if __name__ == "__main__":
    unittest.main()
