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
        self._canonical_by_name: dict[str, str] = {}

    def register(self, name: str, factory: StrategyFactory, *, aliases: tuple[str, ...] = ()) -> None:
        normalized_names = [_normalize_strategy_name(raw_name) for raw_name in (name, *aliases)]
        if len(set(normalized_names)) != len(normalized_names):
            raise ValueError(f"策略名称或别名重复: {normalized_names}")
        for normalized in normalized_names:
            if normalized in self._factories:
                raise ValueError(f"策略已注册: {normalized}")
        for normalized in normalized_names:
            self._factories[normalized] = factory
            self._canonical_by_name[normalized] = normalized_names[0]

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

    def catalog(self) -> list[dict[str, object]]:
        canonical_names = sorted(set(self._canonical_by_name.values()))
        return [
            {
                "name": canonical,
                "aliases": sorted(
                    name
                    for name, target in self._canonical_by_name.items()
                    if target == canonical and name != canonical
                ),
                "details": _strategy_catalog_details(self._factories[canonical], canonical),
            }
            for canonical in canonical_names
        ]


class MarkerSignalStrategy:
    name = "marker_signal"
    explanation = {
        "title": "标记信号顺势策略",
        "summary": "直接读取 Buy、Sell、买、卖标记，并用当前 K 线位置和高周期 Hull/STC 方向过滤入场。",
        "tags": ["标记信号", "Hull 位置过滤", "高周期顺势"],
        "sections": [
            {
                "title": "低周期入场",
                "items": [
                    "signal_mode=any 时，Buy/买任一出现视为多信号，Sell/卖任一出现视为空信号。",
                    "signal_mode 也可选择 ut、dkx 或 confirmed；confirmed 要求两套同向标记同时出现。",
                    "同一根 K 线同时存在多空信号，或完全没有信号时，不开仓。",
                ],
            },
            {
                "title": "Hull 位置条件",
                "items": [
                    "开多要求绿色 Hull 趋势带的上下边界都位于入场 K 线最低价下方。",
                    "开空要求红色 Hull 趋势带的上下边界都位于入场 K 线最高价上方。",
                ],
            },
            {
                "title": "高周期与重复开仓",
                "items": [
                    "启用高周期过滤时，已收完的高周期 Hull 与 STC 必须同向，并且与低周期入场方向一致。",
                    "同一高周期 Hull 颜色段内，同一方向只允许首次开仓；该策略没有额外的重复开仓确认周期。",
                ],
            },
            {
                "title": "需要关注的配置",
                "items": [
                    "signal_mode、use_closed_bar、htf_hull_filter_enabled 和 htf_hull_duration_seconds 会改变信号口径。",
                    "止损、保本、移动保护、手续费和滑点由回测 Profile 的公共风控配置负责。",
                ],
            },
        ],
    }

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
    explanation = {
        "title": "STC 极值反转顺势策略",
        "summary": "在低周期 STC 极值区寻找反转标记，同时要求 Hull 位置正确，并服从高周期 Hull/STC 主趋势。",
        "tags": ["STC 极值", "反转入场", "高周期顺势"],
        "sections": [
            {
                "title": "多单条件",
                "items": [
                    "Buy 或 买 标记至少出现一个。",
                    "低周期 STC 小于 25 且为绿色。",
                    "绿色 Hull 趋势带的上下边界都必须位于入场 K 线最低价下方。",
                ],
            },
            {
                "title": "空单条件",
                "items": [
                    "Sell 或 卖 标记至少出现一个。",
                    "低周期 STC 大于 75 且为红色。",
                    "红色 Hull 趋势带的上下边界都必须位于入场 K 线最高价上方。",
                ],
            },
            {
                "title": "高周期与重复开仓",
                "items": [
                    "高周期由 htf_hull_duration_seconds 配置决定；已收完的 Hull 与 STC 必须同向，并与入场方向一致。",
                    "同一高周期 Hull 颜色段内，同一方向只允许首次开仓；风控平仓后也不会在该颜色段内再次进入。",
                ],
            },
            {
                "title": "需要关注的配置",
                "items": [
                    "STC 的 length、fast_length、slow_length、factor 与 Hull 参数会直接改变信号。",
                    "use_closed_bar 决定使用已收完 K 线还是最新 K 线；风控退出和交易成本由 Profile 负责。",
                ],
            },
        ],
    }

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
    explanation = {
        "title": "STC 1D 主趋势 / 1H 同向再入场",
        "summary": "低周期使用 STC 极值反转信号，固定由 1D Hull/STC 决定主方向，并允许平仓后经 1H 同向确认重复开仓。",
        "tags": ["1D 主趋势", "1H 再入场", "STC 极值"],
        "sections": [
            {
                "title": "首次开仓",
                "items": [
                    "低周期入场条件与 STC 极值反转顺势策略相同：多单要求 Buy/买、STC<25 绿色且 Hull 在 K 线下方；空单条件相反。",
                    "主趋势周期固定为 1D，不受 Profile 中其他高周期数值覆盖；已收完的 1D Hull 和 STC 必须与入场方向一致。",
                ],
            },
            {
                "title": "1H 同向重复开仓",
                "items": [
                    "首次仓位被风控平掉后，如果仍处于同一个 1D Hull 颜色段，后续低周期信号可以申请再次开仓。",
                    "重复开仓必须使用已收完的 1H K 线确认，且 1H Hull 与 1H STC 都要和申请方向一致。",
                    "1H 方向相反、Hull/STC 不一致、快照缺失或周期不正确时，都会继续保持 1D 段内开仓锁。",
                ],
            },
            {
                "title": "趋势段锁定",
                "items": [
                    "锁按品种、方向、1D 周期和 1D Hull 趋势起点生成；1D Hull 进入新颜色段后会形成新的开仓机会。",
                    "1H 只负责确认同一 1D 段内的后续入场，不会改变 1D 主方向。",
                ],
            },
            {
                "title": "适合验证的内容",
                "items": [
                    "建议重点比较重复入场带来的交易次数、回撤、手续费占比及不同市场阶段的稳定性。",
                    "止损、保本、移动保护与成本仍使用 Profile 的公共风控参数，可与指标参数组成矩阵测试。",
                ],
            },
        ],
    }


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


