from __future__ import annotations

import bisect
import csv
import itertools
import json
import math
import multiprocessing
import queue
import threading
import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from tq_app.config_profiles import load_backtest_profile

from .application import BacktestApplication, BacktestRunRequest
from .engine import BacktestResult


RISK_PARAMETER_TYPES: dict[str, type] = {
    "startup_check_bars_5m": int,
    "startup_max_favorable_points": float,
    "startup_current_points": float,
    "disaster_stop_points": float,
    "breakeven_trigger_points": float,
    "breakeven_stop_points": float,
    "trailing_trigger_1_points": float,
    "trailing_protect_1_ratio": float,
    "trailing_trigger_2_points": float,
    "trailing_protect_2_ratio": float,
    "trailing_trigger_3_points": float,
    "trailing_protect_3_ratio": float,
}

INDICATOR_PARAMETER_TYPES: dict[str, type] = {
    "indicator.merged_dkx_hull_ut.hull_length": int,
    "indicator.merged_dkx_hull_ut.hull_length_mult": float,
    "indicator.merged_dkx_hull_ut.hull_variation": str,
    "indicator.merged_dkx_hull_ut.ut_atr_period": int,
    "indicator.merged_dkx_hull_ut.ut_sensitivity": float,
    "indicator.merged_dkx_hull_ut.ut_use_heikin_ashi": bool,
    "indicator.stc.length": int,
    "indicator.stc.fast_length": int,
    "indicator.stc.slow_length": int,
    "indicator.stc.factor": float,
}

PARAMETER_TYPES = {**RISK_PARAMETER_TYPES, **INDICATOR_PARAMETER_TYPES}
MAX_EXPERIMENT_COMBINATIONS = 256
MAX_EXPERIMENT_BARS = 1_200_000


@dataclass(slots=True)
class ExperimentSpec:
    name: str
    profile: str
    base_request: BacktestRunRequest
    grid: dict[str, list[Any]]
    combinations: list[dict[str, Any]]


