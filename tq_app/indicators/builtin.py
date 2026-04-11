from __future__ import annotations

from typing import Any

import pandas as pd

from orderflow import build_spqrc_signal_frame
from tq_app.models import IndicatorMeta, IndicatorResult, SeriesDefinition

from .base import Indicator, IndicatorRegistry

TV_UP = "#089981"
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
        color = TV_DOWN if (value or 0) >= 0 else TV_UP
        points.append({"time": int(row.time), "value": value, "color": color})
    return points


def _rgba(hex_color: str, alpha: float) -> str:
    color = hex_color.lstrip("#")
    if len(color) != 6:
        return hex_color
    red = int(color[0:2], 16)
    green = int(color[2:4], 16)
    blue = int(color[4:6], 16)
    return f"rgba({red}, {green}, {blue}, {alpha:.3f})"


def _probability_heat_points(df: pd.DataFrame, column: str, level: float, color: str) -> list[dict[str, Any]]:
    values = pd.to_numeric(df[column], errors="coerce")
    times = df["time"].astype(int).tolist()
    points: list[dict[str, Any]] = []
    for time_value, raw_value in zip(times, values.tolist()):
        if raw_value is None or pd.isna(raw_value):
            points.append({"time": time_value})
            continue
        probability = float(max(0.0, min(1.0, raw_value)))
        points.append(
            {
                "time": time_value,
                "value": level + probability * 0.82,
                "color": _rgba(color, 0.18 + probability * 0.72),
            }
        )
    return points


class AtrBandsIndicator(Indicator):
    meta = IndicatorMeta(
        id="atr_bands",
        name="ATR Bands",
        pane="price",
        description="基于 ATR 的上下轨，默认参数 N=14, M=1.5。",
        enabled_by_default=True,
    )

    def __init__(self, period: int = 14, multiplier: float = 1.5) -> None:
        self.period = period
        self.multiplier = multiplier

    def build(self, bars: pd.DataFrame, params: dict[str, Any] | None = None) -> IndicatorResult:
        df = bars.copy()
        prev_close = df["close"].shift(1)
        tr1 = df["high"] - df["low"]
        tr2 = (df["high"] - prev_close).abs()
        tr3 = (df["low"] - prev_close).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(self.period).mean()
        df["upper"] = df["high"] + atr * self.multiplier
        df["lower"] = df["low"] - atr * self.multiplier
        return IndicatorResult(
            id=self.meta.id,
            name=self.meta.name,
            pane=self.meta.pane,
            series=[
                SeriesDefinition(
                    id="atr_bands_upper",
                    name=f"Upper({self.period},{self.multiplier})",
                    pane="price",
                    series_type="line",
                    data=_line_points(df.assign(time=df["time"]), "upper"),
                    options={"color": TV_UPPER, "lineWidth": 1, "lastValueVisible": False, "priceLineVisible": False},
                ),
                SeriesDefinition(
                    id="atr_bands_lower",
                    name=f"Lower({self.period},{self.multiplier})",
                    pane="price",
                    series_type="line",
                    data=_line_points(df.assign(time=df["time"]), "lower"),
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
        enabled_by_default=False,
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
                    data=_line_points(df.assign(time=df["time"]), "diff"),
                    options={"color": TV_ACCENT, "lineWidth": 2, "priceLineVisible": False},
                ),
                SeriesDefinition(
                    id="macd_dea",
                    name="DEA",
                    pane="indicator",
                    series_type="line",
                    data=_line_points(df.assign(time=df["time"]), "dea"),
                    options={"color": TV_SIGNAL, "lineWidth": 2, "priceLineVisible": False},
                ),
                SeriesDefinition(
                    id="macd_hist",
                    name="Histogram",
                    pane="indicator",
                    series_type="histogram",
                    data=_histogram_points(df.assign(time=df["time"]), "hist"),
                    options={"base": 0, "priceLineVisible": False},
                ),
            ],
        )


class SmaIndicator(Indicator):
    meta = IndicatorMeta(
        id="sma20",
        name="SMA 20",
        pane="price",
        description="20 周期简单均线，作为自定义指标示例的参考。",
        enabled_by_default=False,
    )

    def __init__(self, period: int = 20) -> None:
        self.period = period

    def build(self, bars: pd.DataFrame, params: dict[str, Any] | None = None) -> IndicatorResult:
        df = bars.copy()
        df["sma"] = df["close"].rolling(self.period).mean()
        return IndicatorResult(
            id=self.meta.id,
            name=self.meta.name,
            pane=self.meta.pane,
            series=[
                SeriesDefinition(
                    id="sma20_line",
                    name=f"SMA({self.period})",
                    pane="price",
                    series_type="line",
                    data=_line_points(df.assign(time=df["time"]), "sma"),
                    options={"color": "#f5c542", "lineWidth": 2, "priceLineVisible": False},
                )
            ],
        )


