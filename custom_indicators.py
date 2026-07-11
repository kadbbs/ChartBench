from __future__ import annotations

import numpy as np
import pandas as pd

from tq_app.indicators import Indicator
from tq_app.models import IndicatorMeta, IndicatorResult, SeriesDefinition


HULL_UP_COLOR = "#4caf50"
HULL_DOWN_COLOR = "#f23645"
HULL_UP_FILL = "rgba(76, 175, 80, 0.60)"
HULL_DOWN_FILL = "rgba(242, 54, 69, 0.60)"


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


def _trend_line_data(df: pd.DataFrame, value_column: str, trend_column: str, expected_trend: bool) -> list[dict[str, float | int]]:
    points: list[dict[str, float | int]] = []
    for row in df[["time", value_column, trend_column]].rename(columns={value_column: "value", trend_column: "trend"}).itertuples(index=False):
        if pd.isna(row.value) or bool(row.trend) != expected_trend:
            points.append({"time": int(row.time)})
            continue
        points.append({"time": int(row.time), "value": float(row.value)})
    return points


def _line_points(df: pd.DataFrame, column: str) -> list[dict[str, float | int | None]]:
    return [
        {"time": int(row.time), "value": None if pd.isna(row.value) else float(row.value)}
        for row in df[["time", column]].rename(columns={column: "value"}).itertuples(index=False)
    ]


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


def _rma(series: pd.Series, period: int) -> pd.Series:
    safe_period = max(int(period), 1)
    values = series.astype("float64").tolist()
    output: list[float] = []
    previous = np.nan
    alpha = 1.0 / safe_period
    for index, value in enumerate(values):
        if index + 1 < safe_period or pd.isna(value):
            output.append(np.nan)
            continue
        if pd.isna(previous):
            window = pd.Series(values[index + 1 - safe_period : index + 1], dtype="float64")
            previous = float(window.mean()) if not window.isna().any() else np.nan
        else:
            previous = alpha * float(value) + (1.0 - alpha) * previous
        output.append(previous)
    return pd.Series(output, index=series.index, dtype="float64")


def _crossover(left: pd.Series, right: pd.Series) -> pd.Series:
    return (left > right) & (left.shift(1) <= right.shift(1))


def _crossunder(left: pd.Series, right: pd.Series) -> pd.Series:
    return (left < right) & (left.shift(1) >= right.shift(1))


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
        description="通达信风格 HULL 多空线，绿涨红跌趋势段并标注 多 / 空 信号。",
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
                    "color": HULL_UP_COLOR,
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
                    "color": HULL_DOWN_COLOR,
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
                    data=_colored_line_data(df, "hull", "trend_up", HULL_UP_COLOR, HULL_DOWN_COLOR),
                    options={
                        "color": HULL_UP_COLOR,
                        "lineWidth": line_width,
                        "priceLineVisible": False,
                        "lastValueVisible": False,
                        "markers": markers,
                    },
                )
            ],
        )


