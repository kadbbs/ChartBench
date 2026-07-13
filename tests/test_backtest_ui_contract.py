from __future__ import annotations

import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (PROJECT_ROOT / "templates" / "backtests.html").read_text(encoding="utf-8")
SCRIPT = (PROJECT_ROOT / "static" / "backtests.js").read_text(encoding="utf-8")


class BacktestUiContractTest(unittest.TestCase):
    def test_javascript_dom_references_exist_once_in_template(self) -> None:
        template_ids = re.findall(r'\bid=["\']([^"\']+)', TEMPLATE)
        referenced_ids = set(re.findall(r'\$\(["\']([^"\']+)', SCRIPT))

        self.assertEqual(len(template_ids), len(set(template_ids)))
        self.assertEqual(referenced_ids - set(template_ids), set())

    def test_research_workflow_controls_are_present(self) -> None:
        for element_id in {
            "profile-select",
            "start-time-input",
            "end-time-input",
            "combination-estimate",
            "run-button",
            "result-table",
            "heatmap",
            "backtest-chart",
        }:
            self.assertIn(f'id="{element_id}"', TEMPLATE)


if __name__ == "__main__":
    unittest.main()