class PseudoOrderflow5mIndicator(Indicator):
    meta = IndicatorMeta(
        id="pseudo_orderflow_5m",
        name="5分钟仿订单流",
        pane="indicator",
        description="基于 5 分钟 bar 内部特征的仿订单流状态图，主要展示 5 个二值条件。",
        enabled_by_default=False,
    )

    def build(self, bars: pd.DataFrame, params: dict[str, Any] | None = None) -> IndicatorResult:
        df = bars.copy()
        flag_specs = [
            ("delta_positive_flag_5m", "Delta>0", 5.0),
            ("delta_ratio_above_mean20_flag_5m", "DeltaRatio>均值20", 4.0),
            ("doi_positive_flag_5m", "dOI>0", 3.0),
            ("imbalance_positive_flag_5m", "盘口尾值>0", 2.0),
            ("efficiency_above_median20_flag_5m", "Efficiency>中位20", 1.0),
        ]

        for column, _, _ in flag_specs:
            if column not in df.columns:
                df[column] = pd.NA

        def build_flag_points(column: str, level: float) -> list[dict[str, Any]]:
            times = df["time"].astype(int).tolist()
            values = pd.to_numeric(df[column], errors="coerce")
            valid_mask = values.notna().tolist()
            active_mask = values.fillna(0.0).gt(0.5).tolist()
            points: list[dict[str, Any]] = []
            for time_value, is_valid, is_active in zip(times, valid_mask, active_mask):
                if not is_valid:
                    points.append({"time": time_value})
                    continue
                points.append(
                    {
                        "time": time_value,
                        "value": level + 0.32 if is_active else level - 0.32,
                        "color": TV_UP if is_active else "#b0b0b0",
                    }
                )
            return points

        return IndicatorResult(
            id=self.meta.id,
            name=self.meta.name,
            pane=self.meta.pane,
            series=[
                SeriesDefinition(
                    id=f"pseudo_orderflow_5m_{column}",
                    name=name,
                    pane="indicator",
                    series_type="histogram",
                    data=build_flag_points(column, level),
                    options={"base": level, "priceLineVisible": False, "lastValueVisible": False},
                )
                for column, name, level in flag_specs
            ],
        )


class SPQRCSignalsIndicator(Indicator):
    meta = IndicatorMeta(
        id="spqrc_signals",
        name="SPQRC 信号",
        pane="price",
        description="基于路径几何、盘口压力、在线区间和粗糙度过滤的主图信号标记。",
        enabled_by_default=False,
        params=[
            {"key": "entry_threshold", "label": "推进阈值", "type": "float", "default": 0.55, "min": 0.1, "max": 0.95, "step": 0.01},
            {"key": "fade_threshold", "label": "假突破阈值", "type": "float", "default": 0.60, "min": 0.1, "max": 0.95, "step": 0.01},
            {"key": "roughness_max", "label": "粗糙度上限", "type": "float", "default": 0.60, "min": 0.1, "max": 1.0, "step": 0.01},
            {"key": "noise_max", "label": "噪声上限", "type": "float", "default": 0.35, "min": 0.05, "max": 1.0, "step": 0.01},
            {"key": "cost_bps", "label": "成本基点", "type": "float", "default": 3.0, "min": 0.0, "step": 0.1},
        ],
    )

    def build(self, bars: pd.DataFrame, params: dict[str, Any] | None = None) -> IndicatorResult:
        df = build_spqrc_signal_frame(bars, self.resolve_params(params))
        markers: list[dict[str, str | int]] = []
        using_model = bool(pd.to_numeric(df.get("model_mode"), errors="coerce").fillna(0.0).max() > 0.5)
        push_long_text = "模多" if using_model else "规多"
        push_short_text = "模空" if using_model else "规空"
        fade_short_text = "模假多" if using_model else "规假多"
        fade_long_text = "模假空" if using_model else "规假空"
        markers.extend(
            {
                "time": int(row.time),
                "position": "belowBar",
                "color": "#00c853",
                "shape": "circle",
                "size": 1,
                "text": push_long_text,
            }
            for row in df.loc[df["long_signal"], ["time"]].itertuples(index=False)
        )
        markers.extend(
            {
                "time": int(row.time),
                "position": "aboveBar",
                "color": "#f23645",
                "shape": "circle",
                "size": 1,
                "text": push_short_text,
            }
            for row in df.loc[df["short_signal"], ["time"]].itertuples(index=False)
        )
        markers.extend(
            {
                "time": int(row.time),
                "position": "aboveBar",
                "color": "#ff9800",
                "shape": "square",
                "size": 1,
                "text": fade_short_text,
            }
            for row in df.loc[df["fade_short_signal"], ["time"]].itertuples(index=False)
        )
        markers.extend(
            {
                "time": int(row.time),
                "position": "belowBar",
                "color": "#2962ff",
                "shape": "square",
                "size": 1,
                "text": fade_long_text,
            }
            for row in df.loc[df["fade_long_signal"], ["time"]].itertuples(index=False)
        )

        return IndicatorResult(
            id=self.meta.id,
            name=self.meta.name,
            pane=self.meta.pane,
            series=[
                SeriesDefinition(
                    id="spqrc_signal_anchor",
                    name="SPQRC 信号锚点",
                    pane="price",
                    series_type="line",
                    data=_line_points(df.assign(time=df["time"]), "close"),
                    options={
                        "color": "rgba(0,0,0,0)",
                        "lineWidth": 1,
                        "priceLineVisible": False,
                        "lastValueVisible": False,
                        "markers": markers,
                    },
                )
            ],
        )


