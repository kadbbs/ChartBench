from __future__ import annotations

import unittest
from pathlib import Path

from tq_app.service import MarketDataService
from tq_app.web import create_app


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ChartProviderConsistencyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = MarketDataService(
            provider="tianqin",
            symbol="KQ.m@SHFE.cu",
            duration_seconds=300,
            data_length=100,
            refresh_ms=200,
            project_root=PROJECT_ROOT,
        )
        self.service._load_contracts = lambda provider: [
            {
                "symbol": "KQ.m@SHFE.cu" if provider == "tianqin" else "BTCUSDT",
                "name": "test",
                "label": "test",
                "exchange_id": provider.upper(),
                "product_id": "test",
            }
        ]
        self.service._symbol_label = lambda provider, symbol: f"{provider}:{symbol}"
        self.service._provider_hint = lambda provider: provider
        self.service._provider_account = lambda provider: {}
        self.service._contract_detail = lambda provider, symbol: {}

    def test_missing_provider_inherits_command_line_provider(self) -> None:
        config = self.service.get_config()
        blank_config = self.service.get_config(provider="  ")

        self.assertEqual(config["provider"], "tianqin")
        self.assertEqual(config["symbol"], "KQ.m@SHFE.cu")
        self.assertEqual(blank_config["provider"], "tianqin")

    def test_config_api_defaults_to_command_line_provider(self) -> None:
        app = create_app(self.service, PROJECT_ROOT)

        with app.test_client() as client:
            response = client.get("/api/config")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["provider"], "tianqin")

    def test_explicit_frontend_provider_switch_still_wins(self) -> None:
        app = create_app(self.service, PROJECT_ROOT)

        with app.test_client() as client:
            response = client.get("/api/config?provider=binance")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["provider"], "binance")
        self.assertEqual(response.get_json()["symbol"], "BTCUSDT")


if __name__ == "__main__":
    unittest.main()
