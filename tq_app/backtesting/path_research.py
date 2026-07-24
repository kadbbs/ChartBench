from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
import subprocess
from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any, Callable

import numpy as np
import pandas as pd

from tq_app.domain.strategies import is_green_color, is_red_color

from .application import ResolvedBacktestRun
from .engine import PreparedBacktestStudy
from .performance import (
    attach_deflated_sharpe,
    cscv_probability_of_backtest_overfitting,
    performance_metrics,
    strip_private_performance_fields,
)
from .runtime import PreparedBacktestMarket


PATH_DATASET_SCHEMA_VERSION = 2
PATH_MATRIX_SCHEMA_VERSION = 3
PATH_STOP_UNITS = {"atr", "percent", "points"}
PATH_INTRABAR_POLICIES = {"stop_first", "take_first"}
PATH_REPLAY_BAR_COLUMNS = (
    "time",
    "open",
    "high",
    "low",
    "close",
    "atr",
)
PATH_BAR_COLUMNS = (
    "relative_index",
    "time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "atr",
    "stc",
    "stc_delta",
    "stc_direction",
    "stc_zone",
    "dkx_w",
    "dkx_d",
    "dkx_k",
    "dkx_spread",
    "dkx_buy",
    "dkx_sell",
    "marker_buy",
    "marker_sell",
    "mhull",
    "shull",
    "hull_direction",
    "mhull_up",
    "shull_up",
    "mhull_down",
    "shull_down",
    "ut_atr",
    "ut_n_loss",
    "ut_source",
    "ut_trailing_stop",
    "ut_distance",
    "ut_position",
    "ut_buy",
    "ut_sell",
    "d1_bar_time",
    "d1_stc",
    "d1_stc_delta",
    "d1_stc_direction",
    "d1_mhull",
    "d1_shull",
    "d1_hull_direction",
    "d1_hull_stc_aligned",
    "d1_trend",
)
PATH_SIGNAL_FEATURE_COLUMNS = PATH_BAR_COLUMNS[7:]
PATH_FEATURE_DEFINITIONS = {
    "atr": "5m 简单移动平均真实波幅；周期见 manifest.execution.atr_period，矩阵 ATR 距离使用此值。",
    "stc": "5m STC 数值。",
    "stc_delta": "当前 5m STC 减上一根 5m STC。",
    "stc_direction": "5m STC 因果方向：1=上升/绿色，-1=下降/红色，0=不明确。",
    "stc_zone": "5m STC 区域：-1=<25，0=25~75，1=>75。",
    "dkx_w": "5m DKX 中间价 W。",
    "dkx_d": "5m DKX D 线。",
    "dkx_k": "5m DKX K 线。",
    "dkx_spread": "5m DKX D-K。",
    "dkx_buy": "5m DKX 金叉原始标记：1=买。",
    "dkx_sell": "5m DKX 死叉原始标记：1=卖。",
    "marker_buy": "策略可见的任一多标记：DKX 买或 UT Buy。",
    "marker_sell": "策略可见的任一空标记：DKX 卖或 UT Sell。",
    "mhull": "5m Hull 主线 MHULL 原始值。",
    "shull": "5m Hull 慢线 SHULL 原始值。",
    "hull_direction": "5m Hull 因果方向：1=上升，-1=下降。",
    "mhull_up": "5m 上升 Hull 段的 MHULL；非上升段为空。",
    "shull_up": "5m 上升 Hull 段的 SHULL；非上升段为空。",
    "mhull_down": "5m 下降 Hull 段的 MHULL；非下降段为空。",
    "shull_down": "5m 下降 Hull 段的 SHULL；非下降段为空。",
    "ut_atr": "5m UT Bot 使用的 Wilder RMA ATR。",
    "ut_n_loss": "5m UT Bot 灵敏度乘 ATR 得到的 nLoss。",
    "ut_source": "5m UT Bot 输入价格；是否使用 Heikin-Ashi 见指标参数。",
    "ut_trailing_stop": "5m UT Bot 跟踪止损线。",
    "ut_distance": "5m UT 输入价格减 UT 跟踪止损线。",
    "ut_position": "5m UT 状态：1=多，-1=空，0=尚未切换。",
    "ut_buy": "5m UT Bot 原始 Buy 触发：1=触发。",
    "ut_sell": "5m UT Bot 原始 Sell 触发：1=触发。",
    "d1_bar_time": "该 5m K 线收盘后可见的最近一根已闭合 1D K 线开盘时间。",
    "d1_stc": "最近已闭合 1D STC 数值。",
    "d1_stc_delta": "最近已闭合 1D STC 减其前一根 1D STC。",
    "d1_stc_direction": "最近已闭合 1D STC 因果方向：1=上升/绿色，-1=下降/红色，0=不明确。",
    "d1_mhull": "最近已闭合 1D MHULL 原始值。",
    "d1_shull": "最近已闭合 1D SHULL 原始值。",
    "d1_hull_direction": "最近已闭合 1D Hull 因果方向：1=上升，-1=下降。",
    "d1_hull_stc_aligned": "最近已闭合 1D Hull/STC 是否同向：1=同向，0=不同向或不明确。",
    "d1_trend": "策略日线过滤方向：1=允许多，-1=允许空，0=不同向或不明确。",
}
CUSTOM_INDICATOR_FEATURE_COLUMNS = (
    "dkx_w",
    "dkx_d",
    "dkx_k",
    "dkx_spread",
    "dkx_buy",
    "dkx_sell",
    "mhull",
    "shull",
    "hull_direction",
    "ut_atr",
    "ut_n_loss",
    "ut_source",
    "ut_trailing_stop",
    "ut_distance",
    "ut_position",
    "ut_buy",
    "ut_sell",
)
INTEGER_FEATURE_COLUMNS = {
    "stc_direction",
    "stc_zone",
    "dkx_buy",
    "dkx_sell",
    "marker_buy",
    "marker_sell",
    "hull_direction",
    "ut_position",
    "ut_buy",
    "ut_sell",
    "d1_stc_direction",
    "d1_hull_direction",
    "d1_hull_stc_aligned",
    "d1_trend",
}


@dataclass(slots=True)
class SignalPathDataset:
    manifest: dict[str, Any]
    bars: list[dict[str, Any]]
    signals: list[dict[str, Any]]
    episodes: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "manifest": self.manifest,
            "bars": self.bars,
            "signals": self.signals,
            "episodes": self.episodes,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SignalPathDataset":
        return cls(
            manifest=dict(payload.get("manifest") or {}),
            bars=list(payload.get("bars") or []),
            signals=list(payload.get("signals") or []),
            episodes=list(payload.get("episodes") or []),
        )


@dataclass(frozen=True, slots=True)
class PathReplayConfig:
    stop_unit: str = "atr"
    stop_value: float | None = None
    take_value: float | None = None
    max_reentries: int = 0
    reentry_cooldown_bars: int = 0
    intrabar_policy: str = "stop_first"
    reveal_test: bool = False


@dataclass(slots=True)
class PreparedPathReplay:
    arrays: dict[str, np.ndarray]
    bar_times: list[int]
    bar_duration: int


def _prepare_path_replay(dataset: SignalPathDataset) -> PreparedPathReplay:
    return PreparedPathReplay(
        arrays={
            key: np.asarray(
                [float(item[key]) for item in dataset.bars],
                dtype="float64",
            )
            for key in ("open", "high", "low", "close")
        },
        bar_times=[int(item["time"]) for item in dataset.bars],
        bar_duration=_bar_duration_seconds(dataset.bars),
    )


