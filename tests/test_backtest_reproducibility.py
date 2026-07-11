from __future__ import annotations

import unittest
from pathlib import Path

from tq_app.backtesting.config import apply_backtest_signal_profile, backtest_signal_config_snapshot
from tq_app.config_profiles import (
    _read_flat_yaml,
    available_backtest_profiles,
    available_profiles,
    load_backtest_profile,
)
from tq_app.live_trading import LiveTradingConfig


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class BacktestReproducibilityTest(unittest.TestCase):
    def test_project_and_live_profiles_default_to_bitget(self) -> None:
        defaults = _read_flat_yaml(PROJECT_ROOT / "config" / "defaults.yaml")
        self.assertEqual(defaults["TQ_DEFAULT_PROVIDER"], "bitget")
        self.assertEqual(defaults["LIVE_TRADING_PROVIDER"], "bitget")
        self.assertEqual(defaults["LIVE_TRADING_PRODUCT_TYPE"], "USDT-FUTURES")

        for profile_name in available_profiles(PROJECT_ROOT):
            with self.subTest(profile=profile_name):
                profile = _read_flat_yaml(PROJECT_ROOT / "config" / "profiles" / f"{profile_name}.yaml")
                self.assertEqual(profile["LIVE_TRADING_PROVIDER"], "bitget")
                self.assertEqual(profile["LIVE_TRADING_PRODUCT_TYPE"], "USDT-FUTURES")

    def test_all_named_profiles_pin_provider_and_signal_configuration(self) -> None:
        required_keys = {
            "provider",
            "product_type",
            "kline_type",
            "signal_strategy",
            "signal_mode",
            "use_closed_bar",
            "htf_hull_filter_enabled",
            "htf_hull_duration_seconds",
            "atr_period",
            "risk_exits_enabled",
            "startup_check_bars_5m",
            "startup_max_favorable_points",
            "startup_current_points",
            "disaster_stop_points",
            "breakeven_trigger_points",
            "breakeven_stop_points",
            "trailing_trigger_1_points",
            "trailing_protect_1_ratio",
            "trailing_trigger_2_points",
            "trailing_protect_2_ratio",
            "trailing_trigger_3_points",
            "trailing_protect_3_ratio",
        }
        profiles = available_backtest_profiles(PROJECT_ROOT)
        self.assertTrue(profiles)
        for profile_name in profiles:
            with self.subTest(profile=profile_name):
                profile = load_backtest_profile(PROJECT_ROOT, profile_name)
                self.assertEqual(profile["provider"], "bitget")
                self.assertEqual(profile["product_type"], "USDT-FUTURES")
                self.assertEqual(required_keys - profile.keys(), set())

    def test_profile_values_override_environment_derived_signal_config(self) -> None:
        base = LiveTradingConfig(
            strategy="different_strategy",
            signal_mode="confirmed",
            use_closed_bar=False,
            htf_hull_filter_enabled=False,
            htf_hull_duration_seconds=3600,
            atr_period=7,
        )
        profile = {
            "signal_strategy": "stc_extreme_contrarian",
            "signal_mode": "any",
            "use_closed_bar": "true",
            "htf_hull_filter_enabled": "true",
            "htf_hull_duration_seconds": "14400",
            "atr_period": "14",
        }

        resolved = apply_backtest_signal_profile(base, profile)
        snapshot = backtest_signal_config_snapshot(resolved)

        self.assertEqual(snapshot["strategy"], "stc_extreme_contrarian")
        self.assertEqual(snapshot["signal_mode"], "any")
        self.assertTrue(snapshot["use_closed_bar"])
        self.assertTrue(snapshot["htf_hull_filter_enabled"])
        self.assertEqual(snapshot["htf_hull_duration_seconds"], 14400)
        self.assertEqual(snapshot["atr_period"], 14)
        self.assertEqual(base.strategy, "different_strategy")

    def test_invalid_profile_boolean_fails_instead_of_falling_back(self) -> None:
        with self.assertRaisesRegex(ValueError, "use_closed_bar"):
            apply_backtest_signal_profile(LiveTradingConfig(), {"use_closed_bar": "sometimes"})


if __name__ == "__main__":
    unittest.main()
