from __future__ import annotations

import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (PROJECT_ROOT / "templates" / "index.html").read_text(encoding="utf-8")
SCRIPT = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")


class ChartUiContractTest(unittest.TestCase):
    def test_javascript_dom_references_exist_once_in_template(self) -> None:
        template_ids = re.findall(r'\bid=["\']([^"\']+)', TEMPLATE)
        referenced_ids = set(re.findall(r'getElementById\(["\']([^"\']+)', SCRIPT))

        self.assertEqual(len(template_ids), len(set(template_ids)), "模板中存在重复 id")
        self.assertEqual(referenced_ids - set(template_ids), set())

    def test_removed_chart_controls_do_not_return(self) -> None:
        removed_ids = {
            "bar-mode-select",
            "range-ticks-input",
            "brick-length-input",
            "cursor-time",
            "toolbar-provider",
            "toolbar-symbol",
            "toolbar-duration",
            "toolbar-save-template",
            "toolbar-reset-template",
        }

        for element_id in removed_ids:
            self.assertNotIn(f'id="{element_id}"', TEMPLATE)
            self.assertNotIn(f'getElementById("{element_id}")', SCRIPT)

    def test_compact_market_controls_remain_available(self) -> None:
        required_ids = {
            "provider-select",
            "symbol-select",
            "duration-select",
            "watchlist-tab-all",
            "watchlist-tab-favorites",
            "watchlist-toggle-current",
            "stream-status",
            "contract-detail-card",
            "indicator-form",
            "chart-stack",
        }

        for element_id in required_ids:
            self.assertIn(f'id="{element_id}"', TEMPLATE)

        self.assertIn('<details id="contract-detail-card"', TEMPLATE)
        self.assertIn('<details class="panel studies-panel"', TEMPLATE)


if __name__ == "__main__":
    unittest.main()