def build_signal_path_dataset(
    resolved: ResolvedBacktestRun,
    prepared: PreparedBacktestMarket,
    study: PreparedBacktestStudy,
    *,
    context_bars: int = 288,
) -> SignalPathDataset:
    """Build the immutable no-risk signal path used by LLM exports and replay.

    Signals are evaluated on a closed 5-minute bar and executed on the next
    bar's open. A position is held until the next opposite signal that has
    already passed the configured 1D Hull/STC filter. Same-side signals are
    recorded but never change the baseline position.
    """

    if resolved.config.duration_seconds != 300:
        raise ValueError("信号路径研究当前固定使用 5 分钟执行周期。")
    if not resolved.live_config.use_closed_bar:
        raise ValueError("信号路径研究要求 use_closed_bar=true，避免未闭合 K 线污染样本。")
    if not resolved.live_config.htf_hull_filter_enabled:
        raise ValueError("信号路径研究要求启用日线 Hull/STC 过滤。")
    if int(resolved.strategy.primary_htf_duration_seconds) != 86400:
        raise ValueError("信号路径研究要求主趋势周期为 1D（86400 秒）。")

    bars = _research_bars(study, resolved.config.atr_period)
    if len(bars) != study.bar_count:
        raise RuntimeError("研究 K 线与预计算信号数量不一致。")

    signals: list[dict[str, Any]] = []
    episodes: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    warmup = max(int(resolved.config.warmup_bars), 1)

    for execution_index in range(warmup, len(bars)):
        signal = study.signals[execution_index]
        if signal is None or signal.side not in {"buy", "sell"}:
            continue
        signal_index = execution_index - 1
        relation = (
            "entry"
            if current is None
            else "same_side"
            if current["side"] == signal.side
            else "reverse"
        )
        context = dict(signal.htf_context or {})
        signal_bar = bars[signal_index]
        event = {
            "id": len(signals) + 1,
            "side": signal.side,
            "relation": relation,
            "signal_index": signal_index,
            "execution_index": execution_index,
            "signal_time": int(bars[signal_index]["time"]),
            "signal_time_label": bars[signal_index]["time_label"],
            "execution_time": int(bars[execution_index]["time"]),
            "execution_time_label": bars[execution_index]["time_label"],
            "execution_open": float(bars[execution_index]["open"]),
            "reason": signal.reason,
            "marker_texts": list(signal.marker_texts),
            "indicator_values": dict(signal.indicator_values or {}),
            "indicator_colors": dict(signal.indicator_colors or {}),
            "atr_value": signal.atr_value,
            "research_features": {
                key: signal_bar.get(key)
                for key in PATH_SIGNAL_FEATURE_COLUMNS
            },
            "daily_trend": context.get("trend"),
            "daily_bar_time": context.get("bar_time"),
            "daily_bar_time_label": context.get("bar_time_label"),
            "daily_segment_start_time": context.get("trend_start_time"),
            "daily_segment_start_time_label": context.get("trend_start_time_label"),
            "daily_indicator_values": dict(context.get("indicator_values") or {}),
            "daily_indicator_colors": dict(context.get("indicator_colors") or {}),
            "daily_lock_key": signal.htf_lock_key,
        }
        signals.append(event)

        if current is None:
            current = _new_episode(len(episodes) + 1, event, context_bars)
            continue
        if current["side"] == signal.side:
            current["same_side_signal_ids"].append(event["id"])
            continue

        _finish_episode(current, bars, event)
        episodes.append(current)
        current = _new_episode(len(episodes) + 1, event, context_bars)

    if current is not None:
        _finish_open_episode(current, bars)
        episodes.append(current)

    first_time = int(bars[0]["time"])
    last_time = int(bars[-1]["time"])
    span = max(last_time - first_time, 1)
    research_end = first_time + int(span * 0.60)
    validation_end = first_time + int(span * 0.80)
    for episode in episodes:
        entry_time = int(episode["entry_time"])
        episode["split"] = (
            "research"
            if entry_time <= research_end
            else "validation"
            if entry_time <= validation_end
            else "test"
        )

    fingerprint = _dataset_fingerprint(
        resolved,
        bars,
        signals,
        context_bars=context_bars,
    )
    manifest = {
        "schema_version": PATH_DATASET_SCHEMA_VERSION,
        "dataset_id": fingerprint[:20],
        "source_sha256": fingerprint,
        "code": _code_identity(Path(__file__).resolve().parents[2]),
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "provider": resolved.config.provider,
        "symbol": resolved.config.symbol,
        "duration_seconds": resolved.config.duration_seconds,
        "primary_htf_duration_seconds": resolved.strategy.primary_htf_duration_seconds,
        "strategy": resolved.strategy.name,
        "signal_strategy": resolved.strategy.signal_strategy_name,
        "bar_count": len(bars),
        "signal_count": len(signals),
        "episode_count": len(episodes),
        "closed_episode_count": sum(1 for item in episodes if item["status"] == "closed"),
        "llm_research_episode_count": sum(
            1
            for item in episodes
            if item.get("split") == "research"
            and item.get("exit_time") is not None
            and int(item["exit_time"]) <= research_end
        ),
        "context_bars": int(context_bars),
        "first_time": first_time,
        "last_time": last_time,
        "first_time_label": bars[0]["time_label"],
        "last_time_label": bars[-1]["time_label"],
        "splits": {
            "research_end": research_end,
            "validation_end": validation_end,
            "research_ratio": 0.60,
            "validation_ratio": 0.20,
            "test_ratio": 0.20,
        },
        "execution": {
            "signal_bar": "closed_5m",
            "fill_bar": "next_5m_open",
            "daily_filter": "closed_1d_hull_and_stc_same_direction",
            "opposite_signal": "qualified_by_daily_filter",
            "same_side_signal": "record_only",
            "risk_exits_enabled": False,
            "initial_equity": resolved.config.initial_equity,
            "margin_amount": resolved.config.margin_amount,
            "margin_ratio_per_trade": resolved.config.margin_ratio_per_trade,
            "leverage": resolved.config.leverage,
            "fee_rate": resolved.config.fee_rate,
            "slippage_rate": resolved.config.slippage_rate,
            "atr_period": resolved.config.atr_period,
        },
        "indicator_parameters": study.indicator_parameters,
        "bar_columns": list(PATH_BAR_COLUMNS),
        "feature_definitions": PATH_FEATURE_DEFINITIONS,
        "causality": {
            "closed_bars_only": True,
            "stc_strategy_color": "current_vs_previous_value",
            "daily_alignment": (
                "For each 5m signal bar, select the second-last 1D candle available "
                "at the next 5m execution open; the newest 1D candle is still forming."
            ),
            "future_daily_values_excluded": True,
        },
    }
    return SignalPathDataset(manifest=manifest, bars=bars, signals=signals, episodes=episodes)


def baseline_summary(dataset: SignalPathDataset) -> dict[str, Any]:
    replay = replay_signal_paths(dataset, PathReplayConfig())
    all_closed = [item for item in dataset.episodes if item["status"] == "closed"]
    validation_end = (dataset.manifest.get("splits") or {}).get("validation_end")
    visible_closed = [
        item
        for item in all_closed
        if (
            int(item.get("exit_time") or item.get("entry_time") or 0)
            <= int(validation_end)
            if validation_end is not None
            else str(item.get("split") or "research") != "test"
        )
    ]
    holding = [int(item["holding_bars"]) for item in visible_closed]
    mfe_atr = [
        float(item["mfe_atr"])
        for item in visible_closed
        if item.get("mfe_atr") is not None
    ]
    mae_atr = [
        float(item["mae_atr"])
        for item in visible_closed
        if item.get("mae_atr") is not None
    ]
    same_side = sum(len(item.get("same_side_signal_ids") or []) for item in dataset.episodes)
    best = {
        **replay,
        "verdict": "基准完成",
        "robust_score": None,
        "path_stat_scope": "research_and_validation",
        "median_holding_bars": median(holding) if holding else 0,
        "median_mfe_atr": median(mfe_atr) if mfe_atr else None,
        "median_mae_atr": median(mae_atr) if mae_atr else None,
        "qualified_signal_count": len(dataset.signals),
        "same_side_signal_count": same_side,
        "closed_episode_count": len(all_closed),
        "visible_closed_episode_count": len(visible_closed),
        "llm_research_episode_count": int(
            dataset.manifest.get("llm_research_episode_count") or 0
        ),
        "open_episode_count": sum(1 for item in dataset.episodes if item["status"] == "open"),
    }
    return {
        "schema_version": PATH_DATASET_SCHEMA_VERSION,
        "type": "signal_path_baseline",
        "name": f"{dataset.manifest['symbol']} 无风控信号路径",
        "dataset": dataset.manifest,
        "best": best,
        "rows": [],
        "grid": {},
        "score_explanation": (
            "基准结果只按日线 Hull/STC 同向过滤后的 5m 反向信号平仓，"
            "没有止损、止盈、保本、移动保护或同向再入场。最终测试未揭盲时，"
            "跨越验证边界的持仓按边界最后一根可见 K 线清算价值估值，不读取未来平仓收益。"
        ),
    }


