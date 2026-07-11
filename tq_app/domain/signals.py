from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import time
from typing import Any
from zoneinfo import ZoneInfo

from .models import TradeDecision
from .strategies import (
    StrategyContext,
    StrategyRegistry,
    get_strategy_registry,
    hull_band_values,
    is_green_color,
    is_red_color,
)


DISPLAY_TIMEZONE = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True, slots=True)
class SignalConfig:
    strategy: str = "stc_extreme_contrarian"
    signal_mode: str = "any"
    use_closed_bar: bool = True
    htf_hull_filter_enabled: bool = True
    htf_hull_duration_seconds: int = 3600
    atr_period: int = 14

    @classmethod
    def from_object(cls, config: object) -> "SignalConfig":
        return cls(
            strategy=str(getattr(config, "strategy", "stc_extreme_contrarian")),
            signal_mode=str(getattr(config, "signal_mode", "any")),
            use_closed_bar=bool(getattr(config, "use_closed_bar", True)),
            htf_hull_filter_enabled=bool(
                getattr(config, "htf_hull_filter_enabled", True)
            ),
            htf_hull_duration_seconds=int(
                getattr(config, "htf_hull_duration_seconds", 3600)
            ),
            atr_period=int(getattr(config, "atr_period", 14)),
        )


class SignalEvaluator:
    """Pure signal evaluator shared by live trading and backtests."""

    def __init__(self, config: SignalConfig, registry: StrategyRegistry | None = None) -> None:
        self.config = config
        self.registry = registry or get_strategy_registry()
        self.strategy = self.registry.create(config.strategy)
        self.primary_htf_duration_seconds = int(
            getattr(self.strategy, "primary_htf_duration_seconds", 0)
            or config.htf_hull_duration_seconds
        )
        reentry_duration = int(
            getattr(self.strategy, "reentry_confirmation_duration_seconds", 0) or 0
        )
        self.reentry_confirmation_duration_seconds = reentry_duration or None

    def evaluate(self, snapshot: dict[str, Any]) -> TradeDecision:
        candles = snapshot.get("candles") or []
        symbol = str(snapshot.get("symbol") or "").upper()
        if not candles:
            return TradeDecision(action="skip", symbol=symbol, side=None, bar_time=None, reason="snapshot 中没有 K 线")

        target_index = -2 if self.config.use_closed_bar and len(candles) >= 2 else -1
        target_candle = candles[target_index]
        bar_time = int(target_candle["time"])
        marker_texts = marker_texts_at(snapshot, bar_time)
        indicator_values, indicator_colors = indicator_context_at(snapshot, bar_time)
        last_close = float(target_candle.get("close") or snapshot.get("last_close") or 0)
        bar_open = optional_float(target_candle.get("open"))
        bar_high = optional_float(target_candle.get("high"))
        bar_low = optional_float(target_candle.get("low"))
        bar_close = optional_float(target_candle.get("close"))
        strategy_result = self.strategy.evaluate(
            StrategyContext(
                marker_texts=tuple(marker_texts),
                indicator_values=indicator_values,
                indicator_colors=indicator_colors,
                signal_mode=self.config.signal_mode,
                bar_high=bar_high,
                bar_low=bar_low,
            )
        )
        side = strategy_result.side
        reason = strategy_result.reason
        htf_lock_key: str | None = None
        htf_context: dict[str, Any] = {}
        htf_reentry_allowed = False
        htf_reentry_context: dict[str, Any] = {}
        if side is not None:
            htf_ok, htf_reason, htf_context = self._higher_timeframe_hull_allows_side(
                side, snapshot.get("higher_timeframe"), symbol=symbol
            )
            if not htf_ok:
                side = None
                reason = htf_reason
            else:
                htf_lock_key = str(htf_context.get("lock_key") or "") or None
                reason = f"{reason}；{htf_reason}"
                if self.reentry_confirmation_duration_seconds is not None:
                    htf_reentry_allowed, htf_reentry_context = self._reentry_hull_allows_side(
                        side,
                        snapshot.get("reentry_higher_timeframe"),
                    )
        decision_values = {
            "action": "place_order" if side is not None else "skip",
            "symbol": symbol,
            "side": side,
            "bar_time": bar_time,
            "marker_texts": marker_texts,
            "indicator_values": indicator_values,
            "indicator_colors": indicator_colors,
            "reason": reason,
            "last_close": last_close,
            "bar_open": bar_open,
            "bar_high": bar_high,
            "bar_low": bar_low,
            "bar_close": bar_close,
            "bar_time_label": bar_time_label(snapshot, bar_time),
            "atr_value": atr_at(snapshot, bar_time, self.config.atr_period),
            "htf_reentry_allowed": htf_reentry_allowed,
            "htf_reentry_context": htf_reentry_context,
        }
        if side is None:
            return TradeDecision(**decision_values)
        return TradeDecision(
            **decision_values,
            client_oid=client_oid(symbol, side, bar_time),
            htf_lock_key=htf_lock_key,
            htf_context=htf_context,
        )

    def _higher_timeframe_hull_allows_side(
        self,
        side: str,
        htf_snapshot: Any,
        *,
        symbol: str,
    ) -> tuple[bool, str, dict[str, Any]]:
        configured_label = duration_label(self.primary_htf_duration_seconds)
        if not self.config.htf_hull_filter_enabled:
            return True, f"{configured_label} Hull 趋势过滤未启用。", {}
        if not isinstance(htf_snapshot, dict):
            return False, f"缺少高周期 Hull 快照，无法确认 {configured_label} 趋势，禁止开仓。", {}

        duration = int(htf_snapshot.get("duration_seconds") or self.primary_htf_duration_seconds)
        if duration != self.primary_htf_duration_seconds:
            return False, (
                f"高周期 Hull 快照周期错误：策略需要 {configured_label}，"
                f"实际为 {duration_label(duration)}，禁止开仓。"
            ), {}
        trend, detail = self._higher_timeframe_hull_trend(htf_snapshot)
        label = detail.get("bar_time_label") or detail.get("bar_time") or "-"
        context = {
            "symbol": symbol.upper(),
            "side": side,
            "duration_seconds": duration,
            "bar_time": detail.get("bar_time"),
            "bar_time_label": detail.get("bar_time_label"),
            "trend_start_time": detail.get("trend_start_time"),
            "trend_start_time_label": detail.get("trend_start_time_label"),
            "trend": trend,
        }
        if detail.get("trend_start_time") is not None:
            context["lock_key"] = htf_entry_lock_key(symbol, side, duration, int(detail["trend_start_time"]))
        duration_text = duration_label(duration)
        if trend == "buy":
            if side == "sell":
                return False, f"{duration_text} Hull 为绿色多趋势，禁止 5m 反向开空；{duration_text}={label}", context
            return True, f"{duration_text} Hull 为绿色多趋势，允许顺势开多；{duration_text}={label}", context
        if trend == "sell":
            if side == "buy":
                return False, f"{duration_text} Hull 为红色空趋势，禁止 5m 反向开多；{duration_text}={label}", context
            return True, f"{duration_text} Hull 为红色空趋势，允许顺势开空；{duration_text}={label}", context
        return False, f"高周期 Hull 趋势不明确，禁止开仓：{detail.get('reason') or detail}", context

    def _reentry_hull_allows_side(
        self,
        side: str,
        htf_snapshot: Any,
    ) -> tuple[bool, dict[str, Any]]:
        duration = int(self.reentry_confirmation_duration_seconds or 0)
        context: dict[str, Any] = {
            "required_duration_seconds": duration,
            "required_side": side,
            "allowed": False,
        }
        if not isinstance(htf_snapshot, dict):
            context["reason"] = f"缺少 {duration_label(duration)} Hull 快照"
            return False, context
        actual_duration = int(htf_snapshot.get("duration_seconds") or 0)
        context["duration_seconds"] = actual_duration
        if actual_duration != duration:
            context["reason"] = (
                f"重复开仓确认周期错误：需要 {duration_label(duration)}，"
                f"实际为 {duration_label(actual_duration)}"
            )
            return False, context
        trend, detail = self._higher_timeframe_hull_trend(htf_snapshot)
        failure_reason = str(detail.get("reason") or "")
        context.update(detail)
        context["trend"] = trend
        context["allowed"] = trend == side
        if trend == side:
            context["reason"] = f"{duration_label(duration)} Hull/STC 与 {side} 同向，允许重复开仓"
            return True, context
        context["reason"] = failure_reason or (
            f"{duration_label(duration)} Hull/STC 未与 {side} 同向，禁止在同一 1D Hull 阶段重复开仓"
        )
        return False, context

    def _higher_timeframe_hull_trend(self, snapshot: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
        hull_trend, detail = self._higher_timeframe_hull_direction(snapshot)
        if hull_trend is None:
            return None, detail
        stc_color = str(detail.get("stc_color") or "")
        stc_trend = "buy" if is_green_color(stc_color) else "sell" if is_red_color(stc_color) else None
        detail["stc_trend"] = stc_trend
        if stc_trend != hull_trend:
            text = duration_label(int(snapshot.get("duration_seconds") or self.primary_htf_duration_seconds))
            direction_text = "绿色上升" if hull_trend == "buy" else "红色下降"
            detail["reason"] = f"{text} Hull 为{direction_text}趋势，但 {text} STC 不是同向色"
            return None, detail
        return hull_trend, detail

    def _higher_timeframe_hull_direction(self, snapshot: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
        candles = snapshot.get("candles") or []
        if not candles:
            return None, {"reason": "高周期快照没有 K 线"}
        target_index = -2 if self.config.use_closed_bar and len(candles) >= 2 else -1
        actual_index = len(candles) + target_index if target_index < 0 else target_index
        bar_time = int(candles[target_index].get("time") or 0)
        values, colors = indicator_context_at(snapshot, bar_time)
        red_band = hull_band_values(values, "buy")
        green_band = hull_band_values(values, "sell")
        stc_color = colors.get("stc.stc", "")
        detail = {
            "bar_time": bar_time,
            "bar_time_label": bar_time_label(snapshot, bar_time),
            "red_band": red_band,
            "green_band": green_band,
            "stc_color": stc_color,
        }
        if red_band and not green_band:
            self._attach_hull_trend_start(snapshot, candles, actual_index, "buy", detail)
            return "buy", detail
        if green_band and not red_band:
            self._attach_hull_trend_start(snapshot, candles, actual_index, "sell", detail)
            return "sell", detail
        detail["reason"] = "绿色多趋势带/红色空趋势带状态为空或同时存在"
        return None, detail

    def _attach_hull_trend_start(
        self,
        snapshot: dict[str, Any],
        candles: list[dict[str, Any]],
        target_index: int,
        trend: str,
        detail: dict[str, Any],
    ) -> None:
        start_index = max(min(target_index, len(candles) - 1), 0)
        for index in range(start_index - 1, -1, -1):
            candle_time = int(candles[index].get("time") or 0)
            values, _colors = indicator_context_at(snapshot, candle_time)
            red_band = hull_band_values(values, "buy")
            green_band = hull_band_values(values, "sell")
            candle_trend = "buy" if red_band and not green_band else "sell" if green_band and not red_band else None
            if candle_trend != trend:
                break
            start_index = index
        start_time = int(candles[start_index].get("time") or 0)
        detail["trend_start_time"] = start_time
        detail["trend_start_time_label"] = bar_time_label(snapshot, start_time)


def marker_texts_at(snapshot: dict[str, Any], bar_time: int) -> list[str]:
    texts: list[str] = []
    for indicator in snapshot.get("indicators") or []:
        for series in indicator.get("series") or []:
            options = series.get("options") or {}
            for marker in options.get("candleMarkers") or options.get("markers") or []:
                if int(marker.get("time") or 0) == bar_time:
                    text = str(marker.get("text") or "").strip()
                    if text:
                        texts.append(text)
    return texts


def indicator_context_at(snapshot: dict[str, Any], bar_time: int) -> tuple[dict[str, float], dict[str, str]]:
    values: dict[str, float] = {}
    colors: dict[str, str] = {}
    for indicator in snapshot.get("indicators") or []:
        indicator_id = str(indicator.get("id") or "indicator")
        for series in indicator.get("series") or []:
            series_id = str(series.get("id") or "series")
            for point in series.get("data") or []:
                if int(point.get("time") or 0) != bar_time:
                    continue
                value = point.get("value")
                if isinstance(value, (int, float)):
                    values[f"{indicator_id}.{series_id}"] = float(value)
                color = str(point.get("color") or "").strip()
                if color:
                    colors[f"{indicator_id}.{series_id}"] = color
                break
    return values, colors


def atr_at(snapshot: dict[str, Any], bar_time: int, period: int) -> float | None:
    candles = snapshot.get("candles") or []
    period = max(int(period), 1)
    target_index = next(
        (index for index, candle in enumerate(candles) if int(candle.get("time") or 0) == bar_time),
        None,
    )
    if target_index is None or target_index <= 0 or target_index + 1 < period:
        return None
    true_ranges: list[Decimal] = []
    for index in range(target_index - period + 1, target_index + 1):
        try:
            high = Decimal(str(candles[index].get("high")))
            low = Decimal(str(candles[index].get("low")))
            previous_close = Decimal(str(candles[index - 1].get("close")))
        except (InvalidOperation, TypeError, ValueError):
            return None
        true_ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    return float(sum(true_ranges) / Decimal(len(true_ranges))) if true_ranges else None


def bar_time_label(snapshot: dict[str, Any], bar_time: int) -> str:
    label = str((snapshot.get("time_labels") or {}).get(str(bar_time)) or "").strip()
    if label:
        return label
    try:
        return datetime.fromtimestamp(bar_time, tz=DISPLAY_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(bar_time)


def client_oid(symbol: str, side: str, bar_time: int | None) -> str:
    return f"tq-live-{symbol.lower()}-{side}-{bar_time or int(time.time())}"[:64]


def htf_entry_lock_key(symbol: str, side: str, duration_seconds: int, bar_time: int) -> str:
    return f"{str(symbol or '').upper()}:{str(side or '').lower()}:htf:{int(duration_seconds)}:{int(bar_time)}"


def duration_label(duration_seconds: int) -> str:
    seconds = max(int(duration_seconds), 1)
    if seconds % 86400 == 0:
        days = seconds // 86400
        return f"{days}D" if days != 1 else "1D"
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    if seconds % 60 == 0:
        return f"{seconds // 60}m"
    return f"{seconds}s"


def optional_float(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
