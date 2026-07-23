from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
import subprocess
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
from .runtime import PreparedBacktestMarket


PATH_DATASET_SCHEMA_VERSION = 1
PATH_MATRIX_SCHEMA_VERSION = 1
PATH_STOP_UNITS = {"atr", "percent", "points"}
PATH_INTRABAR_POLICIES = {"stop_first", "take_first"}
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
    "stc_direction",
    "mhull_up",
    "shull_up",
    "mhull_down",
    "shull_down",
)


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
            "daily_trend": context.get("trend"),
            "daily_bar_time": context.get("bar_time"),
            "daily_bar_time_label": context.get("bar_time_label"),
            "daily_segment_start_time": context.get("trend_start_time"),
            "daily_segment_start_time_label": context.get("trend_start_time_label"),
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
        "causality": {
            "closed_bars_only": True,
            "stc_strategy_color": "current_vs_previous_value",
        },
    }
    return SignalPathDataset(manifest=manifest, bars=bars, signals=signals, episodes=episodes)


def baseline_summary(dataset: SignalPathDataset) -> dict[str, Any]:
    replay = replay_signal_paths(dataset, PathReplayConfig())
    closed = [item for item in dataset.episodes if item["status"] == "closed"]
    holding = [int(item["holding_bars"]) for item in closed]
    mfe_atr = [float(item["mfe_atr"]) for item in closed if item.get("mfe_atr") is not None]
    mae_atr = [float(item["mae_atr"]) for item in closed if item.get("mae_atr") is not None]
    same_side = sum(len(item.get("same_side_signal_ids") or []) for item in dataset.episodes)
    best = {
        **replay,
        "verdict": "基准完成",
        "robust_score": None,
        "median_holding_bars": median(holding) if holding else 0,
        "median_mfe_atr": median(mfe_atr) if mfe_atr else None,
        "median_mae_atr": median(mae_atr) if mae_atr else None,
        "qualified_signal_count": len(dataset.signals),
        "same_side_signal_count": same_side,
        "closed_episode_count": len(closed),
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
            "没有止损、止盈、保本、移动保护或同向再入场。"
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
                }
            )
            completed += 1
            if progress_callback is not None:
                progress_callback(completed, total)

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
        "best": best,
        "rows": ranked,
        "score_explanation": (
            "候选只使用研究段和验证段评分，测试段不参与排序。稳定区要求当前格盈利、"
            "3×3 邻域至少 80% 验证盈利、邻域最差收益为正且验证交易数充足；"
            "同一根 5m 同时触发止损止盈时按所选保守规则处理。"
        ),
    }


def replay_signal_paths(
    dataset: SignalPathDataset,
    config: PathReplayConfig,
) -> dict[str, Any]:
    execution = dataset.manifest.get("execution") or {}
    initial_equity = float(execution.get("initial_equity") or 20_000.0)
    margin_amount = float(execution.get("margin_amount") or 1_000.0)
    margin_ratio = float(execution.get("margin_ratio_per_trade") or 0.0)
    leverage = float(execution.get("leverage") or 10.0)
    fee_rate = float(execution.get("fee_rate") or 0.0)
    slippage_rate = float(execution.get("slippage_rate") or 0.0)
    bars = dataset.bars
    arrays = {
        key: np.asarray([float(item[key]) for item in bars], dtype="float64")
        for key in ("open", "high", "low", "close")
    }
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

    metrics = _replay_metrics(
        records,
        initial_equity,
        reveal_test=config.reveal_test,
    )
    visible_records = (
        records
        if config.reveal_test
        else [item for item in records if item["split"] != "test"]
    )
    metrics["ambiguous_bar_count"] = sum(
        1 for item in visible_records if item["ambiguous_bar"]
    )
    metrics["reentry_trade_count"] = sum(
        1 for item in visible_records if int(item["entry_number"]) > 1
    )
    if not config.reveal_test:
        metrics["test_return_pct"] = None
        metrics["test_trade_count"] = None
        metrics["test_win_rate_pct"] = None
        metrics["test_profit_factor"] = None
    return metrics