class MergedDkxHullUtIndicator(Indicator):
    meta = IndicatorMeta(
        id="merged_dkx_hull_ut",
        name="合并主图：DKX + Hull Suite + UT Bot",
        pane="price",
        description="按 Pine v4 合并脚本复刻 DKX D/K、Hull Suite 和 UT Bot 买卖标记。",
        enabled_by_default=True,
        params=[
            {
                "key": "hull_variation",
                "label": "Hull Variation",
                "type": "string",
                "default": "Hma",
                "options": ["Hma", "Thma", "Ehma"],
            },
            {"key": "hull_length", "label": "Hull Length", "type": "int", "default": 55, "min": 1, "max": 500, "step": 1},
            {
                "key": "hull_length_mult",
                "label": "Length multiplier",
                "type": "float",
                "default": 1.0,
                "min": 0.01,
                "max": 20,
                "step": 0.1,
            },
            {"key": "color_hull", "label": "Color Hull by trend?", "type": "bool", "default": True},
            {"key": "hull_line_width", "label": "Hull Line Thickness", "type": "int", "default": 1, "min": 1, "max": 6, "step": 1},
            {"key": "ut_sensitivity", "label": "UT Sensitivity", "type": "float", "default": 2.0, "min": 0.01, "max": 100, "step": 0.1},
            {"key": "ut_atr_period", "label": "ATR Period", "type": "int", "default": 6, "min": 1, "max": 500, "step": 1},
            {"key": "ut_use_heikin_ashi", "label": "Use Heikin Ashi?", "type": "bool", "default": False},
        ],
    )

    def build(self, bars: pd.DataFrame, params: dict | None = None) -> IndicatorResult:
        resolved = self.resolve_params(params)
        hull_variation = str(resolved["hull_variation"])
        hull_length = int(resolved["hull_length"])
        hull_length_mult = float(resolved["hull_length_mult"])
        color_hull = bool(resolved["color_hull"])
        hull_line_width = int(resolved["hull_line_width"])
        ut_sensitivity = float(resolved["ut_sensitivity"])
        ut_atr_period = int(resolved["ut_atr_period"])
        ut_use_heikin_ashi = bool(resolved["ut_use_heikin_ashi"])

        df = bars.copy()

        df["dkx_w"] = (3 * df["close"] + df["high"] + df["low"] + df["open"]) / 6
        df["dkx_d"] = sum((20 - index) * df["dkx_w"].shift(index).fillna(0) for index in range(20)) / 210
        df["dkx_k"] = df["dkx_d"].rolling(10, min_periods=10).mean()
        df["dkx_buy"] = _crossover(df["dkx_d"], df["dkx_k"])
        df["dkx_sell"] = _crossunder(df["dkx_d"], df["dkx_k"])

        hull_source = df["close"]
        hull_mode_length = max(int(hull_length * hull_length_mult), 1)
        if hull_variation == "Ehma":
            df["mhull"] = _ehma(hull_source, hull_mode_length)
        elif hull_variation == "Thma":
            df["mhull"] = _thma(hull_source, max(hull_mode_length // 2, 1))
        else:
            df["mhull"] = _hma(hull_source, hull_mode_length)
        df["shull"] = df["mhull"].shift(2)
        df["hull_up"] = df["mhull"] > df["mhull"].shift(2)

        prev_close = df["close"].shift(1)
        true_range = pd.concat(
            [
                df["high"] - df["low"],
                (df["high"] - prev_close).abs(),
                (df["low"] - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        x_atr = _rma(true_range, ut_atr_period)
        n_loss = ut_sensitivity * x_atr
        src_ut = (df["open"] + df["high"] + df["low"] + df["close"]) / 4 if ut_use_heikin_ashi else df["close"]
        ema_src_ut = _ema(src_ut, 1)

        trailing_stop: list[float] = []
        position: list[int] = []
        for index, source_value in enumerate(src_ut.tolist()):
            prev_stop = trailing_stop[index - 1] if index > 0 else 0.0
            prev_source = src_ut.iloc[index - 1] if index > 0 else np.nan
            loss_value = n_loss.iloc[index]
            if pd.isna(source_value) or pd.isna(loss_value):
                current_stop = np.nan
            elif source_value > (0.0 if pd.isna(prev_stop) else prev_stop) and prev_source > (0.0 if pd.isna(prev_stop) else prev_stop):
                current_stop = max(0.0 if pd.isna(prev_stop) else prev_stop, float(source_value - loss_value))
            elif source_value < (0.0 if pd.isna(prev_stop) else prev_stop) and prev_source < (0.0 if pd.isna(prev_stop) else prev_stop):
                current_stop = min(0.0 if pd.isna(prev_stop) else prev_stop, float(source_value + loss_value))
            elif source_value > (0.0 if pd.isna(prev_stop) else prev_stop):
                current_stop = float(source_value - loss_value)
            else:
                current_stop = float(source_value + loss_value)
            trailing_stop.append(current_stop)

            prev_position = position[index - 1] if index > 0 else 0
            if index == 0 or pd.isna(current_stop) or pd.isna(prev_stop):
                current_position = prev_position
            elif prev_source < prev_stop and source_value > prev_stop:
                current_position = 1
            elif prev_source > prev_stop and source_value < prev_stop:
                current_position = -1
            else:
                current_position = prev_position
            position.append(current_position)

        df["ut_trailing_stop"] = trailing_stop
        df["ut_buy"] = (src_ut > df["ut_trailing_stop"]) & _crossover(ema_src_ut, df["ut_trailing_stop"])
        df["ut_sell"] = (src_ut < df["ut_trailing_stop"]) & _crossover(df["ut_trailing_stop"], ema_src_ut)

        markers: list[dict[str, str | int]] = []
        markers.extend(
            {
                "time": int(row.time),
                "position": "belowBar",
                "color": "#4caf50",
                "shape": "square",
                "size": 1,
                "text": "买",
            }
            for row in df.loc[df["dkx_buy"], ["time"]].itertuples(index=False)
        )
        markers.extend(
            {
                "time": int(row.time),
                "position": "aboveBar",
                "color": "#f23645",
                "shape": "square",
                "size": 1,
                "text": "卖",
            }
            for row in df.loc[df["dkx_sell"], ["time"]].itertuples(index=False)
        )
        markers.extend(
            {
                "time": int(row.time),
                "position": "belowBar",
                "color": "#4caf50",
                "shape": "square",
                "size": 1,
                "text": "Buy",
            }
            for row in df.loc[df["ut_buy"], ["time"]].itertuples(index=False)
        )
        markers.extend(
            {
                "time": int(row.time),
                "position": "aboveBar",
                "color": "#f23645",
                "shape": "square",
                "size": 1,
                "text": "Sell",
            }
            for row in df.loc[df["ut_sell"], ["time"]].itertuples(index=False)
        )
        markers.sort(key=lambda item: int(item["time"]))
        bar_colors = [
            {"time": int(row.time), "color": "#4caf50"}
            for row in df.loc[df["ut_buy"], ["time"]].itertuples(index=False)
        ]
        bar_colors.extend(
            {"time": int(row.time), "color": "#f23645"}
            for row in df.loc[df["ut_sell"], ["time"]].itertuples(index=False)
        )

        hull_up_color = HULL_UP_COLOR if color_hull else "#ff9800"
        hull_down_color = HULL_DOWN_COLOR if color_hull else "#ff9800"

        return IndicatorResult(
            id=self.meta.id,
            name=self.meta.name,
            pane=self.meta.pane,
            series=[
                SeriesDefinition(
                    id="dkx_d",
                    name="DKX D线",
                    pane="price",
                    series_type="line",
                    data=_line_points(df, "dkx_d"),
                    options={
                        "color": "#ff9800",
                        "lineWidth": 2,
                        "priceLineVisible": False,
                        "lastValueVisible": False,
                        "candleMarkers": markers,
                        "barColors": bar_colors,
                    },
                ),
                SeriesDefinition(
                    id="dkx_k",
                    name="DKX K线",
                    pane="price",
                    series_type="line",
                    data=_line_points(df, "dkx_k"),
                    options={"color": "#2196f3", "lineWidth": 2, "priceLineVisible": False, "lastValueVisible": False},
                ),
                SeriesDefinition(
                    id="mhull_up",
                    name="MHULL 上升",
                    pane="price",
                    series_type="line",
                    data=_trend_line_data(df, "mhull", "hull_up", True),
                    options={
                        "color": hull_up_color,
                        "lineWidth": hull_line_width,
                        "priceLineVisible": False,
                        "lastValueVisible": False,
                        "fillToSeriesId": "shull_up",
                        "fillColor": HULL_UP_FILL if color_hull else "rgba(255, 152, 0, 0.60)",
                    },
                ),
                SeriesDefinition(
                    id="shull_up",
                    name="SHULL 上升",
                    pane="price",
                    series_type="line",
                    data=_trend_line_data(df, "shull", "hull_up", True),
                    options={"color": hull_up_color, "lineWidth": hull_line_width, "priceLineVisible": False, "lastValueVisible": False},
                ),
                SeriesDefinition(
                    id="mhull_down",
                    name="MHULL 下降",
                    pane="price",
                    series_type="line",
                    data=_trend_line_data(df, "mhull", "hull_up", False),
                    options={
                        "color": hull_down_color,
                        "lineWidth": hull_line_width,
                        "priceLineVisible": False,
                        "lastValueVisible": False,
                        "fillToSeriesId": "shull_down",
                        "fillColor": HULL_DOWN_FILL if color_hull else "rgba(255, 152, 0, 0.60)",
                    },
                ),
                SeriesDefinition(
                    id="shull_down",
                    name="SHULL 下降",
                    pane="price",
                    series_type="line",
                    data=_trend_line_data(df, "shull", "hull_up", False),
                    options={"color": hull_down_color, "lineWidth": hull_line_width, "priceLineVisible": False, "lastValueVisible": False},
                ),
            ],
        )


def register_indicators(registry) -> None:
    registry.register(DuoKongLineIndicator())
    registry.register(MergedDkxHullUtIndicator())