class BacktestExperimentManager:
    """A single-worker experiment queue isolated from Flask request threads."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.root = project_root / "backtest_outputs" / "ui"
        self.root.mkdir(parents=True, exist_ok=True)
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._shutdown = threading.Event()
        self._process: multiprocessing.Process | None = None
        self._recover_interrupted_jobs()
        self._thread = threading.Thread(target=self._coordinate, name="backtest-experiment-manager", daemon=True)
        self._thread.start()

    def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        spec = build_experiment_spec(self.project_root, payload)
        now = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_id = f"{now}_{uuid.uuid4().hex[:8]}"
        run_dir = self.root / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        request_payload = _jsonable_payload(payload)
        request_payload["name"] = spec.name
        request_payload["profile"] = spec.profile
        _write_json(run_dir / "request.json", request_payload)
        status = {
            "run_id": run_id,
            "name": spec.name,
            "profile": spec.profile,
            "status": "queued",
            "phase": "等待执行",
            "progress": 0,
            "completed_combinations": 0,
            "total_combinations": len(spec.combinations),
            "created_at": _now_iso(),
            "started_at": None,
            "finished_at": None,
            "error": None,
        }
        _write_json(run_dir / "status.json", status)
        self._queue.put(run_id)
        return status

    def get(self, run_id: str) -> dict[str, Any]:
        run_dir = self._safe_run_dir(run_id)
        status = _read_json(run_dir / "status.json")
        summary_path = run_dir / "summary.json"
        if summary_path.exists():
            summary = _read_json(summary_path)
            status["result_type"] = summary.get("type")
            status["best"] = summary.get("best")
        return status

    def list(self, limit: int = 30) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for path in sorted(self.root.iterdir(), key=lambda item: item.name, reverse=True):
            if not path.is_dir() or not (path / "status.json").exists():
                continue
            try:
                items.append(self.get(path.name))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if len(items) >= max(1, min(int(limit), 100)):
                break
        return items

    def result(self, run_id: str) -> dict[str, Any]:
        path = self._safe_run_dir(run_id) / "summary.json"
        if not path.exists():
            raise FileNotFoundError("实验结果尚未生成。")
        return _read_json(path)

    def report(self, run_id: str) -> dict[str, Any]:
        path = self._safe_run_dir(run_id) / "artifacts" / "report.json"
        if not path.exists():
            raise FileNotFoundError("该实验没有完整报告；可将矩阵候选复制为单次实验后复测。")
        return _read_json(path)

    def chart_preview(self, run_id: str) -> dict[str, Any]:
        path = self._safe_run_dir(run_id) / "chart_preview.json"
        if not path.exists():
            raise FileNotFoundError("该实验没有图表预览。")
        return _read_json(path)

    def request_payload(self, run_id: str) -> dict[str, Any]:
        return _read_json(self._safe_run_dir(run_id) / "request.json")

    def shutdown(self) -> None:
        self._shutdown.set()
        self._queue.put(None)
        process = self._process
        if process is not None and process.is_alive():
            process.terminate()
            process.join(timeout=3)
        self._thread.join(timeout=3)

    def _coordinate(self) -> None:
        context = multiprocessing.get_context("spawn")
        while not self._shutdown.is_set():
            try:
                run_id = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if run_id is None:
                break
            process = context.Process(
                target=_execute_experiment_process,
                args=(str(self.project_root), run_id),
                name=f"backtest-{run_id}",
                daemon=True,
            )
            self._process = process
            process.start()
            while process.is_alive() and not self._shutdown.wait(0.5):
                process.join(timeout=0.1)
            if self._shutdown.is_set() and process.is_alive():
                process.terminate()
            process.join(timeout=3)
            self._process = None
            status_path = self.root / run_id / "status.json"
            try:
                status = _read_json(status_path)
            except (OSError, json.JSONDecodeError):
                continue
            if status.get("status") == "running":
                status.update(
                    status="failed",
                    phase="进程异常结束",
                    finished_at=_now_iso(),
                    error=f"回测进程退出码: {process.exitcode}",
                )
                _write_json(status_path, status)

    def _recover_interrupted_jobs(self) -> None:
        for status_path in self.root.glob("*/status.json"):
            try:
                status = _read_json(status_path)
            except (OSError, json.JSONDecodeError):
                continue
            if status.get("status") not in {"queued", "running"}:
                continue
            status.update(
                status="interrupted",
                phase="服务重启，任务已中断",
                finished_at=_now_iso(),
                error="任务执行期间服务发生重启，可复制参数后重新测试。",
            )
            _write_json(status_path, status)

    def _safe_run_dir(self, run_id: str) -> Path:
        safe = str(run_id).strip()
        if not safe or any(item in safe for item in ("/", "\\", "..")):
            raise ValueError("无效实验编号。")
        path = self.root / safe
        if not path.is_dir():
            raise FileNotFoundError("实验不存在。")
        return path


def build_experiment_spec(project_root: Path, payload: dict[str, Any]) -> ExperimentSpec:
    if not isinstance(payload, dict):
        raise ValueError("实验请求必须是 JSON 对象。")
    profile_name = str(payload.get("profile") or "").strip()
    if not profile_name:
        raise ValueError("请选择回测 profile。")
    profile = load_backtest_profile(project_root, profile_name)
    overrides = payload.get("overrides") or {}
    if not isinstance(overrides, dict):
        raise ValueError("overrides 必须是对象。")
    grid_raw = payload.get("grid") or {}
    if not isinstance(grid_raw, dict):
        raise ValueError("grid 必须是对象。")
    unknown = set(grid_raw) - set(PARAMETER_TYPES)
    if unknown:
        raise ValueError(f"暂不支持这些组合参数: {', '.join(sorted(unknown))}")
    grid: dict[str, list[Any]] = {}
    for key, raw_values in grid_raw.items():
        values = raw_values if isinstance(raw_values, list) else [raw_values]
        coerced = [_coerce_parameter(PARAMETER_TYPES[key], item) for item in values if str(item).strip()]
        if coerced:
            grid[key] = list(dict.fromkeys(coerced))
    combinations = _combinations(grid)
    if len(combinations) > MAX_EXPERIMENT_COMBINATIONS:
        raise ValueError(f"参数组合共 {len(combinations)} 组，单次最多允许 {MAX_EXPERIMENT_COMBINATIONS} 组。请先粗扫再局部细扫。")

    def value(key: str, default: Any) -> Any:
        return overrides[key] if key in overrides else profile.get(key, default)

    request = BacktestRunRequest(
        profile=profile_name,
        provider=str(value("provider", "bitget")).strip().lower(),
        symbol=str(value("symbol", "BTCUSDT")).strip().upper(),
        duration_seconds=_as_int(value("duration_seconds", value("duration", 300)), 300),
        data_length=_as_int(value("data_length", value("length", 800)), 800),
        strategy=str(value("strategy", "live_decision")).strip(),
        product_type=str(value("product_type", "USDT-FUTURES")).strip().upper(),
        kline_type=str(value("kline_type", "MARKET")).strip().upper(),
        start_time=str(value("start_time", "")).strip(),
        end_time=str(value("end_time", "")).strip(),
        initial_equity=_as_float(value("initial_equity", 20_000), 20_000),
        risk_per_trade=_as_float(value("risk_per_trade", 0.01), 0.01),
        margin_amount=_as_float(value("margin_amount", 1_000), 1_000),
        margin_ratio_per_trade=_as_float(value("margin_ratio_per_trade", 0), 0),
        leverage=_as_float(value("leverage", 10), 10),
        fee_rate=_as_float(value("fee_rate", 0.00023), 0.00023),
        slippage_rate=_as_float(value("slippage_rate", 0), 0),
        warmup_bars=_as_int(value("warmup_bars", 80), 80),
        risk_exits_enabled=_as_bool(value("risk_exits_enabled", True)),
        startup_check_bars_5m=_as_int(value("startup_check_bars_5m", 24), 24),
        startup_max_favorable_points=_as_float(value("startup_max_favorable_points", 300), 300),
        startup_current_points=_as_float(value("startup_current_points", -150), -150),
        disaster_stop_points=_as_float(value("disaster_stop_points", -1800), -1800),
        breakeven_trigger_points=_as_float(value("breakeven_trigger_points", 800), 800),
        breakeven_stop_points=_as_float(value("breakeven_stop_points", 100), 100),
        trailing_trigger_1_points=_as_float(value("trailing_trigger_1_points", 2000), 2000),
        trailing_protect_1_ratio=_as_float(value("trailing_protect_1_ratio", 0.4), 0.4),
        trailing_trigger_2_points=_as_float(value("trailing_trigger_2_points", 4000), 4000),
        trailing_protect_2_ratio=_as_float(value("trailing_protect_2_ratio", 0.5), 0.5),
        trailing_trigger_3_points=_as_float(value("trailing_trigger_3_points", 8000), 8000),
        trailing_protect_3_ratio=_as_float(value("trailing_protect_3_ratio", 0.6), 0.6),
        cache_enabled=_as_bool(value("cache_enabled", True)),
        cache_dir=Path("data_cache/backtest_klines"),
    )
    if request.start_time and request.end_time:
        start = pd.Timestamp(request.start_time)
        end = pd.Timestamp(request.end_time)
        estimated_bars = int((end - start).total_seconds() // max(request.duration_seconds, 1)) + 1
        if estimated_bars <= 0:
            raise ValueError("开始时间必须早于结束时间。")
        if estimated_bars > MAX_EXPERIMENT_BARS:
            raise ValueError(f"目标 K 线约 {estimated_bars:,} 根，超过单次实验上限 {MAX_EXPERIMENT_BARS:,} 根。")
    name = str(payload.get("name") or f"{request.symbol} 参数研究").strip()[:80]
    return ExperimentSpec(name=name, profile=profile_name, base_request=request, grid=grid, combinations=combinations)


def cache_coverage(project_root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    spec = build_experiment_spec(project_root, {**payload, "grid": {}})
    request = spec.base_request
    product = f"BINANCE_{request.product_type}" if request.provider == "binance" else request.product_type
    filename = f"{product.upper()}_{request.symbol.upper()}_{request.duration_seconds}s_{request.kline_type.upper()}.csv"
    path = project_root / request.cache_dir / filename
    response: dict[str, Any] = {
        "path": str(path.relative_to(project_root)),
        "exists": path.exists(),
        "size_bytes": path.stat().st_size if path.exists() else 0,
        "first_time": None,
        "last_time": None,
        "coverage_pct": 0.0,
        "complete": False,
    }
    if not path.exists():
        return response
    first_ms, last_ms = _csv_time_bounds(path)
    response["first_time"] = _time_label(first_ms)
    response["last_time"] = _time_label(last_ms)
    try:
        start_ms = int(pd.Timestamp(request.start_time).tz_localize("Asia/Shanghai").tz_convert("UTC").timestamp() * 1000)
        end_ms = int(pd.Timestamp(request.end_time).tz_localize("Asia/Shanghai").tz_convert("UTC").timestamp() * 1000)
    except (TypeError, ValueError):
        response["coverage_pct"] = 100.0
        response["complete"] = True
        return response
    requested_span = max(end_ms - start_ms, 1)
    covered = max(min(last_ms, end_ms) - max(first_ms, start_ms), 0)
    response["coverage_pct"] = round(min(covered / requested_span * 100, 100.0), 2)
    tolerance = request.duration_seconds * 1000
    response["complete"] = first_ms <= start_ms + tolerance and last_ms >= end_ms - tolerance
    return response


def _execute_experiment_process(project_root_raw: str, run_id: str) -> None:
    project_root = Path(project_root_raw)
    run_dir = project_root / "backtest_outputs" / "ui" / run_id
    status_path = run_dir / "status.json"
    status = _read_json(status_path)
    status.update(status="running", phase="解析配置", progress=1, started_at=_now_iso())
    _write_json(status_path, status)
    try:
        payload = _read_json(run_dir / "request.json")
        spec = build_experiment_spec(project_root, payload)
        application = BacktestApplication(project_root)
        resolved = application.resolve(spec.base_request)
        status.update(phase="准备并检查行情", progress=3)
        _write_json(status_path, status)
        prepared = application.prepare_market(resolved)
        status.update(phase="行情准备完成", progress=8)
        _write_json(status_path, status)

        rows: list[dict[str, Any]] = []
        single_result: BacktestResult | None = None
        total = len(spec.combinations)
        prepared_study = None
        prepared_indicator_signature = ""
        for index, parameters in enumerate(spec.combinations, start=1):
            request = _request_for_combination(spec.base_request, parameters, run_dir / "artifacts")
            indicator_signature = json.dumps(request.indicator_params, ensure_ascii=False, sort_keys=True)
            if prepared_study is None or indicator_signature != prepared_indicator_signature:
                status.update(phase=f"预计算指标 {index}/{total}")
                _write_json(status_path, status)
                combination_resolved = application.resolve(request)
                prepared_study = application.prepare_study(combination_resolved, prepared)
                prepared_indicator_signature = indicator_signature
            is_single = total == 1
            result = application.run(
                request,
                prepared_market=prepared,
                prepared_study=prepared_study,
                write_artifacts=is_single,
            )
            if is_single:
                single_result = result
            rows.append(_result_row(index, parameters, result))
            progress = 8 + int(index / total * 86)
            status.update(
                phase=f"计算参数组合 {index}/{total}",
                progress=progress,
                completed_combinations=index,
            )
            _write_json(status_path, status)

        ranked = sorted(rows, key=lambda item: float(item["robust_score"]), reverse=True)
        for rank, row in enumerate(ranked, start=1):
            row["rank"] = rank
        result_type = "single" if total == 1 else "matrix"
        summary = {
            "type": result_type,
            "run_id": run_id,
            "name": spec.name,
            "profile": spec.profile,
            "created_at": status.get("created_at"),
            "data": {
                "provider": spec.base_request.provider,
                "symbol": spec.base_request.symbol,
                "duration_seconds": spec.base_request.duration_seconds,
                "start_time": spec.base_request.start_time,
                "end_time": spec.base_request.end_time,
                "bar_count": len(prepared.bars),
            },
            "grid": spec.grid,
            "combination_count": total,
            "best": ranked[0] if ranked else None,
            "rows": ranked,
            "score_explanation": "稳健分综合总收益、验证/测试段收益、回撤、盈利因子、年度一致性和最小交易数惩罚；用于筛选稳定区域，不代表未来收益。",
        }
        _write_json(run_dir / "summary.json", summary)
        _write_summary_csv(run_dir / "summary.csv", ranked)
        if single_result is not None:
            preview = _chart_preview(prepared.bars, single_result, spec.base_request, max_bars=4_000)
            _write_json(run_dir / "chart_preview.json", preview, compact=True)
        status.update(
            status="succeeded",
            phase="完成",
            progress=100,
            completed_combinations=total,
            finished_at=_now_iso(),
            error=None,
        )
        _write_json(status_path, status)
    except Exception as exc:
        status.update(
            status="failed",
            phase="执行失败",
            finished_at=_now_iso(),
            error=str(exc),
        )
        _write_json(status_path, status)


def _request_for_combination(
    base: BacktestRunRequest,
    parameters: dict[str, Any],
    output_dir: Path,
) -> BacktestRunRequest:
    request = replace(base, output_dir=output_dir, indicator_params={key: dict(value) for key, value in base.indicator_params.items()})
    for key, value in parameters.items():
        if key in RISK_PARAMETER_TYPES:
            setattr(request, key, value)
            continue
        _prefix, indicator_id, parameter_name = key.split(".", 2)
        request.indicator_params.setdefault(indicator_id, {})[parameter_name] = value
    request.extra_context = {
        **base.extra_context,
        "experiment_parameters": parameters,
    }
    return request


def _result_row(index: int, parameters: dict[str, Any], result: BacktestResult) -> dict[str, Any]:
    metrics = result.metrics
    stability = _stability_metrics(result)
    drawdown_pct = abs(float(metrics.get("max_drawdown") or 0.0) * 100)
    profit_factor = float(metrics.get("profit_factor") or 0.0)
    trade_count = int(metrics.get("trade_count") or 0)
    total_return = float(metrics.get("return_pct") or 0.0)
    validation_return = float(stability["validation_return_pct"])
    test_return = float(stability["test_return_pct"])
    positive_year_ratio = float(stability["positive_year_ratio"])
    score = (
        total_return * 0.20
        + validation_return * 0.65
        + test_return * 0.85
        - drawdown_pct * 2.0
        + min(profit_factor, 3.0) * 8.0
        + positive_year_ratio * 20.0
    )
    if trade_count < 30:
        score -= 100.0 - trade_count
    if validation_return <= 0:
        score -= 35.0
    if test_return <= 0:
        score -= 50.0
    verdict = "通过" if validation_return > 0 and test_return > 0 and drawdown_pct <= 25 and trade_count >= 30 else "谨慎"
    if total_return <= 0 or test_return < -5 or drawdown_pct > 40:
        verdict = "不通过"
    return {
        "rank": 0,
        "run": index,
        "parameters": parameters,
        "robust_score": round(score, 6),
        "verdict": verdict,
        "net_profit": metrics.get("net_profit"),
        "return_pct": metrics.get("return_pct"),
        "max_drawdown_pct": drawdown_pct,
        "trade_count": trade_count,
        "win_rate_pct": float(metrics.get("win_rate") or 0.0) * 100,
        "profit_factor": metrics.get("profit_factor"),
        "total_net_points": metrics.get("total_net_points"),
        "total_fees": metrics.get("total_fees"),
        **stability,
    }


def _stability_metrics(result: BacktestResult) -> dict[str, Any]:
    trades = sorted((item for item in result.trades if item.exit_time is not None), key=lambda item: int(item.exit_time or 0))
    initial = float(result.metrics.get("initial_equity") or 1.0)
    if not trades:
        return {
            "research_return_pct": 0.0,
            "validation_return_pct": 0.0,
            "test_return_pct": 0.0,
            "positive_year_ratio": 0.0,
            "worst_year_return_pct": 0.0,
            "yearly_returns_pct": {},
        }
    data_window = result.config.get("resolved_data_window") or {}
    first_time = int(data_window.get("first_bar_time") or trades[0].entry_time)
    last_time = int(data_window.get("last_bar_time") or max(int(item.exit_time or item.entry_time) for item in trades))
    span = max(last_time - first_time, 1)
    research_end = first_time + int(span * 0.60)
    validation_end = first_time + int(span * 0.80)
    buckets = {"research": 0.0, "validation": 0.0, "test": 0.0}
    yearly_pnl: dict[str, float] = {}
    for trade in trades:
        exit_time = int(trade.exit_time or trade.entry_time)
        if exit_time <= research_end:
            buckets["research"] += trade.net_pnl
        elif exit_time <= validation_end:
            buckets["validation"] += trade.net_pnl
        else:
            buckets["test"] += trade.net_pnl
        year = pd.Timestamp(exit_time, unit="s", tz="UTC").tz_convert("Asia/Shanghai").strftime("%Y")
        yearly_pnl[year] = yearly_pnl.get(year, 0.0) + trade.net_pnl
    yearly_returns = {key: value / initial * 100 for key, value in sorted(yearly_pnl.items())}
    positive_years = sum(1 for value in yearly_returns.values() if value > 0)
    return {
        "research_return_pct": buckets["research"] / initial * 100,
        "validation_return_pct": buckets["validation"] / initial * 100,
        "test_return_pct": buckets["test"] / initial * 100,
        "positive_year_ratio": positive_years / len(yearly_returns) if yearly_returns else 0.0,
        "worst_year_return_pct": min(yearly_returns.values(), default=0.0),
        "yearly_returns_pct": yearly_returns,
    }


def _chart_preview(
    bars: pd.DataFrame,
    result: BacktestResult,
    request: BacktestRunRequest,
    *,
    max_bars: int,
) -> dict[str, Any]:
    frame = bars.copy().reset_index(drop=True)
    datetimes = pd.to_datetime(frame["datetime"], utc=True)
    frame["time"] = datetimes.astype("int64") // 1_000_000_000
    bucket_size = max(1, math.ceil(len(frame) / max(max_bars, 1)))
    frame["bucket"] = frame.index // bucket_size
    grouped = frame.groupby("bucket", sort=True).agg(
        time=("time", "first"),
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    )
    candles = [
        {
            "time": int(row.time),
            "open": float(row.open),
            "high": float(row.high),
            "low": float(row.low),
            "close": float(row.close),
        }
        for row in grouped.itertuples(index=False)
    ]
    volume = [
        {"time": int(row.time), "value": float(row.volume)}
        for row in grouped.itertuples(index=False)
    ]
    source_times = frame["time"].astype(int).tolist()
    bucket_times = grouped["time"].astype(int).tolist()
    markers: list[dict[str, Any]] = []
    for marker in result.markers:
        source_index = max(bisect.bisect_right(source_times, int(marker["time"])) - 1, 0)
        bucket_index = min(source_index // bucket_size, len(bucket_times) - 1)
        mapped = dict(marker)
        mapped["time"] = bucket_times[bucket_index]
        markers.append(mapped)
    labels = {
        str(value): pd.Timestamp(value, unit="s", tz="UTC").tz_convert("Asia/Shanghai").strftime("%Y-%m-%d %H:%M:%S")
        for value in bucket_times
    }
    return {
        "symbol": request.symbol,
        "duration_seconds": request.duration_seconds,
        "source_bar_count": len(frame),
        "preview_bar_count": len(candles),
        "bucket_size": bucket_size,
        "candles": candles,
        "volume": volume,
        "markers": markers,
        "time_labels": labels,
    }


def _combinations(grid: dict[str, list[Any]]) -> list[dict[str, Any]]:
    if not grid:
        return [{}]
    keys = list(grid)
    return [dict(zip(keys, values)) for values in itertools.product(*(grid[key] for key in keys))]


def _coerce_parameter(kind: type, value: Any) -> Any:
    if kind is bool:
        return _as_bool(value)
    if kind is int:
        return int(value)
    if kind is float:
        return float(value)
    return str(value).strip()


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _write_summary_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    flattened: list[dict[str, Any]] = []
    parameter_keys = sorted({key for row in rows for key in row.get("parameters", {})})
    for row in rows:
        flat = {key: value for key, value in row.items() if key not in {"parameters", "yearly_returns_pct"}}
        flat.update({key: row["parameters"].get(key) for key in parameter_keys})
        flat["yearly_returns_pct"] = json.dumps(row.get("yearly_returns_pct") or {}, ensure_ascii=False)
        flattened.append(flat)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(flattened[0]))
        writer.writeheader()
        writer.writerows(flattened)


def _csv_time_bounds(path: Path) -> tuple[int, int]:
    with path.open("rb") as file:
        header = file.readline()
        first_line = file.readline()
        if not header or not first_line:
            raise ValueError("缓存文件为空。")
        file.seek(0, 2)
        position = file.tell() - 1
        while position > 0:
            file.seek(position)
            if file.read(1) == b"\n" and position < file.tell():
                candidate = file.readline().strip()
                if candidate:
                    last_line = candidate
                    break
            position -= 1
        else:
            last_line = first_line.strip()
    return int(first_line.split(b",", 1)[0]), int(last_line.split(b",", 1)[0])


def _time_label(timestamp_ms: int) -> str:
    return pd.Timestamp(timestamp_ms, unit="ms", tz="UTC").tz_convert("Asia/Shanghai").strftime("%Y-%m-%d %H:%M:%S")


def _jsonable_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(payload, ensure_ascii=False, default=str))


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any, *, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":") if compact else None, indent=None if compact else 2),
        encoding="utf-8",
    )
    temp.replace(path)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