class SPQRCPanelIndicator(Indicator):
    meta = IndicatorMeta(
        id="spqrc_panel",
        name="SPQRC 面板",
        pane="indicator",
        description="展示 5 个状态概率、粗糙度、区间边际和当前是模型驱动还是规则回退。",
        enabled_by_default=False,
        params=SPQRCSignalsIndicator.meta.params,
    )

    def build(self, bars: pd.DataFrame, params: dict[str, Any] | None = None) -> IndicatorResult:
        df = build_spqrc_signal_frame(bars, self.resolve_params(params))
        return IndicatorResult(
            id=self.meta.id,
            name=self.meta.name,
            pane=self.meta.pane,
            series=[
                SeriesDefinition(
                    id="spqrc_push_up_prob",
                    name="PushUp 概率",
                    pane="indicator",
                    series_type="histogram",
                    data=_probability_heat_points(df.assign(time=df["time"]), "push_up_prob", 9.0, "#00c853"),
                    options={"base": 9.0, "priceLineVisible": False, "lastValueVisible": False},
                ),
                SeriesDefinition(
                    id="spqrc_push_down_prob",
                    name="PushDown 概率",
                    pane="indicator",
                    series_type="histogram",
                    data=_probability_heat_points(df.assign(time=df["time"]), "push_down_prob", 8.0, "#f23645"),
                    options={"base": 8.0, "priceLineVisible": False, "lastValueVisible": False},
                ),
                SeriesDefinition(
                    id="spqrc_fade_up_prob",
                    name="FadeUp 概率",
                    pane="indicator",
                    series_type="histogram",
                    data=_probability_heat_points(df.assign(time=df["time"]), "fade_up_prob", 7.0, "#ff9800"),
                    options={"base": 7.0, "priceLineVisible": False, "lastValueVisible": False},
                ),
                SeriesDefinition(
                    id="spqrc_fade_down_prob",
                    name="FadeDown 概率",
                    pane="indicator",
                    series_type="histogram",
                    data=_probability_heat_points(df.assign(time=df["time"]), "fade_down_prob", 6.0, "#2962ff"),
                    options={"base": 6.0, "priceLineVisible": False, "lastValueVisible": False},
                ),
                SeriesDefinition(
                    id="spqrc_noise_prob",
                    name="Noise 概率",
                    pane="indicator",
                    series_type="histogram",
                    data=_probability_heat_points(df.assign(time=df["time"]), "noise_prob", 5.0, "#888888"),
                    options={"base": 5.0, "priceLineVisible": False, "lastValueVisible": False},
                ),
                SeriesDefinition(
                    id="spqrc_roughness_score",
                    name="粗糙度",
                    pane="indicator",
                    series_type="line",
                    data=_line_points(df.assign(time=df["time"], roughness_plot=4.0 + pd.to_numeric(df["roughness_score"], errors="coerce") * 0.8), "roughness_plot"),
                    options={"color": "#7a5cff", "lineWidth": 2, "priceLineVisible": False, "lastValueVisible": False},
                ),
                SeriesDefinition(
                    id="spqrc_edge_score",
                    name="区间边际",
                    pane="indicator",
                    series_type="line",
                    data=_line_points(df.assign(time=df["time"], edge_plot=3.0 + pd.to_numeric(df["edge_score"], errors="coerce") * 0.7), "edge_plot"),
                    options={"color": "#00c076", "lineWidth": 2, "priceLineVisible": False, "lastValueVisible": False},
                ),
                SeriesDefinition(
                    id="spqrc_state_signal",
                    name="最终状态",
                    pane="indicator",
                    series_type="line",
                    data=_line_points(df.assign(time=df["time"], state_plot=2.0 + pd.to_numeric(df["state_signal"], errors="coerce") * 0.6), "state_plot"),
                    options={"color": "#444444", "lineWidth": 2, "priceLineVisible": False, "lastValueVisible": False},
                ),
                SeriesDefinition(
                    id="spqrc_model_mode",
                    name="模型模式(1=模型/0=回退)",
                    pane="indicator",
                    series_type="line",
                    data=_line_points(df.assign(time=df["time"], model_mode_plot=1.0 + pd.to_numeric(df["model_mode"], errors="coerce") * 0.6), "model_mode_plot"),
                    options={"color": "#111111", "lineWidth": 1, "lineStyle": 2, "priceLineVisible": False, "lastValueVisible": False},
                ),
            ],
        )


def register_builtin_indicators(registry: IndicatorRegistry) -> None:
    registry.register(AtrBandsIndicator())
    registry.register(MacdIndicator())
    registry.register(SmaIndicator())
    registry.register(PseudoOrderflow5mIndicator())
    registry.register(SPQRCSignalsIndicator())
    registry.register(SPQRCPanelIndicator())
