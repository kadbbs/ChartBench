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
            "workflow-select",
            "path-action-select",
            "path-baseline-run-select",
            "path-stop-values-input",
            "path-take-values-input",
            "start-time-input",
            "end-time-input",
            "combination-estimate",
            "run-button",
            "strategy-details-button",
            "strategy-details-modal",
            "strategy-details-content",
            "strategy-details-close",
            "result-table",
            "heatmap",
            "heatmap-metric-select",
            "path-comparison-section",
            "path-comparison-summary",
            "path-comparison-table",
            "path-exit-summary",
            "performance-diagnostics-section",
            "performance-basis-badge",
            "performance-method-note",
            "performance-diagnostics",
            "artifact-list",
            "backtest-chart",
        }:
            self.assertIn(f'id="{element_id}"', TEMPLATE)

    def test_strategy_detail_panel_tracks_selected_catalog_strategy(self) -> None:
        self.assertIn('role="dialog"', TEMPLATE)
        self.assertIn("function effectiveStrategyName", SCRIPT)
        self.assertIn("function renderStrategyDetails", SCRIPT)
        self.assertIn('details.reentry_confirmation_duration_seconds', SCRIPT)
        self.assertIn('details.refresh_startup_on_same_side_signal', SCRIPT)
        self.assertIn("重置启动计时，不加仓", SCRIPT)

    def test_research_form_uses_full_width_progressive_layout(self) -> None:
        for class_name in {
            "builder-header",
            "builder-body",
            "config-grid",
            "parameter-layout",
            "run-dock",
            "workflow-steps",
        }:
            self.assertIn(f'class="{class_name}', TEMPLATE)
        self.assertNotIn('<aside class="builder-card"', TEMPLATE)
        self.assertIn("function profileDisplayName", SCRIPT)
        self.assertIn('class="estimate-item"', SCRIPT)

    def test_path_heatmap_does_not_collapse_hidden_dimensions(self) -> None:
        self.assertIn("function renderPathHeatmap", SCRIPT)
        self.assertIn("byCoordinate.get", SCRIPT)
        self.assertIn("positive_neighbor_ratio", SCRIPT)
        self.assertIn("最终测试 20%", SCRIPT)
        self.assertIn("function renderPathComparison", SCRIPT)
        self.assertIn("validation_return_pct_delta_vs_baseline", SCRIPT)
        self.assertIn("总盈利", SCRIPT)
        self.assertIn("总亏损", SCRIPT)
        self.assertIn("function renderPerformanceDiagnostics", SCRIPT)
        self.assertIn("daily_expected_shortfall_95_pct", SCRIPT)
        self.assertIn("deflated_sharpe_ratio_pct", SCRIPT)
        self.assertIn("矩阵 PBO", SCRIPT)

    def test_path_downloads_prioritize_prompt_ready_indicator_sample(self) -> None:
        self.assertIn("① 给大模型：研究样本（推荐）", SCRIPT)
        self.assertIn("全量 5m K 线与完整指标", SCRIPT)
        self.assertIn("完整 5m + 已闭合 1D", SCRIPT)
        self.assertIn("因果对齐的已闭合日线指标", TEMPLATE)


if __name__ == "__main__":
    unittest.main()
