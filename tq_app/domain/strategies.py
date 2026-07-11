from __future__ import annotations

from dataclasses import dataclass
import importlib.util
from pathlib import Path
import threading
from typing import Callable, Protocol


SIGNAL_TEXT_BY_SIDE = {
    "buy": {"Buy", "买"},
    "sell": {"Sell", "卖"},
}


@dataclass(frozen=True, slots=True)
class StrategyContext:
    marker_texts: tuple[str, ...]
    indicator_values: dict[str, float]
    indicator_colors: dict[str, str]
    signal_mode: str
    bar_high: float | None
    bar_low: float | None


@dataclass(frozen=True, slots=True)
class StrategyResult:
    side: str | None
    reason: str


class SignalStrategy(Protocol):
    name: str

    def evaluate(self, context: StrategyContext) -> StrategyResult:
        raise NotImplementedError


StrategyFactory = Callable[[], SignalStrategy]


class StrategyRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, StrategyFactory] = {}

    def register(self, name: str, factory: StrategyFactory, *, aliases: tuple[str, ...] = ()) -> None:
        normalized_names = [_normalize_strategy_name(raw_name) for raw_name in (name, *aliases)]
        if len(set(normalized_names)) != len(normalized_names):
            raise ValueError(f"策略名称或别名重复: {normalized_names}")
        for normalized in normalized_names:
            if normalized in self._factories:
                raise ValueError(f"策略已注册: {normalized}")
        for normalized in normalized_names:
            self._factories[normalized] = factory

    def create(self, name: str) -> SignalStrategy:
        normalized = _normalize_strategy_name(name)
        try:
            factory = self._factories[normalized]
        except KeyError as exc:
            available = ", ".join(self.names())
            raise KeyError(f"未知策略: {name}，当前可选: {available}") from exc
        return factory()

    def names(self) -> list[str]:
        return sorted(self._factories)


class MarkerSignalStrategy:
    name = "marker_signal"

    def evaluate(self, context: StrategyContext) -> StrategyResult:
        side = _side_from_marker_texts(context.marker_texts, context.signal_mode)
        if side is None:
            return StrategyResult(None, f"目标 K 线没有满足 {context.signal_mode} 模式的交易信号")
        hull_ok, hull_reason = hull_position_allows_side(
            side,
            context.indicator_values,
            bar_high=context.bar_high,
            bar_low=context.bar_low,
        )
        if not hull_ok:
            return StrategyResult(None, hull_reason)
        return StrategyResult(side, f"检测到 {','.join(context.marker_texts)} 信号")


class StcExtremeContrarianStrategy:
    name = "stc_extreme_contrarian"

    def evaluate(self, context: StrategyContext) -> StrategyResult:
        texts = set(context.marker_texts)
        stc_value = context.indicator_values.get("stc.stc")
        stc_color = context.indicator_colors.get("stc.stc", "")
        has_any_buy = "Buy" in texts or "买" in texts
        has_any_sell = "Sell" in texts or "卖" in texts

        if has_any_sell and stc_value is not None and stc_value > 75 and is_red_color(stc_color):
            hull_ok, hull_reason = hull_position_allows_side(
                "sell", context.indicator_values, bar_high=context.bar_high, bar_low=context.bar_low
            )
            if not hull_ok:
                return StrategyResult(None, hull_reason)
            return StrategyResult(
                "sell",
                f"空单观察信号：Sell 或 卖 出现，且 STC={stc_value:.2f}>75 并为红色；{hull_reason}",
            )
        if has_any_buy and stc_value is not None and stc_value < 25 and is_green_color(stc_color):
            hull_ok, hull_reason = hull_position_allows_side(
                "buy", context.indicator_values, bar_high=context.bar_high, bar_low=context.bar_low
            )
            if not hull_ok:
                return StrategyResult(None, hull_reason)
            return StrategyResult(
                "buy",
                f"多单观察信号：Buy 或 买 出现，且 STC={stc_value:.2f}<25 并为绿色；{hull_reason}",
            )
        return StrategyResult(
            None,
            "未满足观察策略：空单需 Sell/卖 任一信号且 STC>75 红色；多单需 Buy/买 任一信号且 STC<25 绿色。"
            f" 当前 signals={','.join(context.marker_texts) or '-'}, STC={stc_value}, color={stc_color or '-'}",
        )


class StcExtremeContrarian1d1hReentryStrategy(StcExtremeContrarianStrategy):
    """Base STC strategy with 1D filtering and 1H Hull/STC-confirmed re-entry."""

    name = "stc_extreme_contrarian_1d_1h_reentry"
    primary_htf_duration_seconds = 86400
    reentry_confirmation_duration_seconds = 3600


