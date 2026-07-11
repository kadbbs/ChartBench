from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from tq_app.backtesting.runtime import BacktestMarketRequest, prepare_backtest_market
from tq_app.cli.arguments import add_market_arguments


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class _LiveConfig:
    htf_hull_filter_enabled = True


class _MultiTimeframeStrategy:
    primary_htf_duration_seconds = 86400
    reentry_confirmation_duration_seconds = 3600


class UnifiedCliTest(unittest.TestCase):
    def _run_json(self, *args: str) -> dict:
        result = subprocess.run(
            [sys.executable, "chartbench.py", *args],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
        return json.loads(result.stdout)

    def test_catalog_and_profile_commands(self) -> None:
        strategies = self._run_json("list", "strategies")
        providers = self._run_json("list", "providers")
        matrices = self._run_json("list", "profiles", "--scope", "matrix")

        self.assertIn("stc_extreme_contrarian", {item["name"] for item in strategies["strategies"]})
        self.assertEqual(providers["providers"], ["binance", "bitget", "tianqin"])
        self.assertIn("btc_risk_matrix", matrices["profiles"])

    def test_config_show_redacts_secrets_and_explains_sources(self) -> None:
        payload = self._run_json("config", "show", "--scope", "chart", "--explain")

        password = payload["config"].get("TIANQIN_PASSWORD")
        if password is not None:
            self.assertEqual(password["value"], "***")
            self.assertIn(password["source"], {".env", "process environment"})
        self.assertIn("source", payload["config"]["TQ_CHART_DEFAULT_PROVIDER"])

    def test_config_validation_has_no_runtime_side_effects(self) -> None:
        payload = self._run_json("config", "validate", "--scope", "live", "--profile", "live_5u")

        self.assertTrue(payload["valid"])
        self.assertEqual(payload["errors"], [])

    def test_common_argument_aliases_share_destinations(self) -> None:
        import argparse

        parser = argparse.ArgumentParser()
        add_market_arguments(
            parser,
            provider_default="bitget",
            provider_choices=["bitget"],
            symbol_default="BTCUSDT",
            duration_default=300,
            data_length_default=800,
        )

        canonical = parser.parse_args(["--duration-seconds", "900", "--data-length", "1200"])
        legacy = parser.parse_args(["--duration", "900", "--length", "1200"])

        self.assertEqual(vars(canonical), vars(legacy))

    def test_shared_market_preparation_loads_primary_and_reentry_timeframes(self) -> None:
        calls: list[dict] = []

        def fake_fetch(**kwargs):
            calls.append(kwargs)
            return pd.DataFrame(
                {
                    "datetime": [pd.Timestamp("2026-01-01", tz="UTC")],
                    "open": [1.0],
                    "high": [1.0],
                    "low": [1.0],
                    "close": [1.0],
                    "volume": [1.0],
                }
            )

        request = BacktestMarketRequest(
            provider="bitget",
            symbol="BTCUSDT",
            product_type="USDT-FUTURES",
            duration_seconds=300,
            data_length=10_000,
            start_time_ms=1_700_000_000_000,
            end_time_ms=1_710_000_000_000,
            cache_enabled=True,
        )
        with patch("tq_app.backtesting.runtime.fetch_market_candles", side_effect=fake_fetch):
            prepared = prepare_backtest_market(
                project_root=PROJECT_ROOT,
                request=request,
                live_config=_LiveConfig(),
                strategy=_MultiTimeframeStrategy(),
            )

        self.assertEqual([item["duration_seconds"] for item in calls], [300, 86400, 3600])
        self.assertIsNotNone(prepared.htf_bars)
        self.assertIsNotNone(prepared.reentry_htf_bars)
        self.assertEqual(prepared.primary_htf_duration_seconds, 86400)
        self.assertEqual(prepared.reentry_htf_duration_seconds, 3600)


if __name__ == "__main__":
    unittest.main()