def run_path_matrix(
    dataset: SignalPathDataset,
    *,
    stop_unit: str,
    stop_values: list[float],
    take_values: list[float],
    max_reentries: int = 0,
    reentry_cooldown_bars: int = 0,
    intrabar_policy: str = "stop_first",
    reveal_test: bool = False,
    progress_callback: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    if stop_unit not in PATH_STOP_UNITS:
        raise ValueError(f"未知距离单位: {stop_unit}")
    if intrabar_policy not in PATH_INTRABAR_POLICIES:
        raise ValueError(f"未知同 K 线优先规则: {intrabar_policy}")
    if not stop_values or not take_values:
        raise ValueError("止损和止盈轴都至少需要一个候选值。")
    if any(float(value) <= 0 for value in (*stop_values, *take_values)):
        raise ValueError("止损和止盈候选值必须大于 0。")

    prepared_replay = _prepare_path_replay(dataset)
    baseline = replay_signal_paths(
        dataset,
        PathReplayConfig(reveal_test=reveal_test),
        include_performance_series=True,
        _prepared=prepared_replay,
    )
    rows: list[dict[str, Any]] = []
    total = len(stop_values) * len(take_values)
    completed = 0
    for stop_value in stop_values:
        for take_value in take_values:
            parameters = {
                "stop_loss": float(stop_value),
                "take_profit": float(take_value),
                "unit": stop_unit,
                "max_reentries": int(max_reentries),
                "reentry_cooldown_bars": int(reentry_cooldown_bars),
                "intrabar_policy": intrabar_policy,
            }
            metrics = replay_signal_paths(
                dataset,
                PathReplayConfig(
                    stop_unit=stop_unit,
                    stop_value=float(stop_value),
                    take_value=float(take_value),
                    max_reentries=max(int(max_reentries), 0),
                    reentry_cooldown_bars=max(int(reentry_cooldown_bars), 0),
                    intrabar_policy=intrabar_policy,
                    reveal_test=reveal_test,
                ),
                include_performance_series=True,
                _prepared=prepared_replay,
            )
            score = _selection_score(metrics)
            rows.append(
                {
                    "rank": 0,
                    "parameters": parameters,
                    "robust_score": score,
                    "plateau_score": 0.0,
                    "plateau": False,
                    "region_id": None,
                    "region_size": 0,
                    "positive_neighbor_ratio": 0.0,
                    "neighbor_min_validation_return_pct": None,
                    "neighbor_median_validation_return_pct": None,
                    "neighbor_std_validation_return_pct": None,
                    "verdict": "待评估",
                    **metrics,
                    **_comparison_fields(metrics, baseline),
                }
            )
            completed += 1
            if progress_callback is not None:
                progress_callback(completed, total)

    statistical_candidates = [baseline, *rows]
    multiple_testing = attach_deflated_sharpe(statistical_candidates)
    overfitting = cscv_probability_of_backtest_overfitting(
        [
            list(item.get("_daily_returns") or [])
            for item in statistical_candidates
        ]
    )
    for row in rows:
        row["matrix_trial_count"] = int(multiple_testing["trial_count"])
        row["matrix_pbo_pct"] = overfitting.get("pbo_pct")
        row["matrix_cscv_split_count"] = int(
            overfitting.get("cscv_split_count") or 0
        )
    _attach_neighborhood_stability(rows, stop_values, take_values)
    _attach_plateau_regions(rows, stop_values, take_values)
    ranked = sorted(
        rows,
        key=lambda item: (
            bool(item["plateau"]),
            int(item["region_size"]),
            float(item["plateau_score"]),
            float(item["robust_score"]),
        ),
        reverse=True,
    )
    for rank, row in enumerate(ranked, start=1):
        row["rank"] = rank
        row["verdict"] = (
            "稳定区"
            if row["plateau"]
            else "盈利孤点"
            if float(row["validation_return_pct"]) > 0
            else "不通过"
        )
    best = _region_center_candidate(ranked, stop_values, take_values) or (ranked[0] if ranked else None)
    if best is not None:
        best["verdict"] = "稳定区候选" if best["plateau"] else best["verdict"]
    for row in rows:
        strip_private_performance_fields(row)
    strip_private_performance_fields(baseline)

    return {
        "schema_version": PATH_MATRIX_SCHEMA_VERSION,
        "type": "signal_path_matrix",
        "name": f"{dataset.manifest['symbol']} 止损止盈稳定区",
        "dataset": dataset.manifest,
        "grid": {
            "stop_loss": [float(value) for value in stop_values],
            "take_profit": [float(value) for value in take_values],
        },
        "heatmap": {
            "x_key": "stop_loss",
            "y_key": "take_profit",
            "x_values": [float(value) for value in stop_values],
            "y_values": [float(value) for value in take_values],
            "unit": stop_unit,
            "default_metric": "validation_return_pct",
            "test_revealed": bool(reveal_test),
        },
        "combination_count": len(rows),
        "statistical_validation": {
            **multiple_testing,
            **overfitting,
            "scope": (
                "all"
                if reveal_test
                else "research_and_validation_only"
            ),
        },
        "baseline": baseline,
        "best": best,
        "best_comparison": (
            _comparison_fields(best, baseline)
            if best is not None
            else {}
        ),
        "rows": ranked,
        "score_explanation": (
            "候选只使用研究段和验证段评分，测试段不参与排序。稳定区要求当前格盈利、"
            "3×3 邻域至少 80% 验证盈利、邻域最差收益为正且验证交易数充足；"
            "同一根 5m 同时触发止损止盈时按所选保守规则处理。DSR 按本次矩阵组合数"
            "校正多重试验，PBO 使用研究+验证逐日权益做 CSCV，均不读取隐藏测试段。"
        ),
    }


def replay_signal_paths(
    dataset: SignalPathDataset,
    config: PathReplayConfig,
    *,
    include_performance_series: bool = False,
    _prepared: PreparedPathReplay | None = None,
) -> dict[str, Any]:
    execution = dataset.manifest.get("execution") or {}
    initial_equity = float(execution.get("initial_equity") or 20_000.0)
    margin_amount = float(execution.get("margin_amount") or 1_000.0)
    margin_ratio = float(execution.get("margin_ratio_per_trade") or 0.0)
    leverage = float(execution.get("leverage") or 10.0)
    fee_rate = float(execution.get("fee_rate") or 0.0)
    slippage_rate = float(execution.get("slippage_rate") or 0.0)
    bars = dataset.bars
    prepared_replay = _prepared or _prepare_path_replay(dataset)
    arrays = prepared_replay.arrays
    signal_by_id = {int(item["id"]): item for item in dataset.signals}
    equity = initial_equity
    global_peak = initial_equity
    records: list[dict[str, Any]] = []

    for episode in dataset.episodes:
        candidates = [
            {
                "execution_index": int(episode["entry_index"]),
                "signal_index": int(episode["signal_index"]),
                "signal_time": int(episode["signal_time"]),
                "execution_time": int(episode["entry_time"]),
                "event_id": int(episode["entry_signal_id"]),
            }
        ]
        candidates.extend(
            {
                "execution_index": int(signal_by_id[event_id]["execution_index"]),
                "signal_index": int(signal_by_id[event_id]["signal_index"]),
                "signal_time": int(signal_by_id[event_id]["signal_time"]),
                "execution_time": int(signal_by_id[event_id]["execution_time"]),
                "event_id": int(event_id),
            }
            for event_id in episode.get("same_side_signal_ids") or []
            if int(event_id) in signal_by_id
        )
        candidates.sort(key=lambda item: item["execution_index"])
        next_candidate = 0
        entries_used = 0
        last_exit_index = -1
        boundary_index = (
            int(episode["exit_index"])
            if episode.get("exit_index") is not None
            else len(bars)
        )

        while next_candidate < len(candidates):
            candidate = candidates[next_candidate]
            next_candidate += 1
            entry_index = int(candidate["execution_index"])
            if entry_index >= boundary_index:
                break
            if entries_used > 0:
                if entries_used > config.max_reentries:
                    break
                if entry_index <= last_exit_index + max(config.reentry_cooldown_bars, 0):
                    continue
            side = str(episode["side"])
            raw_entry = float(arrays["open"][entry_index])
            entry_price = _apply_slippage(raw_entry, side, slippage_rate)
            atr = _optional_float(
                bars[int(candidate["signal_index"])].get("atr")
            )
            risk_exit = _barrier_exit(
                arrays,
                entry_index=entry_index,
                end_exclusive=boundary_index,
                side=side,
                entry_price=entry_price,
                atr=atr,
                stop_unit=config.stop_unit,
                stop_value=config.stop_value,
                take_value=config.take_value,
                intrabar_policy=config.intrabar_policy,
            )
            if risk_exit is None:
                if episode["status"] != "closed":
                    break
                exit_index = int(episode["exit_index"])
                raw_exit = float(arrays["open"][exit_index])
                exit_reason = "reverse_signal"
                ambiguous = False
                mark_end_exclusive = exit_index
            else:
                exit_index, raw_exit, exit_reason, ambiguous = risk_exit
                mark_end_exclusive = exit_index

            notional = (
                equity * margin_ratio * leverage
                if margin_ratio > 0
                else margin_amount * leverage
            )
            qty = notional / entry_price if entry_price > 0 else 0.0
            if qty <= 0:
                raise RuntimeError("路径重放下单数量无效。")
            entry_fee = abs(qty * entry_price) * fee_rate
            exit_side = "sell" if side == "buy" else "buy"
            exit_price = _apply_slippage(raw_exit, exit_side, slippage_rate)
            exit_fee = abs(qty * exit_price) * fee_rate
            gross_pnl = _direction(side) * (exit_price - entry_price) * qty
            net_pnl = gross_pnl - entry_fee - exit_fee

            equity_before = equity
            global_peak, trade_drawdown = _mark_to_market_drawdown(
                arrays,
                entry_index=entry_index,
                end_exclusive=mark_end_exclusive,
                side=side,
                entry_price=entry_price,
                qty=qty,
                equity_before=equity,
                entry_fee=entry_fee,
                global_peak=global_peak,
            )
            equity += net_pnl
            equity_after = equity
            global_peak = max(global_peak, equity)
            realized_drawdown = (global_peak - equity) / global_peak if global_peak > 0 else 0.0
            split = str(episode.get("split") or "research")
            records.append(
                {
                    "episode_id": int(episode["id"]),
                    "entry_number": entries_used + 1,
                    "side": side,
                    "split": split,
                    "entry_index": entry_index,
                    "exit_index": exit_index,
                    "entry_time": int(candidate["execution_time"]),
                    "exit_time": int(bars[exit_index]["time"]),
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "exit_reason": exit_reason,
                    "qty": qty,
                    "equity_before": equity_before,
                    "equity_after": equity_after,
                    "entry_fee": entry_fee,
                    "exit_fee": exit_fee,
                    "gross_pnl": gross_pnl,
                    "fees": entry_fee + exit_fee,
                    "net_pnl": net_pnl,
                    "ambiguous_bar": ambiguous,
                    "path_drawdown_pct": max(trade_drawdown, realized_drawdown) * 100,
                }
            )
            entries_used += 1
            last_exit_index = exit_index
            if exit_reason == "reverse_signal" or entries_used > config.max_reentries:
                break

    has_time_boundaries = (
        (dataset.manifest.get("splits") or {}).get("validation_end")
        is not None
    )
    performance_records = (
        records
        if config.reveal_test or has_time_boundaries
        else [item for item in records if item["split"] != "test"]
    )
    visible_end_index = _path_visible_end_index(
        dataset,
        reveal_test=config.reveal_test,
        bar_times=prepared_replay.bar_times,
    )
    visible_records = _records_through_index(
        dataset,
        performance_records,
        end_index=visible_end_index,
        fee_rate=fee_rate,
        slippage_rate=slippage_rate,
    )
    visible_max_drawdown_pct = _drawdown_through_index(
        dataset,
        performance_records,
        end_index=visible_end_index,
        initial_equity=initial_equity,
        fee_rate=fee_rate,
        slippage_rate=slippage_rate,
        arrays=prepared_replay.arrays,
    )
    performance_times, performance_equity = _daily_mark_to_market_equity(
        dataset,
        performance_records,
        initial_equity=initial_equity,
        reveal_test=config.reveal_test,
        fee_rate=fee_rate,
        slippage_rate=slippage_rate,
        prepared=prepared_replay,
    )
    split_returns = _time_consistent_split_returns(
        dataset,
        performance_records,
        initial_equity=initial_equity,
        fee_rate=fee_rate,
        slippage_rate=slippage_rate,
        bar_times=prepared_replay.bar_times,
    )
    yearly_returns = _yearly_returns_from_equity(
        performance_times,
        performance_equity,
        initial_equity=initial_equity,
    )
    metrics = _replay_metrics(
        records,
        initial_equity,
        reveal_test=config.reveal_test,
        visible_records=visible_records,
        split_boundaries=dataset.manifest.get("splits") or {},
        split_returns=split_returns,
        yearly_returns=yearly_returns,
        max_drawdown_pct=visible_max_drawdown_pct,
    )
    metrics.update(
        performance_metrics(
            performance_equity,
            performance_times,
            max_drawdown_pct=visible_max_drawdown_pct,
        )
    )
    metrics["ambiguous_bar_count"] = sum(
        1 for item in visible_records if item["ambiguous_bar"]
    )
    metrics["reentry_trade_count"] = sum(
        1 for item in visible_records if int(item["entry_number"]) > 1
    )
    metrics["window_mark_count"] = sum(
        1 for item in visible_records if item["exit_reason"] == "window_mark"
    )
    if not config.reveal_test:
        for key in (
            "net_profit",
            "gross_profit",
            "gross_loss",
            "total_fees",
            "return_pct",
            "trade_count",
            "winning_trade_count",
            "losing_trade_count",
            "win_rate_pct",
            "profit_factor",
        ):
            metrics[f"test_{key}"] = None
    if not include_performance_series:
        strip_private_performance_fields(metrics)
    return metrics


def write_signal_path_artifacts(dataset: SignalPathDataset, output_dir: Path) -> list[dict[str, Any]]:
    dataset_dir = output_dir / "dataset"
    dataset_dir.mkdir(parents=True, exist_ok=True)
    _write_json(dataset_dir / "manifest.json", dataset.manifest)
    _write_gzip_json(dataset_dir / "internal.json.gz", dataset.as_dict())
    _write_gzip_json(
        dataset_dir / "replay.json.gz",
        _replay_cache_payload(dataset),
    )
    _write_csv(dataset_dir / "episodes.csv", dataset.episodes)
    _write_csv(dataset_dir / "signals.csv", dataset.signals)
    _write_gzip_csv(dataset_dir / "bars.csv.gz", dataset.bars)
    _write_llm_jsonl(dataset_dir / "llm_research_samples.jsonl.gz", dataset)
    readme = (
        "ChartBench signal-path research dataset\n\n"
        "- manifest.json: data identity, strategy semantics and split boundaries\n"
        "- episodes.csv: no-risk entry-to-qualified-opposite-signal episodes\n"
        "- signals.csv: every qualified entry, same-side and reverse signal, including exact signal-node indicator snapshots\n"
        "- bars.csv.gz: unique 5-minute OHLCV plus causal 5m DKX/Hull/UT/STC/ATR and aligned closed-1D Hull/STC features\n"
        "- llm_research_samples.jsonl.gz: research split only; every row contains bar_columns and feature_definitions; test data is excluded\n"
        "- internal.json.gz: complete internal research dataset\n"
        "- replay.json.gz: compact OHLC/ATR replay cache used internally by ChartBench\n"
        "\nDirection encoding: 1=up/buy/green, -1=down/sell/red, 0=unclear or not aligned.\n"
        "Daily features always use the latest fully closed 1D candle visible when the next 5m bar opens.\n"
    )
    (dataset_dir / "README.txt").write_text(readme, encoding="utf-8")
    return artifact_catalog(output_dir)


def write_path_matrix_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    flattened: list[dict[str, Any]] = []
    for row in rows:
        flat = {key: value for key, value in row.items() if key != "parameters"}
        flat.update(row.get("parameters") or {})
        flattened.append(flat)
    _write_csv(path, flattened)


def load_signal_path_dataset(path: Path) -> SignalPathDataset:
    source_path = path
    replay_path = path.with_name("replay.json.gz")
    if path.name == "internal.json.gz" and replay_path.is_file():
        source_path = replay_path
    with gzip.open(source_path, "rt", encoding="utf-8") as file:
        payload = json.load(file)
    dataset = SignalPathDataset.from_dict(payload)
    if (
        path.name == "internal.json.gz"
        and source_path == path
        and not replay_path.exists()
    ):
        _write_gzip_json(replay_path, _replay_cache_payload(dataset))
    return dataset


def _replay_cache_payload(dataset: SignalPathDataset) -> dict[str, Any]:
    return {
        "manifest": dataset.manifest,
        "bars": [
            {
                key: item.get(key)
                for key in PATH_REPLAY_BAR_COLUMNS
            }
            for item in dataset.bars
        ],
        "signals": dataset.signals,
        "episodes": dataset.episodes,
    }


def artifact_catalog(run_dir: Path) -> list[dict[str, Any]]:
    labels = {
        "dataset/manifest.json": "数据清单",
        "dataset/episodes.csv": "开平仓 Episode",
        "dataset/signals.csv": "有效信号账本",
        "dataset/bars.csv.gz": "5m K 线与指标",
        "dataset/llm_research_samples.jsonl.gz": "大模型研究样本",
        "dataset/README.txt": "数据说明",
        "dataset_reference.json": "复用基准数据说明",
        "matrix.csv": "矩阵完整结果",
    }
    artifacts: list[dict[str, Any]] = []
    for relative, label in labels.items():
        path = run_dir / relative
        if not path.is_file():
            continue
        artifacts.append(
            {
                "name": relative,
                "label": label,
                "size_bytes": path.stat().st_size,
            }
        )
    return artifacts


def _research_bars(study: PreparedBacktestStudy, atr_period: int) -> list[dict[str, Any]]:
    snapshot = study.full_snapshot
    candles = list(snapshot.get("candles") or [])
    volume_by_time = {
        int(item["time"]): float(item.get("value") or 0.0)
        for item in snapshot.get("volume") or []
    }
    labels = snapshot.get("time_labels") or {}
    frame = pd.DataFrame(candles)
    previous_close = frame["close"].shift(1)
    true_range = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - previous_close).abs(),
            (frame["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = true_range.rolling(max(int(atr_period), 1), min_periods=max(int(atr_period), 1)).mean()
    feature_ids = {
        "stc.stc": "stc",
        "merged_dkx_hull_ut.mhull_up": "mhull_up",
        "merged_dkx_hull_ut.shull_up": "shull_up",
        "merged_dkx_hull_ut.mhull_down": "mhull_down",
        "merged_dkx_hull_ut.shull_down": "shull_down",
    }
    features = _series_features_by_time(snapshot, feature_ids)
    custom_features = _indicator_feature_table(snapshot, "merged_dkx_hull_ut")
    custom_times = custom_features.get("time") or []
    custom_index_by_time = (
        {}
        if len(custom_times) == len(candles)
        and all(int(candle["time"]) == custom_times[index] for index, candle in enumerate(candles))
        else {timestamp: index for index, timestamp in enumerate(custom_times)}
    )

    bars: list[dict[str, Any]] = []
    previous_stc: float | None = None
    for index, candle in enumerate(candles):
        timestamp = int(candle["time"])
        item = {
            "index": index,
            "time": timestamp,
            "time_label": str(labels.get(str(timestamp)) or ""),
            "open": float(candle["open"]),
            "high": float(candle["high"]),
            "low": float(candle["low"]),
            "close": float(candle["close"]),
            "volume": volume_by_time.get(timestamp, 0.0),
            "atr": _finite_or_none(atr.iloc[index]),
            "stc": None,
            "stc_delta": None,
            "stc_direction": None,
            "stc_zone": None,
            "dkx_w": None,
            "dkx_d": None,
            "dkx_k": None,
            "dkx_spread": None,
            "dkx_buy": 0,
            "dkx_sell": 0,
            "marker_buy": 0,
            "marker_sell": 0,
            "mhull": None,
            "shull": None,
            "hull_direction": None,
            "mhull_up": None,
            "shull_up": None,
            "mhull_down": None,
            "shull_down": None,
            "ut_atr": None,
            "ut_n_loss": None,
            "ut_source": None,
            "ut_trailing_stop": None,
            "ut_distance": None,
            "ut_position": 0,
            "ut_buy": 0,
            "ut_sell": 0,
            "d1_bar_time": None,
            "d1_stc": None,
            "d1_stc_delta": None,
            "d1_stc_direction": None,
            "d1_mhull": None,
            "d1_shull": None,
            "d1_hull_direction": None,
            "d1_hull_stc_aligned": None,
            "d1_trend": None,
        }
        item.update(features.get(timestamp) or {})
        custom_index = (
            index
            if index < len(custom_times) and custom_times[index] == timestamp
            else custom_index_by_time.get(timestamp)
        )
        if custom_index is not None:
            for column in CUSTOM_INDICATOR_FEATURE_COLUMNS:
                values = custom_features.get(column) or []
                if custom_index >= len(values):
                    continue
                value = _feature_value(values[custom_index], column)
                if value is not None:
                    item[column] = value
        stc_value = _optional_float(item.get("stc"))
        if stc_value is not None:
            item["stc_delta"] = (
                stc_value - previous_stc
                if previous_stc is not None
                else None
            )
            item["stc_zone"] = -1 if stc_value < 25 else 1 if stc_value > 75 else 0
            previous_stc = stc_value
        item["marker_buy"] = int(bool(item.get("dkx_buy")) or bool(item.get("ut_buy")))
        item["marker_sell"] = int(bool(item.get("dkx_sell")) or bool(item.get("ut_sell")))
        bars.append(item)
    _attach_closed_daily_features(
        bars,
        study.htf_snapshot,
        low_duration_seconds=int(snapshot.get("duration_seconds") or 300),
    )
    return bars


def _series_features_by_time(
    snapshot: dict[str, Any],
    feature_ids: dict[str, str],
) -> dict[int, dict[str, Any]]:
    features: dict[int, dict[str, Any]] = {}
    for indicator in snapshot.get("indicators") or []:
        indicator_id = str(indicator.get("id") or "")
        for series in indicator.get("series") or []:
            series_id = str(series.get("id") or "")
            target = feature_ids.get(f"{indicator_id}.{series_id}")
            if target is None:
                continue
            for point in series.get("data") or []:
                value = _optional_float(point.get("value"))
                if value is None:
                    continue
                timestamp = int(point.get("time") or 0)
                item = features.setdefault(timestamp, {})
                item[target] = value
                if target == "stc":
                    color = str(point.get("signal_color") or point.get("color") or "")
                    item["stc_direction"] = (
                        1 if is_green_color(color) else -1 if is_red_color(color) else 0
                    )
    return features


def _indicator_feature_table(
    snapshot: dict[str, Any],
    indicator_id: str,
) -> dict[str, list[Any]]:
    for indicator in snapshot.get("indicators") or []:
        if str(indicator.get("id") or "") != indicator_id:
            continue
        return {
            key: values
            for key, values in (indicator.get("features") or {}).items()
            if isinstance(values, list)
        }
    return {}


def _feature_value(value: Any, column: str) -> float | int | None:
    parsed = _optional_float(value)
    if parsed is None:
        return None
    if column in INTEGER_FEATURE_COLUMNS:
        return int(round(parsed))
    return parsed


def _daily_indicator_rows(snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(snapshot, dict):
        return []
    candles = list(snapshot.get("candles") or [])
    if not candles:
        return []
    visual = _series_features_by_time(snapshot, {"stc.stc": "stc"})
    custom = _indicator_feature_table(snapshot, "merged_dkx_hull_ut")
    custom_times = custom.get("time") or []
    custom_index_by_time = (
        {}
        if len(custom_times) == len(candles)
        and all(int(candle["time"]) == custom_times[index] for index, candle in enumerate(candles))
        else {timestamp: index for index, timestamp in enumerate(custom_times)}
    )
    rows: list[dict[str, Any]] = []
    previous_stc: float | None = None
    for index, candle in enumerate(candles):
        timestamp = int(candle["time"])
        row: dict[str, Any] = {
            "time": timestamp,
            "stc": None,
            "stc_delta": None,
            "stc_direction": None,
            "mhull": None,
            "shull": None,
            "hull_direction": None,
        }
        row.update(visual.get(timestamp) or {})
        custom_index = (
            index
            if index < len(custom_times) and custom_times[index] == timestamp
            else custom_index_by_time.get(timestamp)
        )
        if custom_index is not None:
            for column in ("mhull", "shull", "hull_direction"):
                values = custom.get(column) or []
                if custom_index < len(values):
                    row[column] = _feature_value(values[custom_index], column)
        stc_value = _optional_float(row.get("stc"))
        if stc_value is not None:
            row["stc_delta"] = (
                stc_value - previous_stc
                if previous_stc is not None
                else None
            )
            previous_stc = stc_value
        rows.append(row)
    return rows


def _attach_closed_daily_features(
    bars: list[dict[str, Any]],
    daily_snapshot: dict[str, Any] | None,
    *,
    low_duration_seconds: int,
) -> None:
    daily_rows = _daily_indicator_rows(daily_snapshot)
    if not daily_rows:
        return
    daily_times = [int(item["time"]) for item in daily_rows]
    for index, bar in enumerate(bars):
        # The strategy confirms bar[index] when bar[index + 1] opens. At that
        # moment the newest daily candle is still forming, so -2 is the latest
        # fully closed 1D candle. This mirrors BacktestSnapshotSlicer +
        # SignalEvaluator(use_closed_bar=True) exactly.
        visible_time = (
            int(bars[index + 1]["time"])
            if index + 1 < len(bars)
            else int(bar["time"]) + max(int(low_duration_seconds), 1)
        )
        daily_index = bisect_right(daily_times, visible_time) - 2
        if daily_index < 0:
            continue
        daily = daily_rows[daily_index]
        hull_direction = _feature_value(daily.get("hull_direction"), "d1_hull_direction")
        stc_direction = _feature_value(daily.get("stc_direction"), "d1_stc_direction")
        aligned = bool(
            hull_direction in {-1, 1}
            and stc_direction in {-1, 1}
            and hull_direction == stc_direction
        )
        bar.update(
            {
                "d1_bar_time": int(daily["time"]),
                "d1_stc": _optional_float(daily.get("stc")),
                "d1_stc_delta": _optional_float(daily.get("stc_delta")),
                "d1_stc_direction": stc_direction,
                "d1_mhull": _optional_float(daily.get("mhull")),
                "d1_shull": _optional_float(daily.get("shull")),
                "d1_hull_direction": hull_direction,
                "d1_hull_stc_aligned": int(aligned),
                "d1_trend": int(hull_direction) if aligned else 0,
            }
        )


def _new_episode(identifier: int, event: dict[str, Any], context_bars: int) -> dict[str, Any]:
    entry_index = int(event["execution_index"])
    return {
        "id": identifier,
        "side": event["side"],
        "status": "open",
        "split": "",
        "entry_signal_id": int(event["id"]),
        "signal_index": int(event["signal_index"]),
        "signal_time": int(event["signal_time"]),
        "signal_time_label": event["signal_time_label"],
        "entry_index": entry_index,
        "entry_time": int(event["execution_time"]),
        "entry_time_label": event["execution_time_label"],
        "entry_price": float(event["execution_open"]),
        "entry_atr": None,
        "exit_signal_id": None,
        "exit_signal_index": None,
        "exit_signal_time": None,
        "exit_signal_time_label": "",
        "exit_index": None,
        "exit_time": None,
        "exit_time_label": "",
        "exit_price": None,
        "exit_reason": "",
        "context_start_index": max(entry_index - max(int(context_bars), 1), 0),
        "path_end_index": entry_index,
        "holding_bars": 0,
        "same_side_signal_ids": [],
        "daily_trend": event.get("daily_trend"),
        "daily_bar_time": event.get("daily_bar_time"),
        "daily_bar_time_label": event.get("daily_bar_time_label"),
        "daily_segment_start_time": event.get("daily_segment_start_time"),
        "daily_segment_start_time_label": event.get("daily_segment_start_time_label"),
        "mfe_points": None,
        "mae_points": None,
        "mfe_pct": None,
        "mae_pct": None,
        "mfe_atr": None,
        "mae_atr": None,
        "gross_points": None,
        "gross_return_pct": None,
    }


def _finish_episode(episode: dict[str, Any], bars: list[dict[str, Any]], reverse: dict[str, Any]) -> None:
    exit_index = int(reverse["execution_index"])
    episode.update(
        status="closed",
        exit_signal_id=int(reverse["id"]),
        exit_signal_index=int(reverse["signal_index"]),
        exit_signal_time=int(reverse["signal_time"]),
        exit_signal_time_label=reverse["signal_time_label"],
        exit_index=exit_index,
        exit_time=int(reverse["execution_time"]),
        exit_time_label=reverse["execution_time_label"],
        exit_price=float(reverse["execution_open"]),
        exit_reason="qualified_reverse_signal",
        path_end_index=max(exit_index - 1, int(episode["entry_index"])),
        holding_bars=max(exit_index - int(episode["entry_index"]), 0),
    )
    _attach_excursions(episode, bars)


def _finish_open_episode(episode: dict[str, Any], bars: list[dict[str, Any]]) -> None:
    episode.update(
        status="open",
        path_end_index=len(bars) - 1,
        holding_bars=max(len(bars) - int(episode["entry_index"]), 0),
    )
    _attach_excursions(episode, bars)


def _attach_excursions(episode: dict[str, Any], bars: list[dict[str, Any]]) -> None:
    start = int(episode["entry_index"])
    end = int(episode["path_end_index"]) + 1
    path = bars[start:end]
    if not path:
        return
    entry = float(episode["entry_price"])
    direction = _direction(str(episode["side"]))
    favorable = max(direction * (float(item["high"] if direction > 0 else item["low"]) - entry) for item in path)
    adverse = min(direction * (float(item["low"] if direction > 0 else item["high"]) - entry) for item in path)
    gross: float | None = None
    if episode["status"] == "closed":
        gross = direction * (float(episode["exit_price"]) - entry)
        favorable = max(favorable, gross)
        adverse = min(adverse, gross)
    atr = _optional_float(bars[int(episode["signal_index"])].get("atr"))
    episode["entry_atr"] = atr
    episode["mfe_points"] = favorable
    episode["mae_points"] = adverse
    episode["mfe_pct"] = favorable / entry * 100 if entry else None
    episode["mae_pct"] = adverse / entry * 100 if entry else None
    episode["mfe_atr"] = favorable / atr if atr and atr > 0 else None
    episode["mae_atr"] = adverse / atr if atr and atr > 0 else None
    if gross is not None:
        episode["gross_points"] = gross
        episode["gross_return_pct"] = gross / entry * 100 if entry else None


def _barrier_exit(
    arrays: dict[str, np.ndarray],
    *,
    entry_index: int,
    end_exclusive: int,
    side: str,
    entry_price: float,
    atr: float | None,
    stop_unit: str,
    stop_value: float | None,
    take_value: float | None,
    intrabar_policy: str,
) -> tuple[int, float, str, bool] | None:
    if stop_value is None and take_value is None:
        return None
    distance_base = (
        entry_price / 100.0
        if stop_unit == "percent"
        else atr
        if stop_unit == "atr"
        else 1.0
    )
    if distance_base is None or distance_base <= 0:
        return None
    direction = _direction(side)
    stop_price = (
        entry_price - direction * distance_base * float(stop_value)
        if stop_value is not None
        else None
    )
    take_price = (
        entry_price + direction * distance_base * float(take_value)
        if take_value is not None
        else None
    )
    lows = arrays["low"][entry_index:end_exclusive]
    highs = arrays["high"][entry_index:end_exclusive]
    if not len(lows):
        return None
    if side == "buy":
        stop_hits = lows <= stop_price if stop_price is not None else np.zeros(len(lows), dtype=bool)
        take_hits = highs >= take_price if take_price is not None else np.zeros(len(lows), dtype=bool)
    else:
        stop_hits = highs >= stop_price if stop_price is not None else np.zeros(len(lows), dtype=bool)
        take_hits = lows <= take_price if take_price is not None else np.zeros(len(lows), dtype=bool)
    stop_positions = np.flatnonzero(stop_hits)
    take_positions = np.flatnonzero(take_hits)
    stop_at = int(stop_positions[0]) if len(stop_positions) else None
    take_at = int(take_positions[0]) if len(take_positions) else None
    if stop_at is None and take_at is None:
        return None
    ambiguous = stop_at is not None and take_at is not None and stop_at == take_at
    if ambiguous:
        index = entry_index + int(stop_at)
        raw_open = float(arrays["open"][index])
        stop_gap = (
            raw_open <= float(stop_price)
            if side == "buy"
            else raw_open >= float(stop_price)
        ) if stop_price is not None else False
        take_gap = (
            raw_open >= float(take_price)
            if side == "buy"
            else raw_open <= float(take_price)
        ) if take_price is not None else False
        if stop_gap:
            return index, raw_open, "stop_loss", False
        if take_gap:
            assert take_price is not None
            return index, float(take_price), "take_profit", False
    choose_stop = (
        take_at is None
        or (stop_at is not None and stop_at < take_at)
        or (ambiguous and intrabar_policy == "stop_first")
    )
    relative_index = int(stop_at if choose_stop else take_at)
    index = entry_index + relative_index
    raw_open = float(arrays["open"][index])
    if choose_stop:
        assert stop_price is not None
        gap_worse = raw_open < stop_price if side == "buy" else raw_open > stop_price
        return index, raw_open if gap_worse else float(stop_price), "stop_loss", ambiguous
    assert take_price is not None
    return index, float(take_price), "take_profit", ambiguous


def _mark_to_market_drawdown(
    arrays: dict[str, np.ndarray],
    *,
    entry_index: int,
    end_exclusive: int,
    side: str,
    entry_price: float,
    qty: float,
    equity_before: float,
    entry_fee: float,
    global_peak: float,
) -> tuple[float, float]:
    if end_exclusive <= entry_index:
        return global_peak, 0.0
    direction = _direction(side)
    favorable_prices = (
        arrays["high"][entry_index:end_exclusive]
        if side == "buy"
        else arrays["low"][entry_index:end_exclusive]
    )
    adverse_prices = (
        arrays["low"][entry_index:end_exclusive]
        if side == "buy"
        else arrays["high"][entry_index:end_exclusive]
    )
    base = equity_before - entry_fee
    favorable_equity = base + direction * (favorable_prices - entry_price) * qty
    adverse_equity = base + direction * (adverse_prices - entry_price) * qty
    peak_series = np.maximum.accumulate(np.maximum(favorable_equity, global_peak))
    safe_peaks = np.maximum(peak_series, 1e-12)
    drawdowns = (safe_peaks - adverse_equity) / safe_peaks
    return max(global_peak, float(np.max(favorable_equity))), max(float(np.max(drawdowns)), 0.0)


def _path_visible_end_index(
    dataset: SignalPathDataset,
    *,
    reveal_test: bool,
    bar_times: list[int] | None = None,
) -> int:
    if not dataset.bars:
        return -1
    if reveal_test:
        return len(dataset.bars) - 1
    validation_end = (dataset.manifest.get("splits") or {}).get("validation_end")
    if validation_end is None:
        return len(dataset.bars) - 1
    resolved_bar_times = (
        bar_times
        if bar_times is not None
        else [int(item["time"]) for item in dataset.bars]
    )
    return min(
        max(bisect_right(resolved_bar_times, int(validation_end)) - 1, 0),
        len(dataset.bars) - 1,
    )


def _records_through_index(
    dataset: SignalPathDataset,
    records: list[dict[str, Any]],
    *,
    end_index: int,
    fee_rate: float,
    slippage_rate: float,
) -> list[dict[str, Any]]:
    """Close a boundary-crossing position at the last visible close."""

    if end_index < 0:
        return []
    visible: list[dict[str, Any]] = []
    for record in sorted(records, key=lambda item: int(item["entry_index"])):
        if int(record["entry_index"]) > end_index:
            break
        if int(record["exit_index"]) <= end_index:
            visible.append(record)
            continue
        visible.append(
            _marked_record(
                dataset,
                record,
                end_index=end_index,
                fee_rate=fee_rate,
                slippage_rate=slippage_rate,
            )
        )
        break
    return visible


def _marked_record(
    dataset: SignalPathDataset,
    record: dict[str, Any],
    *,
    end_index: int,
    fee_rate: float,
    slippage_rate: float,
) -> dict[str, Any]:
    side = str(record["side"])
    exit_side = "sell" if side == "buy" else "buy"
    mark_price = _apply_slippage(
        float(dataset.bars[end_index]["close"]),
        exit_side,
        slippage_rate,
    )
    qty = float(record["qty"])
    exit_fee = abs(qty * mark_price) * fee_rate
    gross_pnl = (
        _direction(side)
        * (mark_price - float(record["entry_price"]))
        * qty
    )
    net_pnl = gross_pnl - float(record["entry_fee"]) - exit_fee
    marked = dict(record)
    marked.update(
        exit_index=end_index,
        exit_time=(
            int(dataset.bars[end_index]["time"])
            + _bar_duration_seconds(dataset.bars)
        ),
        exit_price=mark_price,
        exit_reason="window_mark",
        gross_pnl=gross_pnl,
        exit_fee=exit_fee,
        fees=float(record["entry_fee"]) + exit_fee,
        net_pnl=net_pnl,
        equity_after=float(record["equity_before"]) + net_pnl,
        ambiguous_bar=False,
    )
    return marked


def _drawdown_through_index(
    dataset: SignalPathDataset,
    records: list[dict[str, Any]],
    *,
    end_index: int,
    initial_equity: float,
    fee_rate: float,
    slippage_rate: float,
    arrays: dict[str, np.ndarray] | None = None,
) -> float:
    if end_index < 0 or not records:
        return 0.0
    resolved_arrays = arrays or {
        key: np.asarray(
            [float(item[key]) for item in dataset.bars],
            dtype="float64",
        )
        for key in ("open", "high", "low", "close")
    }
    global_peak = float(initial_equity)
    maximum = 0.0
    for record in sorted(records, key=lambda item: int(item["entry_index"])):
        entry_index = int(record["entry_index"])
        if entry_index > end_index:
            break
        actual_exit = int(record["exit_index"])
        crosses_boundary = actual_exit > end_index
        end_exclusive = end_index + 1 if crosses_boundary else actual_exit
        global_peak, path_drawdown = _mark_to_market_drawdown(
            resolved_arrays,
            entry_index=entry_index,
            end_exclusive=end_exclusive,
            side=str(record["side"]),
            entry_price=float(record["entry_price"]),
            qty=float(record["qty"]),
            equity_before=float(record["equity_before"]),
            entry_fee=float(record["entry_fee"]),
            global_peak=global_peak,
        )
        if crosses_boundary:
            marked = _marked_record(
                dataset,
                record,
                end_index=end_index,
                fee_rate=fee_rate,
                slippage_rate=slippage_rate,
            )
            marked_equity = float(marked["equity_after"])
            realized_drawdown = (
                (global_peak - marked_equity) / global_peak
                if global_peak > 0
                else 0.0
            )
            maximum = max(maximum, path_drawdown, realized_drawdown)
            break
        equity_after = float(record["equity_after"])
        global_peak = max(global_peak, equity_after)
        realized_drawdown = (
            (global_peak - equity_after) / global_peak
            if global_peak > 0
            else 0.0
        )
        maximum = max(maximum, path_drawdown, realized_drawdown)
    return maximum * 100.0


def _equity_at_index(
    dataset: SignalPathDataset,
    records: list[dict[str, Any]],
    *,
    index: int,
    initial_equity: float,
    fee_rate: float,
    slippage_rate: float,
) -> float:
    if index < 0:
        return float(initial_equity)
    realized = float(initial_equity)
    for record in sorted(records, key=lambda item: int(item["entry_index"])):
        if int(record["entry_index"]) > index:
            break
        if int(record["exit_index"]) <= index:
            realized = float(record["equity_after"])
            continue
        return float(
            _marked_record(
                dataset,
                record,
                end_index=index,
                fee_rate=fee_rate,
                slippage_rate=slippage_rate,
            )["equity_after"]
        )
    return realized


def _time_consistent_split_returns(
    dataset: SignalPathDataset,
    records: list[dict[str, Any]],
    *,
    initial_equity: float,
    fee_rate: float,
    slippage_rate: float,
    bar_times: list[int] | None = None,
) -> dict[str, float] | None:
    splits = dataset.manifest.get("splits") or {}
    if (
        splits.get("research_end") is None
        or splits.get("validation_end") is None
        or not dataset.bars
    ):
        return None
    resolved_bar_times = (
        bar_times
        if bar_times is not None
        else [int(item["time"]) for item in dataset.bars]
    )
    research_index = max(
        bisect_right(resolved_bar_times, int(splits["research_end"])) - 1,
        0,
    )
    validation_index = max(
        bisect_right(resolved_bar_times, int(splits["validation_end"])) - 1,
        research_index,
    )
    final_index = len(dataset.bars) - 1
    research_equity = _equity_at_index(
        dataset,
        records,
        index=research_index,
        initial_equity=initial_equity,
        fee_rate=fee_rate,
        slippage_rate=slippage_rate,
    )
    validation_equity = _equity_at_index(
        dataset,
        records,
        index=validation_index,
        initial_equity=initial_equity,
        fee_rate=fee_rate,
        slippage_rate=slippage_rate,
    )
    final_equity = _equity_at_index(
        dataset,
        records,
        index=final_index,
        initial_equity=initial_equity,
        fee_rate=fee_rate,
        slippage_rate=slippage_rate,
    )
    return {
        "research": research_equity - initial_equity,
        "validation": validation_equity - research_equity,
        "test": final_equity - validation_equity,
    }


def _yearly_returns_from_equity(
    timestamps: list[int],
    equity_values: list[float],
    *,
    initial_equity: float,
) -> dict[str, float]:
    yearly_pnl: dict[str, float] = {}
    for timestamp, previous, current in zip(
        timestamps[1:],
        equity_values[:-1],
        equity_values[1:],
    ):
        year = (
            pd.Timestamp(int(timestamp), unit="s", tz="UTC")
            .tz_convert("Asia/Shanghai")
            .strftime("%Y")
        )
        yearly_pnl[year] = yearly_pnl.get(year, 0.0) + current - previous
    return {
        year: pnl / initial_equity * 100.0
        for year, pnl in sorted(yearly_pnl.items())
    }


def _bar_duration_seconds(bars: list[dict[str, Any]]) -> int:
    if len(bars) < 2:
        return 300
    for left, right in zip(bars, bars[1:]):
        step = int(right["time"]) - int(left["time"])
        if step > 0:
            return step
    return 300


def _record_performance_split(
    record: dict[str, Any],
    boundaries: dict[str, Any],
) -> str:
    research_end = boundaries.get("research_end")
    validation_end = boundaries.get("validation_end")
    if research_end is None or validation_end is None:
        return str(record.get("split") or "research")
    exit_time = int(record["exit_time"])
    if exit_time <= int(research_end):
        return "research"
    if exit_time <= int(validation_end):
        return "validation"
    return "test"


def _daily_mark_to_market_equity(
    dataset: SignalPathDataset,
    records: list[dict[str, Any]],
    *,
    initial_equity: float,
    reveal_test: bool,
    fee_rate: float,
    slippage_rate: float,
    prepared: PreparedPathReplay | None = None,
) -> tuple[list[int], list[float]]:
    """Return a UTC day-end liquidation-value series.

    The visible series stops at the validation boundary until the user reveals
    the final test. A trade that crosses that boundary is valued at the last
    visible close instead of importing its later exit result.
    """

    if not dataset.bars:
        return [], []
    prepared_replay = prepared or _prepare_path_replay(dataset)
    bar_times = prepared_replay.bar_times
    visible_end_index = _path_visible_end_index(
        dataset,
        reveal_test=reveal_test,
        bar_times=bar_times,
    )
    bar_duration = prepared_replay.bar_duration

    day_end_indices: list[int] = []
    previous_day: int | None = None
    previous_index = 0
    for index in range(visible_end_index + 1):
        close_time = bar_times[index] + bar_duration
        day = (close_time - 1) // 86_400
        if previous_day is not None and day != previous_day:
            day_end_indices.append(previous_index)
        previous_day = day
        previous_index = index
    day_end_indices.append(visible_end_index)

    ordered_records = sorted(
        records,
        key=lambda item: (
            int(item["entry_index"]),
            int(item["exit_index"]),
        ),
    )
    close_prices = prepared_replay.arrays["close"]
    output_times = [bar_times[0]]
    output_equity = [float(initial_equity)]
    record_index = 0
    realized_equity = float(initial_equity)

    for day_end_index in day_end_indices:
        marked_equity = realized_equity
        while record_index < len(ordered_records):
            record = ordered_records[record_index]
            entry_index = int(record["entry_index"])
            exit_index = int(record["exit_index"])
            if entry_index > day_end_index:
                break
            if exit_index <= day_end_index:
                realized_equity = float(record["equity_after"])
                marked_equity = realized_equity
                record_index += 1
                continue

            side = str(record["side"])
            direction = _direction(side)
            raw_mark = float(close_prices[day_end_index])
            exit_side = "sell" if side == "buy" else "buy"
            mark_price = _apply_slippage(raw_mark, exit_side, slippage_rate)
            estimated_exit_fee = abs(float(record["qty"]) * mark_price) * fee_rate
            marked_equity = (
                float(record["equity_before"])
                - float(record["entry_fee"])
                + direction
                * (mark_price - float(record["entry_price"]))
                * float(record["qty"])
                - estimated_exit_fee
            )
            break

        output_times.append(bar_times[day_end_index] + bar_duration)
        output_equity.append(marked_equity)

    return output_times, output_equity


def _replay_metrics(
    records: list[dict[str, Any]],
    initial_equity: float,
    *,
    reveal_test: bool,
    visible_records: list[dict[str, Any]] | None = None,
    split_boundaries: dict[str, Any] | None = None,
    split_returns: dict[str, float] | None = None,
    yearly_returns: dict[str, float] | None = None,
    max_drawdown_pct: float | None = None,
) -> dict[str, Any]:
    visible = (
        visible_records
        if visible_records is not None
        else records
        if reveal_test
        else [item for item in records if item["split"] != "test"]
    )
    wins = [item for item in visible if float(item["net_pnl"]) > 0]
    losses = [item for item in visible if float(item["net_pnl"]) < 0]
    breakevens = [item for item in visible if float(item["net_pnl"]) == 0]
    gross_profit = sum(float(item["net_pnl"]) for item in wins)
    gross_loss = abs(sum(float(item["net_pnl"]) for item in losses))
    visible_net_profit = sum(float(item["net_pnl"]) for item in visible)
    total_fees = sum(float(item["fees"]) for item in visible)
    average_win = gross_profit / len(wins) if wins else 0.0
    average_loss = -gross_loss / len(losses) if losses else 0.0
    holding_bars = [
        max(int(item["exit_index"]) - int(item["entry_index"]), 0)
        for item in visible
    ]
    visible_max_drawdown_pct = (
        max(float(max_drawdown_pct), 0.0)
        if max_drawdown_pct is not None
        else max(
            (float(item.get("path_drawdown_pct") or 0.0) for item in visible),
            default=0.0,
        )
    )
    if yearly_returns is None:
        yearly_pnl: dict[str, float] = {}
        for item in visible:
            year = (
                pd.Timestamp(int(item["entry_time"]), unit="s", tz="UTC")
                .tz_convert("Asia/Shanghai")
                .strftime("%Y")
            )
            yearly_pnl[year] = yearly_pnl.get(year, 0.0) + float(item["net_pnl"])
        yearly_returns = {
            year: pnl / initial_equity * 100
            for year, pnl in sorted(yearly_pnl.items())
        }
    positive_years = sum(1 for value in yearly_returns.values() if value > 0)
    metrics: dict[str, Any] = {
        "initial_equity": initial_equity,
        "final_equity": initial_equity + visible_net_profit,
        "gross_pnl": sum(float(item["gross_pnl"]) for item in visible),
        "net_profit": visible_net_profit,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "return_pct": visible_net_profit / initial_equity * 100,
        "max_drawdown_pct": visible_max_drawdown_pct,
        "trade_count": len(visible),
        "winning_trade_count": len(wins),
        "losing_trade_count": len(losses),
        "breakeven_trade_count": len(breakevens),
        "win_rate_pct": len(wins) / len(visible) * 100 if visible else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0),
        "average_trade_pnl": visible_net_profit / len(visible) if visible else 0.0,
        "average_win": average_win,
        "average_loss": average_loss,
        "payoff_ratio": (
            average_win / abs(average_loss)
            if average_loss < 0
            else 999.0
            if average_win > 0
            else 0.0
        ),
        "largest_win": max((float(item["net_pnl"]) for item in wins), default=0.0),
        "largest_loss": min((float(item["net_pnl"]) for item in losses), default=0.0),
        "average_holding_bars": (
            sum(holding_bars) / len(holding_bars)
            if holding_bars
            else 0.0
        ),
        "total_fees": total_fees,
        "long_trade_count": sum(1 for item in visible if item["side"] == "buy"),
        "short_trade_count": sum(1 for item in visible if item["side"] == "sell"),
        "stop_loss_count": sum(1 for item in visible if item["exit_reason"] == "stop_loss"),
        "take_profit_count": sum(1 for item in visible if item["exit_reason"] == "take_profit"),
        "reverse_exit_count": sum(1 for item in visible if item["exit_reason"] == "reverse_signal"),
        "visible_window": "all" if reveal_test else "research_and_validation",
        "yearly_returns_pct": yearly_returns,
        "positive_year_ratio": (
            positive_years / len(yearly_returns) if yearly_returns else 0.0
        ),
        "worst_year_return_pct": min(yearly_returns.values(), default=0.0),
    }
    boundaries = split_boundaries or {}
    for split in ("research", "validation", "test"):
        subset = [
            item
            for item in records
            if _record_performance_split(item, boundaries) == split
        ]
        pnl = (
            float(split_returns[split])
            if split_returns is not None
            else sum(float(item["net_pnl"]) for item in subset)
        )
        split_wins = sum(1 for item in subset if float(item["net_pnl"]) > 0)
        split_losses = sum(1 for item in subset if float(item["net_pnl"]) < 0)
        split_profit = sum(float(item["net_pnl"]) for item in subset if float(item["net_pnl"]) > 0)
        split_loss = abs(sum(float(item["net_pnl"]) for item in subset if float(item["net_pnl"]) < 0))
        metrics[f"{split}_net_profit"] = pnl
        metrics[f"{split}_gross_profit"] = split_profit
        metrics[f"{split}_gross_loss"] = split_loss
        metrics[f"{split}_total_fees"] = sum(float(item["fees"]) for item in subset)
        metrics[f"{split}_return_pct"] = pnl / initial_equity * 100
        metrics[f"{split}_trade_count"] = len(subset)
        metrics[f"{split}_winning_trade_count"] = split_wins
        metrics[f"{split}_losing_trade_count"] = split_losses
        metrics[f"{split}_win_rate_pct"] = split_wins / len(subset) * 100 if subset else 0.0
        metrics[f"{split}_profit_factor"] = (
            split_profit / split_loss
            if split_loss > 0
            else 999.0
            if split_profit > 0
            else 0.0
        )
    return metrics


def _comparison_fields(
    candidate: dict[str, Any],
    baseline: dict[str, Any],
) -> dict[str, float | int]:
    """Return flat fields so the UI and matrix CSV share one comparison contract."""

    float_metrics = {
        "annualized_volatility_pct",
        "cagr_pct",
        "calmar_ratio",
        "daily_expected_shortfall_95_pct",
        "daily_max_drawdown_pct",
        "daily_var_95_pct",
        "net_profit",
        "gross_profit",
        "gross_loss",
        "martin_ratio",
        "return_pct",
        "research_return_pct",
        "validation_return_pct",
        "max_drawdown_pct",
        "max_drawdown_duration_days",
        "omega_ratio",
        "profit_factor",
        "psr_benchmark_pct",
        "psr_zero_pct",
        "return_excess_kurtosis",
        "return_skewness",
        "sharpe_ratio",
        "sortino_ratio",
        "ulcer_index_pct",
        "win_rate_pct",
        "total_fees",
        "average_trade_pnl",
    }
    count_metrics = {
        "trade_count",
        "winning_trade_count",
        "losing_trade_count",
        "stop_loss_count",
        "take_profit_count",
        "reverse_exit_count",
        "reentry_trade_count",
        "performance_observation_count",
    }
    comparison: dict[str, float | int] = {}
    for key in sorted(float_metrics):
        baseline_value = float(baseline.get(key) or 0.0)
        candidate_value = float(candidate.get(key) or 0.0)
        comparison[f"baseline_{key}"] = baseline_value
        comparison[f"{key}_delta_vs_baseline"] = candidate_value - baseline_value
    for key in sorted(count_metrics):
        baseline_value = int(baseline.get(key) or 0)
        candidate_value = int(candidate.get(key) or 0)
        comparison[f"baseline_{key}"] = baseline_value
        comparison[f"{key}_delta_vs_baseline"] = candidate_value - baseline_value
    return comparison


def _selection_score(metrics: dict[str, Any]) -> float:
    research = float(metrics.get("research_return_pct") or 0.0)
    validation = float(metrics.get("validation_return_pct") or 0.0)
    drawdown = float(metrics.get("max_drawdown_pct") or 0.0)
    validation_pf = min(float(metrics.get("validation_profit_factor") or 0.0), 4.0)
    validation_trades = int(metrics.get("validation_trade_count") or 0)
    positive_year_ratio = float(metrics.get("positive_year_ratio") or 0.0)
    score = (
        validation * 0.75
        + research * 0.15
        + validation_pf * 3.0
        + positive_year_ratio * 5.0
        - drawdown * 0.70
    )
    if validation <= 0:
        score -= 30.0
    if validation_trades < 5:
        score -= (5 - validation_trades) * 5.0
    return round(score, 6)


def _attach_neighborhood_stability(
    rows: list[dict[str, Any]],
    stop_values: list[float],
    take_values: list[float],
) -> None:
    by_coordinate = {
        (float(item["parameters"]["stop_loss"]), float(item["parameters"]["take_profit"])): item
        for item in rows
    }
    for stop_index, stop_value in enumerate(stop_values):
        for take_index, take_value in enumerate(take_values):
            row = by_coordinate[(float(stop_value), float(take_value))]
            neighbors: list[dict[str, Any]] = []
            for x_index in range(max(stop_index - 1, 0), min(stop_index + 2, len(stop_values))):
                for y_index in range(max(take_index - 1, 0), min(take_index + 2, len(take_values))):
                    neighbors.append(
                        by_coordinate[(float(stop_values[x_index]), float(take_values[y_index]))]
                    )
            returns = [float(item["validation_return_pct"] or 0.0) for item in neighbors]
            positive_ratio = sum(1 for value in returns if value > 0) / len(returns)
            neighbor_min = min(returns)
            neighbor_median = float(np.median(returns))
            neighbor_std = float(np.std(returns))
            plateau = (
                float(row["validation_return_pct"] or 0.0) > 0
                and int(row["validation_trade_count"] or 0) >= 5
                and len(neighbors) == 9
                and positive_ratio >= 0.80
                and neighbor_min > 0
            )
            row.update(
                positive_neighbor_ratio=positive_ratio,
                neighbor_min_validation_return_pct=neighbor_min,
                neighbor_median_validation_return_pct=neighbor_median,
                neighbor_std_validation_return_pct=neighbor_std,
                plateau=plateau,
                plateau_score=round(
                    neighbor_median
                    - neighbor_std
                    - float(row["max_drawdown_pct"] or 0.0) * 0.35,
                    6,
                ),
            )


def _attach_plateau_regions(
    rows: list[dict[str, Any]],
    stop_values: list[float],
    take_values: list[float],
) -> None:
    by_index: dict[tuple[int, int], dict[str, Any]] = {}
    stop_index = {float(value): index for index, value in enumerate(stop_values)}
    take_index = {float(value): index for index, value in enumerate(take_values)}
    for row in rows:
        coordinate = (
            stop_index[float(row["parameters"]["stop_loss"])],
            take_index[float(row["parameters"]["take_profit"])],
        )
        by_index[coordinate] = row
    visited: set[tuple[int, int]] = set()
    region_id = 0
    for coordinate, row in by_index.items():
        if coordinate in visited or not row["plateau"]:
            continue
        region_id += 1
        stack = [coordinate]
        region: list[tuple[int, int]] = []
        visited.add(coordinate)
        while stack:
            current = stack.pop()
            region.append(current)
            x_value, y_value = current
            for neighbor in ((x_value - 1, y_value), (x_value + 1, y_value), (x_value, y_value - 1), (x_value, y_value + 1)):
                candidate = by_index.get(neighbor)
                if neighbor in visited or candidate is None or not candidate["plateau"]:
                    continue
                visited.add(neighbor)
                stack.append(neighbor)
        for item in region:
            by_index[item]["region_id"] = region_id
            by_index[item]["region_size"] = len(region)


def _region_center_candidate(
    rows: list[dict[str, Any]],
    stop_values: list[float],
    take_values: list[float],
) -> dict[str, Any] | None:
    regions: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        if row.get("region_id") is not None:
            regions.setdefault(int(row["region_id"]), []).append(row)
    if not regions:
        return None
    selected = max(
        regions.values(),
        key=lambda items: (
            len(items),
            median(float(item["validation_return_pct"]) for item in items),
            median(float(item["plateau_score"]) for item in items),
        ),
    )
    stop_index = {float(value): index for index, value in enumerate(stop_values)}
    take_index = {float(value): index for index, value in enumerate(take_values)}
    center_x = sum(stop_index[float(item["parameters"]["stop_loss"])] for item in selected) / len(selected)
    center_y = sum(take_index[float(item["parameters"]["take_profit"])] for item in selected) / len(selected)
    return min(
        selected,
        key=lambda item: (
            (
                stop_index[float(item["parameters"]["stop_loss"])] - center_x
            )
            ** 2
            + (
                take_index[float(item["parameters"]["take_profit"])] - center_y
            )
            ** 2,
            -float(item["plateau_score"]),
        ),
    )


def _write_llm_jsonl(path: Path, dataset: SignalPathDataset) -> None:
    bars = dataset.bars
    signals = {int(item["id"]): item for item in dataset.signals}
    research_end = int((dataset.manifest.get("splits") or {}).get("research_end") or 0)
    with gzip.open(path, "wt", encoding="utf-8") as file:
        for episode in dataset.episodes:
            # A research entry whose outcome crosses the split boundary would
            # leak validation candles into the model prompt. Export only
            # episodes that are fully closed inside the research window.
            if (
                episode.get("split") != "research"
                or episode.get("exit_time") is None
                or int(episode["exit_time"]) > research_end
            ):
                continue
            entry_index = int(episode["entry_index"])
            context_start = int(episode["context_start_index"])
            path_end = int(episode["path_end_index"]) + 1
            payload = {
                "schema_version": int(
                    dataset.manifest.get("schema_version")
                    or PATH_DATASET_SCHEMA_VERSION
                ),
                "dataset_id": dataset.manifest["dataset_id"],
                "episode": {
                    key: value
                    for key, value in episode.items()
                    if key not in {"same_side_signal_ids", "context_start_index", "path_end_index"}
                },
                "entry_signal": signals.get(int(episode["entry_signal_id"])),
                "exit_signal": (
                    signals.get(int(episode["exit_signal_id"]))
                    if episode.get("exit_signal_id") is not None
                    else None
                ),
                "same_side_signals": [
                    signals[event_id]
                    for event_id in episode.get("same_side_signal_ids") or []
                    if int(event_id) in signals
                ],
                "bar_columns": list(PATH_BAR_COLUMNS),
                "feature_definitions": PATH_FEATURE_DEFINITIONS,
                "context_bars": [
                    _compact_bar(item, index - entry_index)
                    for index, item in enumerate(bars[context_start:entry_index], start=context_start)
                ],
                "holding_path_bars": [
                    _compact_bar(item, index - entry_index)
                    for index, item in enumerate(bars[entry_index:path_end], start=entry_index)
                ],
            }
            file.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str))
            file.write("\n")