def hull_position_allows_side(
    side: str,
    indicator_values: dict[str, float],
    *,
    bar_high: float | None,
    bar_low: float | None,
) -> tuple[bool, str]:
    if bar_high is None or bar_low is None:
        return False, "缺少开仓 K 线 high/low，无法判断 Hull 与 K 线位置，禁止开仓。"
    high = float(bar_high)
    low = float(bar_low)
    hull_values = hull_band_values(indicator_values, side)
    if side == "buy":
        if not hull_values:
            return False, "缺少绿色 Hull 多趋势带指标值，无法判断多单位置，禁止开仓。"
        if all(value < low for value in hull_values):
            return True, f"绿色 Hull 多趋势带在 K 线下方，允许多单：hull={format_float_list(hull_values)}, low={format_price(low)}"
        return False, (
            "绿色 Hull 多趋势带未完全位于开仓 K 线下方，禁止多单：要求上下边界都 < low；"
            f"hull={format_float_list(hull_values)}, high={format_price(high)}, low={format_price(low)}"
        )
    if side == "sell":
        if not hull_values:
            return False, "缺少红色 Hull 空趋势带指标值，无法判断空单位置，禁止开仓。"
        if all(value > high for value in hull_values):
            return True, f"红色 Hull 空趋势带在 K 线上方，允许空单：hull={format_float_list(hull_values)}, high={format_price(high)}"
        return False, (
            "红色 Hull 空趋势带未完全位于开仓 K 线上方，禁止空单：要求上下边界都 > high；"
            f"hull={format_float_list(hull_values)}, high={format_price(high)}, low={format_price(low)}"
        )
    return False, f"未知开仓方向，无法判断 Hull 位置: {side}"


def hull_band_values(indicator_values: dict[str, float], side: str) -> list[float]:
    keys = (
        ("merged_dkx_hull_ut.mhull_up", "merged_dkx_hull_ut.shull_up")
        if side == "buy"
        else ("merged_dkx_hull_ut.mhull_down", "merged_dkx_hull_ut.shull_down")
    )
    return [float(indicator_values[key]) for key in keys if indicator_values.get(key) is not None]


def is_red_color(color: str) -> bool:
    normalized = color.replace(" ", "").lower()
    return "red" in normalized or "#f23645" in normalized or "239,83,80" in normalized or "242,54,69" in normalized


def is_green_color(color: str) -> bool:
    normalized = color.replace(" ", "").lower()
    return "green" in normalized or "#089981" in normalized or "#4caf50" in normalized or "38,166,154" in normalized


def format_price(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def format_float_list(values: list[float]) -> str:
    return "[" + ", ".join(format_price(value) for value in values) + "]"


def _side_from_marker_texts(marker_texts: tuple[str, ...], mode: str) -> str | None:
    texts = set(marker_texts)
    has_buy = bool(texts & SIGNAL_TEXT_BY_SIDE["buy"])
    has_sell = bool(texts & SIGNAL_TEXT_BY_SIDE["sell"])
    if mode == "ut":
        has_buy = "Buy" in texts
        has_sell = "Sell" in texts
    elif mode == "dkx":
        has_buy = "买" in texts
        has_sell = "卖" in texts
    elif mode == "confirmed":
        has_buy = "Buy" in texts and "买" in texts
        has_sell = "Sell" in texts and "卖" in texts
    if has_buy == has_sell:
        return None
    return "buy" if has_buy else "sell"


def _normalize_strategy_name(name: str) -> str:
    normalized = str(name or "").strip().lower()
    if not normalized:
        raise ValueError("策略名称不能为空")
    return normalized


_REGISTRY = StrategyRegistry()
_REGISTRY.register("marker_signal", MarkerSignalStrategy, aliases=("default", "live_decision"))
_REGISTRY.register("stc_extreme_contrarian", StcExtremeContrarianStrategy)
_REGISTRY.register(
    "stc_extreme_contrarian_1d_1h_reentry",
    StcExtremeContrarian1d1hReentryStrategy,
    aliases=("stc_1d_1h_reentry",),
)
_CUSTOM_LOAD_LOCK = threading.Lock()
_LOADED_CUSTOM_PATHS: set[Path] = set()


def get_strategy_registry() -> StrategyRegistry:
    return _REGISTRY


def register_strategy(name: str, factory: StrategyFactory, *, aliases: tuple[str, ...] = ()) -> None:
    _REGISTRY.register(name, factory, aliases=aliases)


def load_custom_strategies(project_root: Path, registry: StrategyRegistry | None = None) -> None:
    custom_path = (project_root / "custom_strategies.py").resolve()
    if not custom_path.exists():
        return
    target = registry or _REGISTRY
    with _CUSTOM_LOAD_LOCK:
        if custom_path in _LOADED_CUSTOM_PATHS:
            return
        spec = importlib.util.spec_from_file_location("custom_strategies", custom_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"无法加载自定义策略文件: {custom_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        register = getattr(module, "register_strategies", None)
        if register is None:
            raise RuntimeError("custom_strategies.py 需要提供 register_strategies(registry) 函数。")
        register(target)
        _LOADED_CUSTOM_PATHS.add(custom_path)
