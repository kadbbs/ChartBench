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
TV_STC_UP = "rgba(38, 166, 154, 0.8)"
TV_STC_DOWN = "rgba(239, 83, 80, 0.8)"
TV_STC_BAND = "rgba(120, 144, 156, 0.16)"
TV_STC_GUIDE = "rgba(148, 163, 184, 0.36)"


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


def _colored_line_points(df: pd.DataFrame, column: str, trend_column: str, up_color: str, down_color: str) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for row in df[["time", column, trend_column]].rename(columns={column: "value", trend_column: "trend"}).itertuples(index=False):
        value = None if pd.isna(row.value) else float(row.value)
        if value is None:
            points.append({"time": int(row.time)})
            continue
        points.append(
            {
                "time": int(row.time),
                "value": value,
                "color": up_color if bool(row.trend) else down_color,
            }
        )
    return points


def _constant_line_points(df: pd.DataFrame, value: float) -> list[dict[str, Any]]:
    return [{"time": int(row.time), "value": float(value)} for row in df[["time"]].itertuples(index=False)]


class AtrBandsIndicator(Indicator):
    meta = IndicatorMeta(
        id="atr_bands",
        name="ATR Bands",
        pane="price",
        description="基于 ATR 的上下轨，默认参数 N=14, M=2。",
        enabled_by_default=True,
        params=[
            {"key": "period", "label": "ATR周期", "type": "int", "default": 14, "min": 1, "max": 500, "step": 1},
            {"key": "multiplier", "label": "倍数", "type": "float", "default": 2, "min": 0.1, "max": 20, "step": 0.1},
            {
                "key": "basis",
                "label": "基准",
                "type": "string",
                "default": "high_low",
                "options": ["high_low", "close", "hl2", "hlc3"],
            },
        ],
    )

    def __init__(self, period: int = 14, multiplier: float = 2) -> None:
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
        params=[
            {"key": "fast", "label": "Fast", "type": "int", "default": 12, "min": 1, "max": 500, "step": 1},
            {"key": "slow", "label": "Slow", "type": "int", "default": 26, "min": 1, "max": 500, "step": 1},
            {"key": "signal", "label": "Signal", "type": "int", "default": 9, "min": 1, "max": 500, "step": 1},
        ],
    )

    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9) -> None:
        self.fast = fast
        self.slow = slow
        self.signal = signal

    def build(self, bars: pd.DataFrame, params: dict[str, Any] | None = None) -> IndicatorResult:
        resolved = self.resolve_params(params)
        fast = max(1, int(resolved.get("fast", self.fast)))
        slow = max(1, int(resolved.get("slow", self.slow)))
        signal = max(1, int(resolved.get("signal", self.signal)))

        df = bars.copy()
        ema_fast = df["close"].ewm(span=fast, adjust=False).mean()
        ema_slow = df["close"].ewm(span=slow, adjust=False).mean()
        df["diff"] = ema_fast - ema_slow
        df["dea"] = df["diff"].ewm(span=signal, adjust=False).mean()
        df["hist"] = (df["diff"] - df["dea"]) * 2
        return IndicatorResult(
            id=self.meta.id,
            name=self.meta.name,
            pane=self.meta.pane,
            series=[
                SeriesDefinition(
                    id="macd_diff",
                    name=f"DIFF({fast},{slow})",
                    pane="indicator",
                    series_type="line",
                    data=_line_points(df, "diff"),
                    options={"color": TV_ACCENT, "lineWidth": 2, "priceLineVisible": False},
                ),
                SeriesDefinition(
                    id="macd_dea",
                    name=f"DEA({signal})",
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


class StcIndicator(Indicator):
    meta = IndicatorMeta(
        id="stc",
        name="STC",
        pane="indicator",
        description="Schaff Trend Cycle，默认参数 80/27/50/0.5。",
        enabled_by_default=True,
        params=[
            {"key": "length", "label": "Length", "type": "int", "default": 80, "min": 1, "max": 500, "step": 1},
            {"key": "fast_length", "label": "FastLength", "type": "int", "default": 27, "min": 1, "max": 500, "step": 1},
            {"key": "slow_length", "label": "SlowLength", "type": "int", "default": 50, "min": 1, "max": 500, "step": 1},
            {"key": "factor", "label": "Factor", "type": "float", "default": 0.5, "min": 0.01, "max": 1, "step": 0.01},
        ],
    )

    def build(self, bars: pd.DataFrame, params: dict[str, Any] | None = None) -> IndicatorResult:
        resolved = self.resolve_params(params)
        length = max(1, int(resolved.get("length", 80)))
        fast_length = max(1, int(resolved.get("fast_length", 27)))
        slow_length = max(1, int(resolved.get("slow_length", 50)))
        factor = min(max(float(resolved.get("factor", 0.5)), 0.01), 1.0)

        df = bars.copy()
        fast_ma = df["close"].ewm(span=fast_length, adjust=False).mean()
        slow_ma = df["close"].ewm(span=slow_length, adjust=False).mean()
        macd_source = fast_ma - slow_ma
        macd_low = macd_source.rolling(length, min_periods=1).min()
        macd_range = macd_source.rolling(length, min_periods=1).max() - macd_low

        first_stochastic: list[float] = []
        smoothed_first: list[float] = []
        second_stochastic: list[float] = []
        stc_values: list[float] = []

        previous_first = 0.0
        previous_second = 0.0
        for index, macd_value in enumerate(macd_source.tolist()):
            range_value = float(macd_range.iloc[index])
            if range_value > 0:
                first_value = (float(macd_value) - float(macd_low.iloc[index])) / range_value * 100
            else:
                first_value = previous_first
            first_stochastic.append(first_value)
            previous_first = first_value

            if index == 0:
                smoothed_value = first_value
            else:
                smoothed_value = smoothed_first[-1] + factor * (first_value - smoothed_first[-1])
            smoothed_first.append(smoothed_value)

            smoothed_series = pd.Series(smoothed_first)
            smooth_low = float(smoothed_series.rolling(length, min_periods=1).min().iloc[-1])
            smooth_range = float(smoothed_series.rolling(length, min_periods=1).max().iloc[-1] - smooth_low)
            if smooth_range > 0:
                second_value = (smoothed_value - smooth_low) / smooth_range * 100
            else:
                second_value = previous_second
            second_stochastic.append(second_value)
            previous_second = second_value

            if index == 0:
                stc_value = second_value
            else:
                stc_value = stc_values[-1] + factor * (second_value - stc_values[-1])
            stc_values.append(stc_value)

        df["stc"] = stc_values
        df["stc_up"] = df["stc"] > df["stc"].shift(1)

        return IndicatorResult(
            id=self.meta.id,
            name=self.meta.name,
            pane=self.meta.pane,
            series=[
                SeriesDefinition(
                    id="stc",
                    name=f"STC({length},{fast_length},{slow_length})",
                    pane="indicator",
                    series_type="line",
                    data=_colored_line_points(df, "stc", "stc_up", TV_STC_UP, TV_STC_DOWN),
                    options={"color": TV_STC_UP, "lineWidth": 2, "priceLineVisible": False},
                ),
                SeriesDefinition(
                    id="stc_upper",
                    name="75",
                    pane="indicator",
                    series_type="line",
                    data=_constant_line_points(df, 75),
                    options={
                        "color": TV_STC_GUIDE,
                        "lineWidth": 1,
                        "priceLineVisible": False,
                        "lastValueVisible": False,
                        "fillToSeriesId": "stc_lower",
                        "fillColor": TV_STC_BAND,
                    },
                ),
                SeriesDefinition(
                    id="stc_lower",
                    name="25",
                    pane="indicator",
                    series_type="line",
                    data=_constant_line_points(df, 25),
                    options={"color": TV_STC_GUIDE, "lineWidth": 1, "priceLineVisible": False, "lastValueVisible": False},
                ),
            ],
        )


def register_builtin_indicators(registry: IndicatorRegistry) -> None:
    registry.register(AtrBandsIndicator())
    registry.register(MacdIndicator())
    registry.register(StcIndicator())