def _compact_bar(bar: dict[str, Any], relative_index: int) -> list[Any]:
    values = {"relative_index": relative_index, **bar}
    return [values.get(column) for column in PATH_BAR_COLUMNS]


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(row.get(key)) for key in fieldnames})


def _write_gzip_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        with gzip.open(path, "wt", encoding="utf-8"):
            return
    fieldnames = sorted({key for row in rows for key in row})
    with gzip.open(path, "wt", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(row.get(key)) for key in fieldnames})


def _csv_value(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return value


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _write_gzip_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, separators=(",", ":"), default=str)


def _dataset_fingerprint(
    resolved: ResolvedBacktestRun,
    bars: list[dict[str, Any]],
    signals: list[dict[str, Any]],
    *,
    context_bars: int,
) -> str:
    digest = hashlib.sha256()
    identity = {
        "schema_version": PATH_DATASET_SCHEMA_VERSION,
        "bar_columns": PATH_BAR_COLUMNS,
        "provider": resolved.config.provider,
        "symbol": resolved.config.symbol,
        "duration_seconds": resolved.config.duration_seconds,
        "strategy": resolved.strategy.name,
        "signal_strategy": resolved.strategy.signal_strategy_name,
        "indicator_parameters": resolved.request.indicator_params,
        "context_bars": int(context_bars),
    }
    digest.update(json.dumps(identity, sort_keys=True, default=str).encode("utf-8"))
    for bar in bars:
        digest.update(
            f"{bar['time']}:{bar['open']}:{bar['high']}:{bar['low']}:{bar['close']}\n".encode("utf-8")
        )
    for signal in signals:
        digest.update(
            f"{signal['signal_time']}:{signal['execution_time']}:{signal['side']}\n".encode("utf-8")
        )
    return digest.hexdigest()


def _code_identity(project_root: Path) -> dict[str, Any]:
    commit: str | None = None
    dirty: bool | None = None
    try:
        commit_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        status_result = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        commit = commit_result.stdout.strip() or None
        dirty = bool(status_result.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return {"git_commit": commit, "git_dirty": dirty}


def _direction(side: str) -> float:
    return 1.0 if side == "buy" else -1.0


def _apply_slippage(price: float, side: str, rate: float) -> float:
    return float(price) * (1.0 + _direction(side) * max(float(rate), 0.0))


def _optional_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _finite_or_none(value: Any) -> float | None:
    parsed = _optional_float(value)
    return parsed
