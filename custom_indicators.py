from __future__ import annotations

import numpy as np
import pandas as pd

from tq_app.indicators import Indicator
from tq_app.models import IndicatorMeta, IndicatorResult, SeriesDefinition


def _line_point(time_value: int, value: float | None) -> dict[str, float | int]:
    if value is None or pd.isna(value):
        return {"time": int(time_value)}
    return {"time": int(time_value), "value": float(value)}


def _colored_line_data(
    df: pd.DataFrame,
    value_column: str,
    trend_column: str,
    up_color: str,
    down_color: str,
) -> list[dict[str, float | int | str]]:
    points: list[dict[str, float | int | str]] = []
    times = pd.to_numeric(df["time"], errors="coerce").fillna(0).astype(int).tolist()
    values = df[value_column].tolist()
    trends = df[trend_column].tolist()

    for time_value, value, trend in zip(times, values, trends):
        if pd.isna(value):
            points.append({"time": int(time_value)})
            continue
        points.append(
            {
                "time": int(time_value),
                "value": float(value),
                "color": up_color if bool(trend) else down_color,
            }
        )

    return points


def _wma(series: pd.Series, period: int) -> pd.Series:
    safe_period = max(int(period), 1)
    weights = np.arange(1, safe_period + 1, dtype="float64")
    return series.rolling(safe_period).apply(
        lambda values: float(np.dot(values, weights) / weights.sum()),
        raw=True,
    )


def _ema(series: pd.Series, period: int) -> pd.Series:
    safe_period = max(int(period), 1)
    return series.ewm(span=safe_period, adjust=False).mean()


def _hma(series: pd.Series, period: int) -> pd.Series:
    safe_period = max(int(period), 1)
    half_length = max(safe_period // 2, 1)
    sqrt_length = max(int(safe_period**0.5), 1)
    base = 2 * _wma(series, half_length) - _wma(series, safe_period)
    return _wma(base, sqrt_length)


def _ehma(series: pd.Series, period: int) -> pd.Series:
    safe_period = max(int(period), 1)
    half_length = max(safe_period // 2, 1)
    sqrt_length = max(int(safe_period**0.5), 1)
    base = 2 * _ema(series, half_length) - _ema(series, safe_period)
    return _ema(base, sqrt_length)


def _thma(series: pd.Series, period: int) -> pd.Series:
    safe_period = max(int(period), 1)
    third_length = max(safe_period // 3, 1)
    half_length = max(safe_period // 2, 1)
    return _wma(3 * _wma(series, third_length) - _wma(series, half_length) - _wma(series, safe_period), safe_period)


def _source_series(df: pd.DataFrame, source: str) -> pd.Series:
    source_key = (source or "close").lower()
    if source_key in df.columns:
        return df[source_key]
    if source_key == "hl2":
        return (df["high"] + df["low"]) / 2
    if source_key == "hlc3":
        return (df["high"] + df["low"] + df["close"]) / 3
    if source_key == "ohlc4":
        return (df["open"] + df["high"] + df["low"] + df["close"]) / 4
    return df["close"]


class DuoKongLineIndicator(Indicator):
    meta = IndicatorMeta(
        id="duo_kong_line",
        name="多空线",
        pane="price",
        description="通达信风格 HULL 多空线，红绿趋势段并标注 多 / 空 信号。",
        enabled_by_default=True,
        params=[
            {
                "key": "mode",
                "label": "模式",
                "type": "int",
                "default": 1,
                "min": 1,
                "max": 3,
                "step": 1,
                "options": [1, 2, 3],
            },
            {"key": "length", "label": "周期", "type": "int", "default": 55, "min": 2, "step": 1},
            {
                "key": "source",
                "label": "价格源",
                "type": "string",
                "default": "close",
                "options": ["close", "open", "high", "low", "hl2", "hlc3", "ohlc4"],
            },
            {"key": "line_width", "label": "线宽", "type": "int", "default": 3, "min": 1, "max": 6, "step": 1},
            {"key": "show_signals", "label": "显示信号", "type": "bool", "default": True},
        ],
    )

    def build(self, bars: pd.DataFrame, params: dict | None = None) -> IndicatorResult:
        resolved = self.resolve_params(params)
        mode = resolved["mode"]
        length = resolved["length"]
        source = resolved["source"]
        line_width = resolved["line_width"]
        show_signals = resolved["show_signals"]

        df = bars.copy()
        hull_source = _source_series(df, source)
        if mode == 2:
            df["hull"] = _ehma(hull_source, length)
        elif mode == 3:
            df["hull"] = _thma(hull_source, length)
        else:
            df["hull"] = _hma(hull_source, length)

        previous_hull = df["hull"].shift(1)
        slope = df["hull"] - previous_hull
        df["trend_up"] = df["hull"] >= previous_hull
        df["buy_signal"] = (slope > 0) & (slope.shift(1) <= 0)
        df["sell_signal"] = (slope < 0) & (slope.shift(1) >= 0)

        markers: list[dict[str, str | int]] = []
        if show_signals:
            markers.extend(
                {
                    "time": int(row.time),
                    "position": "belowBar",
                    "color": "#ff4d4f",
                    "shape": "circle",
                    "size": 1,
                    "text": "多",
                }
                for row in df.loc[df["buy_signal"], ["time"]].itertuples(index=False)
            )
            markers.extend(
                {
                    "time": int(row.time),
                    "position": "aboveBar",
                    "color": "#00a86b",
                    "shape": "circle",
                    "size": 1,
                    "text": "空",
                }
                for row in df.loc[df["sell_signal"], ["time"]].itertuples(index=False)
            )

        return IndicatorResult(
            id=self.meta.id,
            name=self.meta.name,
            pane=self.meta.pane,
            series=[
                SeriesDefinition(
                    id="duo_kong_line",
                    name=f"多空线({length})",
                    pane="price",
                    series_type="line",
                    data=_colored_line_data(df, "hull", "trend_up", "#e53935", "#00c853"),
                    options={
                        "color": "#e53935",
                        "lineWidth": line_width,
                        "priceLineVisible": False,
                        "lastValueVisible": False,
                        "markers": markers,
                    },
                )
            ],
        )


def register_indicators(registry) -> None:
    registry.register(DuoKongLineIndicator())
