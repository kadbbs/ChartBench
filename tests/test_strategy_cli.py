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

        self.assertEqual(
            registry.catalog(),
            [{"name": "custom_strategy", "aliases": ["custom"]}],
        )


if __name__ == "__main__":
    unittest.main()