def _strategy_catalog_details(factory: StrategyFactory, canonical_name: str) -> dict[str, object]:
    raw = getattr(factory, "explanation", None)
    if not isinstance(raw, dict):
        return {
            "title": canonical_name,
            "summary": "该自定义策略尚未提供结构化说明，请以策略实现及回测结果为准。",
            "tags": ["自定义策略"],
            "sections": [
                {
                    "title": "说明状态",
                    "items": ["策略可正常使用，但作者尚未在 explanation 元数据中声明入场、过滤和重复开仓规则。"],
                }
            ],
            "primary_htf_duration_seconds": _positive_int_or_none(
                getattr(factory, "primary_htf_duration_seconds", None)
            ),
            "reentry_confirmation_duration_seconds": _positive_int_or_none(
                getattr(factory, "reentry_confirmation_duration_seconds", None)
            ),
        }
    sections = []
    for section in raw.get("sections", []):
        if not isinstance(section, dict):
            continue
        items = [str(item) for item in section.get("items", []) if str(item).strip()]
        if items:
            sections.append({"title": str(section.get("title") or "规则"), "items": items})
    return {
        "title": str(raw.get("title") or canonical_name),
        "summary": str(raw.get("summary") or "暂无策略摘要。"),
        "tags": [str(tag) for tag in raw.get("tags", []) if str(tag).strip()],
        "sections": sections,
        "primary_htf_duration_seconds": _positive_int_or_none(
            getattr(factory, "primary_htf_duration_seconds", None)
        ),
        "reentry_confirmation_duration_seconds": _positive_int_or_none(
            getattr(factory, "reentry_confirmation_duration_seconds", None)
        ),
    }


def _positive_int_or_none(value: object) -> int | None:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


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


def get_strategy_catalog(project_root: Path | None = None) -> list[dict[str, object]]:
    if project_root is not None:
        load_custom_strategies(project_root)
    return _REGISTRY.catalog()


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