def write_signal_path_artifacts(dataset: SignalPathDataset, output_dir: Path) -> list[dict[str, Any]]:
    dataset_dir = output_dir / "dataset"
    dataset_dir.mkdir(parents=True, exist_ok=True)
    _write_json(dataset_dir / "manifest.json", dataset.manifest)
    _write_gzip_json(dataset_dir / "internal.json.gz", dataset.as_dict())
    _write_csv(dataset_dir / "episodes.csv", dataset.episodes)
    _write_csv(dataset_dir / "signals.csv", dataset.signals)
    _write_gzip_csv(dataset_dir / "bars.csv.gz", dataset.bars)
    _write_llm_jsonl(dataset_dir / "llm_research_samples.jsonl.gz", dataset)
    readme = (
        "ChartBench signal-path research dataset\n\n"
        "- manifest.json: data identity, strategy semantics and split boundaries\n"
        "- episodes.csv: no-risk entry-to-qualified-opposite-signal episodes\n"
        "- signals.csv: every qualified entry, same-side and reverse signal\n"
        "- bars.csv.gz: unique 5-minute OHLCV and causal indicator features\n"
        "- llm_research_samples.jsonl.gz: research split only; test data is excluded\n"
        "- internal.json.gz: replay cache used by ChartBench\n"
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
    with gzip.open(path, "rt", encoding="utf-8") as file:
        payload = json.load(file)
    return SignalPathDataset.from_dict(payload)


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
    features: dict[int, dict[str, Any]] = {}
    for indicator in snapshot.get("indicators") or []:
        indicator_id = str(indicator.get("id") or "")
        for series in indicator.get("series") or []:
            series_id = str(series.get("id") or "")
            target = feature_ids.get(f"{indicator_id}.{series_id}")
            if target is None:
                continue
            for point in series.get("data") or []:
                value = point.get("value")
                if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                    continue
                timestamp = int(point.get("time") or 0)
                item = features.setdefault(timestamp, {})
                item[target] = float(value)
                if target == "stc":
                    color = str(point.get("signal_color") or point.get("color") or "")
                    item["stc_direction"] = 1 if is_green_color(color) else -1 if is_red_color(color) else 0

    bars: list[dict[str, Any]] = []
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
            "stc_direction": None,
            "mhull_up": None,
            "shull_up": None,
            "mhull_down": None,
            "shull_down": None,
        }
        item.update(features.get(timestamp) or {})
        bars.append(item)
    return bars


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


def _replay_metrics(
    records: list[dict[str, Any]],
    initial_equity: float,
    *,
    reveal_test: bool,
) -> dict[str, Any]:
    visible = records if reveal_test else [item for item in records if item["split"] != "test"]
    wins = [item for item in visible if float(item["net_pnl"]) > 0]
    losses = [item for item in visible if float(item["net_pnl"]) < 0]
    gross_profit = sum(float(item["net_pnl"]) for item in wins)
    gross_loss = abs(sum(float(item["net_pnl"]) for item in losses))
    visible_net_profit = sum(float(item["net_pnl"]) for item in visible)
    visible_max_drawdown_pct = max(
        (float(item.get("path_drawdown_pct") or 0.0) for item in visible),
        default=0.0,
    )
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
        "net_profit": visible_net_profit,
        "return_pct": visible_net_profit / initial_equity * 100,
        "max_drawdown_pct": visible_max_drawdown_pct,
        "trade_count": len(visible),
        "win_rate_pct": len(wins) / len(visible) * 100 if visible else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0),
        "total_fees": sum(float(item["fees"]) for item in visible),
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
    for split in ("research", "validation", "test"):
        subset = [item for item in records if item["split"] == split]
        pnl = sum(float(item["net_pnl"]) for item in subset)
        split_wins = sum(1 for item in subset if float(item["net_pnl"]) > 0)
        split_profit = sum(float(item["net_pnl"]) for item in subset if float(item["net_pnl"]) > 0)
        split_loss = abs(sum(float(item["net_pnl"]) for item in subset if float(item["net_pnl"]) < 0))
        metrics[f"{split}_return_pct"] = pnl / initial_equity * 100
        metrics[f"{split}_trade_count"] = len(subset)
        metrics[f"{split}_win_rate_pct"] = split_wins / len(subset) * 100 if subset else 0.0
        metrics[f"{split}_profit_factor"] = (
            split_profit / split_loss
            if split_loss > 0
            else 999.0
            if split_profit > 0
            else 0.0
        )
    return metrics


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
                "schema_version": 1,
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
