from __future__ import annotations

import unittest

import pandas as pd

from tq_app.domain.signals import indicator_context_at
from tq_app.indicators.builtin import StcIndicator


class SignalCausalityTest(unittest.TestCase):
    def test_stc_strategy_color_is_identical_for_full_history_and_prefix(self) -> None:
        close = [
            100.0,
            101.0,
            100.5,
            102.0,
            101.0,
            103.0,
            102.5,
            104.0,
            103.0,
            105.0,
            104.0,
            106.0,
        ]
        bars = pd.DataFrame(
            {
                "time": list(range(1, len(close) + 1)),
                "open": close,
                "high": [value + 1 for value in close],
                "low": [value - 1 for value in close],
                "close": close,
                "volume": [1.0] * len(close),
            }
        )
        indicator = StcIndicator()
        params = {"length": 4, "fast_length": 2, "slow_length": 5, "factor": 0.5}
        full = indicator.build(bars, params)

        for end in range(3, len(bars) + 1):
            prefix = indicator.build(bars.iloc[:end].copy(), params)
            timestamp = int(bars.iloc[end - 1]["time"])
            full_snapshot = {"indicators": [_indicator_payload(full)]}
            prefix_snapshot = {"indicators": [_indicator_payload(prefix)]}

            full_values, full_colors = indicator_context_at(full_snapshot, timestamp)
            prefix_values, prefix_colors = indicator_context_at(prefix_snapshot, timestamp)

            self.assertEqual(full_values["stc.stc"], prefix_values["stc.stc"])
            self.assertEqual(full_colors["stc.stc"], prefix_colors["stc.stc"])


def _indicator_payload(result) -> dict:
    return {
        "id": result.id,
        "series": [
            {
                "id": series.id,
                "data": series.data,
                "options": series.options,
            }
            for series in result.series
        ],
    }


if __name__ == "__main__":
    unittest.main()
