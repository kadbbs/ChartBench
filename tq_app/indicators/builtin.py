from __future__ import annotations

from typing import Any

import pandas as pd

from tq_app.models import IndicatorMeta, IndicatorResult, SeriesDefinition

from .base import Indicator, IndicatorRegistry

TV_DOWN = "#f23645"
TV_ACCENT = "#2962ff"
TV_SIGNAL = "#ff9800"
TV_UPPER = "#ff6d6d"
TV_LOWER = "#00c076"


def _line_points(df: pd.DataFrame, column: str) -> list[dict[str, Any]]:
    return [
        {"time": int(row.time), "value": None if pd.isna(row.value) else float(row.value)}
        for row in df[["time", column]].rename(columns={column: "value"}).itertuples(index=False)
    ]


def _histogram_points(df: pd.DataFrame, column: str) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for row in df[["time", column]].rename(columns={column: "value"}).itertuples(index=False):
        value = None if pd.isna(row.value) else float(row.value)
        color = TV_DOWN if (value or 0) >= 0 else TV_LOWER
        points.append({"time": int(row.time), "value": value, "color": color})
    return points


class AtrBandsIndicator(Indicator):
    meta = IndicatorMeta(
        id="atr_bands",
        name="ATR Bands",
        pane="price",
        description="基于 ATR 的上下轨，默认参数 N=14, M=1.5。",
        enabled_by_default=True,
        params=[
            {"key": "period", "label": "ATR周期", "type": "int", "default": 14, "min": 1, "max": 500, "step": 1},
            {"key": "multiplier", "label": "倍数", "type": "float", "default": 1.5, "min": 0.1, "max": 20, "step": 0.1},
            {
                "key": "basis",
                "label": "基准",
                "type": "string",
                "default": "high_low",
                "options": ["high_low", "close", "hl2", "hlc3"],
            },
        ],
    )

    def __init__(self, period: int = 14, multiplier: float = 1.5) -> None:
        self.period = period
        self.multiplier = multiplier

    def build(self, bars: pd.DataFrame, params: dict[str, Any] | None = None) -> IndicatorResult:
        resolved = self.resolve_params(params)
        period = max(1, int(resolved.get("period", self.period)))
        multiplier = max(0.0, float(resolved.get("multiplier", self.multiplier)))
        basis = str(resolved.get("basis", "high_low"))

        df = bars.copy()
        prev_close = df["close"].shift(1)
        tr1 = df["high"] - df["low"]
        tr2 = (df["high"] - prev_close).abs()
        tr3 = (df["low"] - prev_close).abs()
        atr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1).rolling(period, min_periods=period).mean()
        if basis == "close":
            df["upper"] = df["close"] + atr * multiplier
            df["lower"] = df["close"] - atr * multiplier
        elif basis == "hl2":
            mid = (df["high"] + df["low"]) / 2
            df["upper"] = mid + atr * multiplier
            df["lower"] = mid - atr * multiplier
        elif basis == "hlc3":
            mid = (df["high"] + df["low"] + df["close"]) / 3
            df["upper"] = mid + atr * multiplier
            df["lower"] = mid - atr * multiplier
        else:
            df["upper"] = df["high"] + atr * multiplier
            df["lower"] = df["low"] - atr * multiplier
        return IndicatorResult(
            id=self.meta.id,
            name=self.meta.name,
            pane=self.meta.pane,
            series=[
                SeriesDefinition(
                    id="atr_bands_upper",
                    name=f"Upper({period},{multiplier:g})",
                    pane="price",
                    series_type="line",
                    data=_line_points(df, "upper"),
                    options={"color": TV_UPPER, "lineWidth": 1, "lastValueVisible": False, "priceLineVisible": False},
                ),
                SeriesDefinition(
                    id="atr_bands_lower",
                    name=f"Lower({period},{multiplier:g})",
                    pane="price",
                    series_type="line",
                    data=_line_points(df, "lower"),
                    options={"color": TV_LOWER, "lineWidth": 1, "lastValueVisible": False, "priceLineVisible": False},
                ),
            ],
        )


class MacdIndicator(Indicator):
    meta = IndicatorMeta(
        id="macd",
        name="MACD",
        pane="indicator",
        description="经典 MACD，默认参数 12/26/9。",
        enabled_by_default=True,
    )

    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9) -> None:
        self.fast = fast
        self.slow = slow
        self.signal = signal

    def build(self, bars: pd.DataFrame, params: dict[str, Any] | None = None) -> IndicatorResult:
        df = bars.copy()
        ema_fast = df["close"].ewm(span=self.fast, adjust=False).mean()
        ema_slow = df["close"].ewm(span=self.slow, adjust=False).mean()
        df["diff"] = ema_fast - ema_slow
        df["dea"] = df["diff"].ewm(span=self.signal, adjust=False).mean()
        df["hist"] = (df["diff"] - df["dea"]) * 2
        return IndicatorResult(
            id=self.meta.id,
            name=self.meta.name,
            pane=self.meta.pane,
            series=[
                SeriesDefinition(
                    id="macd_diff",
                    name="DIFF",
                    pane="indicator",
                    series_type="line",
                    data=_line_points(df, "diff"),
                    options={"color": TV_ACCENT, "lineWidth": 2, "priceLineVisible": False},
                ),
                SeriesDefinition(
                    id="macd_dea",
                    name="DEA",
                    pane="indicator",
                    series_type="line",
                    data=_line_points(df, "dea"),
                    options={"color": TV_SIGNAL, "lineWidth": 2, "priceLineVisible": False},
                ),
                SeriesDefinition(
                    id="macd_hist",
                    name="Histogram",
                    pane="indicator",
                    series_type="histogram",
                    data=_histogram_points(df, "hist"),
                    options={"base": 0, "priceLineVisible": False},
                ),
            ],
        )


def register_builtin_indicators(registry: IndicatorRegistry) -> None:
    registry.register(AtrBandsIndicator())
    registry.register(MacdIndicator())
