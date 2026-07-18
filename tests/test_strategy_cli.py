from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tq_app.domain import StrategyRegistry, load_custom_strategies


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class StrategyCliTest(unittest.TestCase):
    def _list_from(self, entrypoint: str) -> list[dict[str, object]]:
        result = subprocess.run(
            [sys.executable, entrypoint, "--list-strategies"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        return json.loads(result.stdout)["strategies"]

    def test_live_and_backtest_commands_return_same_catalog(self) -> None:
        live = self._list_from("run_live_trading.py")
        backtest = self._list_from("run_backtest.py")

        self.assertEqual(live, backtest)
        by_name = {item["name"]: item["aliases"] for item in live}
        self.assertEqual(by_name["marker_signal"], ["default", "live_decision"])
        self.assertEqual(by_name["stc_extreme_contrarian"], [])
        self.assertEqual(
            by_name["stc_extreme_contrarian_1d_1h_reentry"],
            ["stc_1d_1h_reentry"],
        )
        self.assertEqual(
            by_name["stc_extreme_contrarian_1d_1h_reentry_24bar_refresh"],
            ["stc_1d_1h_reentry_24bar_refresh"],
        )
        details_by_name = {item["name"]: item["details"] for item in live}
        self.assertIn("STC 小于 25", " ".join(details_by_name["stc_extreme_contrarian"]["sections"][0]["items"]))
        self.assertEqual(
            details_by_name["stc_extreme_contrarian_1d_1h_reentry"]["primary_htf_duration_seconds"],
            86400,
        )
        self.assertEqual(
            details_by_name["stc_extreme_contrarian_1d_1h_reentry"]["reentry_confirmation_duration_seconds"],
            3600,
        )
        refresh_details = details_by_name[
            "stc_extreme_contrarian_1d_1h_reentry_24bar_refresh"
        ]
        self.assertTrue(refresh_details["refresh_startup_on_same_side_signal"])
        self.assertIn("不加仓", refresh_details["tags"])
        self.assertIn("重新计算 24 根", refresh_details["summary"])

    def test_custom_strategy_is_grouped_with_its_alias(self) -> None:
        registry = StrategyRegistry()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "custom_strategies.py").write_text(
                """
class CustomStrategy:
    name = "custom_strategy"
    def evaluate(self, context):
        return None

def register_strategies(registry):
    registry.register("custom_strategy", CustomStrategy, aliases=("custom",))
""".strip(),
                encoding="utf-8",
            )
            load_custom_strategies(root, registry)

        catalog = registry.catalog()
        self.assertEqual(catalog[0]["name"], "custom_strategy")
        self.assertEqual(catalog[0]["aliases"], ["custom"])
        self.assertEqual(catalog[0]["details"]["tags"], ["自定义策略"])
        self.assertIn("尚未提供结构化说明", catalog[0]["details"]["summary"])


if __name__ == "__main__":
    unittest.main()
