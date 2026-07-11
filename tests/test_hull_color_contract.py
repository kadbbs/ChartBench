from __future__ import annotations

import unittest
from pathlib import Path

import pandas as pd

from tq_app.domain.strategies import hull_band_values
from tq_app.indicators import build_indicator_registry


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HULL_UP_COLOR = "#4caf50"
HULL_DOWN_COLOR = "#f23645"
HULL_UP_FILL = "rgba(76, 175, 80, 0.60)"
HULL_DOWN_FILL = "rgba(242, 54, 69, 0.60)"


def _bars() -> pd.DataFrame:
    closes = [100.0 + index for index in range(70)]
    closes += [closes[-1] - index for index in range(1, 71)]
    closes += [closes[-1] + index for index in range(1, 71)]
    return pd.DataFrame(
        {
            "time": range(1, len(closes) + 1),
            "open": closes,
            "high": [value + 1.0 for value in closes],
            "low": [value - 1.0 for value in closes],
            "close": closes,
            "volume": [1.0] * len(closes),
        }
    )


class HullColorContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        registry = build_indicator_registry(PROJECT_ROOT)
        cls.merged = registry.get("merged_dkx_hull_ut").build(_bars())
        cls.duo_kong = registry.get("duo_kong_line").build(_bars())

    def test_merged_hull_up_is_green_and_down_is_red(self) -> None:
        series = {item.id: item for item in self.merged.series}

        self.assertEqual(series["mhull_up"].options["color"], HULL_UP_COLOR)
        self.assertEqual(series["shull_up"].options["color"], HULL_UP_COLOR)
        self.assertEqual(series["mhull_up"].options["fillColor"], HULL_UP_FILL)
        self.assertEqual(series["mhull_down"].options["color"], HULL_DOWN_COLOR)
        self.assertEqual(series["shull_down"].options["color"], HULL_DOWN_COLOR)
        self.assertEqual(series["mhull_down"].options["fillColor"], HULL_DOWN_FILL)

    def test_strategy_direction_still_uses_stable_series_ids(self) -> None:
        values = {
            "merged_dkx_hull_ut.mhull_up": 101.0,
            "merged_dkx_hull_ut.shull_up": 102.0,
            "merged_dkx_hull_ut.mhull_down": 201.0,
            "merged_dkx_hull_ut.shull_down": 202.0,
        }

        self.assertEqual(hull_band_values(values, "buy"), [101.0, 102.0])
        self.assertEqual(hull_band_values(values, "sell"), [201.0, 202.0])

    def test_duo_kong_line_and_markers_follow_green_long_red_short(self) -> None:
        series = self.duo_kong.series[0]
        point_colors = {point.get("color") for point in series.data if point.get("color")}
        markers = series.options.get("markers") or []
        marker_colors = {marker["text"]: marker["color"] for marker in markers}

        self.assertEqual(series.options["color"], HULL_UP_COLOR)
        self.assertEqual(point_colors, {HULL_UP_COLOR, HULL_DOWN_COLOR})
        self.assertEqual(marker_colors["多"], HULL_UP_COLOR)
        self.assertEqual(marker_colors["空"], HULL_DOWN_COLOR)


if __name__ == "__main__":
    unittest.main()
