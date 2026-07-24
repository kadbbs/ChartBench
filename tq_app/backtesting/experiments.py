from __future__ import annotations

import bisect
import csv
import itertools
import json
import math
import multiprocessing
import queue
import shutil
import threading
import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pandas as pd

from tq_app.config_profiles import load_backtest_profile

from .application import BacktestApplication, BacktestRunRequest
from .engine import BacktestResult
from .path_research import (
    PATH_INTRABAR_POLICIES,
    PATH_STOP_UNITS,
    artifact_catalog,
    baseline_summary,
    build_signal_path_dataset,
    load_signal_path_dataset,
    run_path_matrix,
    run_percent_trailing_strategy,
    write_path_matrix_csv,
    write_path_strategy_trades,
    write_signal_path_artifacts,
)
from .performance import (
    attach_deflated_sharpe,
    cscv_probability_of_backtest_overfitting,
    performance_metrics,
    realized_daily_equity,
    strip_private_performance_fields,
)


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
MAX_PARAMETER_VALUES = 256
MAX_PATH_MATRIX_COMBINATIONS = 2_500


@dataclass(slots=True)
class ExperimentSpec:
    name: str
    profile: str
    base_request: BacktestRunRequest
    grid: dict[str, list[Any]]
    combinations: list[dict[str, Any]]


@dataclass(slots=True)
class PathExperimentSpec:
    name: str
    action: str
    profile: str
    base_request: BacktestRunRequest
    context_bars: int
    stop_unit: str
    stop_values: list[float]
    take_values: list[float]
    hard_stop_pct: float
    trailing_activation_pct: float
    trailing_drawdown_pct: float
    max_reentries: int
    reentry_cooldown_bars: int
    intrabar_policy: str
    reveal_test: bool
    baseline_run_id: str | None

    @property
    def combination_count(self) -> int:
        if self.action != "matrix":
            return 1
        return len(self.stop_values) * len(self.take_values)


class BacktestExperimentManager:
    """A single-worker experiment queue isolated from Flask request threads."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.root = project_root / "backtest_outputs" / "ui"
        self.root.mkdir(parents=True, exist_ok=True)
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._shutdown = threading.Event()
        self._process: multiprocessing.Process | None = None
        self._active_run_id: str | None = None
        self._mutation_lock = threading.RLock()
        self._recover_interrupted_jobs()
        self._thread = threading.Thread(target=self._coordinate, name="backtest-experiment-manager", daemon=True)
        self._thread.start()

    def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        is_path_research = str(payload.get("workflow") or "") == "signal_path"
        spec = (
            build_path_experiment_spec(self.project_root, payload)
            if is_path_research
            else build_experiment_spec(self.project_root, payload)
        )
        total_combinations = (
            spec.combination_count
            if isinstance(spec, PathExperimentSpec)
            else len(spec.combinations)
        )
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
            "workflow": "signal_path" if is_path_research else "standard",
            "action": spec.action if isinstance(spec, PathExperimentSpec) else "backtest",
            "status": "queued",
            "phase": "等待执行",
            "progress": 0,
            "completed_combinations": 0,
            "total_combinations": total_combinations,
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
        run_dir = self._safe_run_dir(run_id)
        path = run_dir / "summary.json"
        if not path.exists():
            raise FileNotFoundError("实验结果尚未生成。")
        summary = _read_json(path)
        return _upgrade_legacy_path_baseline_summary(run_dir, summary)

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

    def artifacts(self, run_id: str) -> list[dict[str, Any]]:
        return artifact_catalog(self._safe_run_dir(run_id))

    def deletion_info(self, run_id: str) -> dict[str, Any]:
        run_dir = self._safe_run_dir(run_id)
        status = _read_json(run_dir / "status.json")
        file_count, size_bytes = _directory_usage(run_dir)
        dependent_run_ids = self._dependent_run_ids(run_id)
        run_status = str(status.get("status") or "")
        can_delete = (
            run_status not in {"queued", "running"}
            and run_id != self._active_run_id
        )
        return {
            "run_id": run_id,
            "name": status.get("name") or run_id,
            "status": run_status,
            "can_delete": can_delete,
            "blocked_reason": (
                None
                if can_delete
                else "排队中或运行中的任务不能删除，请等待任务结束。"
            ),
            "file_count": file_count,
            "size_bytes": size_bytes,
            "dependent_run_ids": dependent_run_ids,
            "dependent_run_count": len(dependent_run_ids),
            "shared_market_cache_deleted": False,
        }

    def delete_files(
        self,
        run_id: str,
        *,
        confirm_run_id: str,
        confirm_permanent: bool,
        confirm_dependencies: bool = False,
    ) -> dict[str, Any]:
        if confirm_run_id != run_id or not confirm_permanent:
            raise ValueError("彻底删除确认信息不匹配。")
        with self._mutation_lock:
            info = self.deletion_info(run_id)
            if not info["can_delete"]:
                raise ValueError(str(info["blocked_reason"]))
            if info["dependent_run_count"] and not confirm_dependencies:
                raise ValueError(
                    "该任务仍被其他实验引用，请确认依赖关系后再删除。"
                )
            run_dir = self._safe_run_dir(run_id).resolve()
            root = self.root.resolve()
            if root not in run_dir.parents or run_dir.parent != root:
                raise ValueError("实验目录超出允许的删除范围。")
            shutil.rmtree(run_dir)
        return {
            "deleted": True,
            "run_id": run_id,
            "deleted_file_count": info["file_count"],
            "freed_bytes": info["size_bytes"],
            "dependent_run_ids": info["dependent_run_ids"],
            "recoverable": False,
            "shared_market_cache_deleted": False,
        }

    def artifact_path(self, run_id: str, artifact_name: str) -> Path:
        run_dir = self._safe_run_dir(run_id).resolve()
        relative = Path(str(artifact_name or ""))
        if not relative.parts or relative.is_absolute() or ".." in relative.parts:
            raise ValueError("无效产物名称。")
        path = (run_dir / relative).resolve()
        if run_dir not in path.parents or not path.is_file():
            raise FileNotFoundError("研究产物不存在。")
        return path

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
            self._active_run_id = run_id
            process.start()
            while process.is_alive() and not self._shutdown.wait(0.5):
                process.join(timeout=0.1)
            if self._shutdown.is_set() and process.is_alive():
                process.terminate()
            process.join(timeout=3)
            self._process = None
            self._active_run_id = None
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

    def _dependent_run_ids(self, run_id: str) -> list[str]:
        dependents: set[str] = set()
        for candidate in self.root.iterdir():
            if not candidate.is_dir() or candidate.name == run_id:
                continue
            for filename in ("request.json", "dataset_reference.json"):
                path = candidate / filename
                if not path.is_file():
                    continue
                try:
                    payload = _read_json(path)
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
                if str(payload.get("baseline_run_id") or "") == run_id:
                    dependents.add(candidate.name)
        return sorted(dependents)


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
        coerced = _expand_parameter_values(key, values)
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


def build_path_experiment_spec(project_root: Path, payload: dict[str, Any]) -> PathExperimentSpec:
    if not isinstance(payload, dict):
        raise ValueError("信号路径研究请求必须是 JSON 对象。")
    if str(payload.get("workflow") or "") != "signal_path":
        raise ValueError("workflow 必须为 signal_path。")
    action = str(payload.get("action") or "baseline").strip().lower()
    if action not in {"baseline", "matrix", "percent_trailing"}:
        raise ValueError(
            "信号路径研究 action 只能是 baseline、matrix 或 percent_trailing。"
        )
    base = build_experiment_spec(project_root, {**payload, "grid": {}})
    profile_values = load_backtest_profile(project_root, base.profile)
    indicator_params: dict[str, dict[str, Any]] = {}
    for full_key, kind in INDICATOR_PARAMETER_TYPES.items():
        if full_key not in profile_values:
            continue
        _prefix, indicator_id, parameter_name = full_key.split(".", 2)
        indicator_params.setdefault(indicator_id, {})[parameter_name] = _coerce_parameter(
            kind,
            profile_values[full_key],
        )
    request = replace(
        base.base_request,
        risk_exits_enabled=False,
        indicator_params=indicator_params,
    )
    context_bars = _as_int(payload.get("context_bars"), 288)
    if not 12 <= context_bars <= 2_016:
        raise ValueError("入场前上下文必须在 12 到 2016 根 5m K 线之间。")
    stop_unit = str(payload.get("stop_unit") or "atr").strip().lower()
    if stop_unit not in PATH_STOP_UNITS:
        raise ValueError("距离单位只能是 atr、percent 或 points。")
    intrabar_policy = str(payload.get("intrabar_policy") or "stop_first").strip().lower()
    if intrabar_policy not in PATH_INTRABAR_POLICIES:
        raise ValueError("同 K 线优先规则只能是 stop_first 或 take_first。")
    if action == "matrix":
        stop_values = _path_axis_values(
            "stop_values",
            payload.get("stop_values"),
            default=[0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0],
        )
        take_values = _path_axis_values(
            "take_values",
            payload.get("take_values"),
            default=[0.75, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0],
        )
        if len(stop_values) * len(take_values) > MAX_PATH_MATRIX_COMBINATIONS:
            raise ValueError(
                f"路径矩阵共 {len(stop_values) * len(take_values)} 组，"
                f"单次最多允许 {MAX_PATH_MATRIX_COMBINATIONS} 组。"
            )
    else:
        stop_values = [1.0]
        take_values = [1.0]
    if action == "percent_trailing":
        hard_stop_pct = _path_percent_value(
            "硬止损百分比",
            payload.get("hard_stop_pct"),
            3.0,
        )
        trailing_activation_pct = _path_percent_value(
            "移动止盈启动百分比",
            payload.get("trailing_activation_pct"),
            8.0,
        )
        trailing_drawdown_pct = _path_percent_value(
            "最佳价回撤百分比",
            payload.get("trailing_drawdown_pct"),
            10.0,
        )
    else:
        hard_stop_pct = 3.0
        trailing_activation_pct = 8.0
        trailing_drawdown_pct = 10.0
    max_reentries = _as_int(payload.get("max_reentries"), 0)
    reentry_cooldown_bars = _as_int(payload.get("reentry_cooldown_bars"), 0)
    if not 0 <= max_reentries <= 10:
        raise ValueError("同向再入场次数必须在 0 到 10 之间。")
    if not 0 <= reentry_cooldown_bars <= 2_016:
        raise ValueError("同向再入场冷却必须在 0 到 2016 根之间。")
    baseline_run_id = str(payload.get("baseline_run_id") or "").strip() or None
    if baseline_run_id is not None and (
        action == "baseline"
        or any(item in baseline_run_id for item in ("/", "\\", ".."))
    ):
        raise ValueError("基准任务编号无效。")
    default_name = {
        "baseline": f"{request.symbol} 无风控信号路径",
        "matrix": f"{request.symbol} 止损止盈矩阵",
        "percent_trailing": (
            f"{request.symbol} {hard_stop_pct:g}%硬止损 + "
            f"{trailing_activation_pct:g}%启动 / "
            f"{trailing_drawdown_pct:g}%回撤"
        ),
    }[action]
    return PathExperimentSpec(
        name=str(payload.get("name") or default_name).strip()[:80],
        action=action,
        profile=base.profile,
        base_request=request,
        context_bars=context_bars,
        stop_unit=stop_unit,
        stop_values=stop_values,
        take_values=take_values,
        hard_stop_pct=hard_stop_pct,
        trailing_activation_pct=trailing_activation_pct,
        trailing_drawdown_pct=trailing_drawdown_pct,
        max_reentries=max_reentries,
        reentry_cooldown_bars=reentry_cooldown_bars,
        intrabar_policy=intrabar_policy,
        reveal_test=_as_bool(payload.get("reveal_test", False)),
        baseline_run_id=baseline_run_id,
    )


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
        if str(payload.get("workflow") or "") == "signal_path":
            _execute_path_experiment(
                project_root=project_root,
                run_id=run_id,
                run_dir=run_dir,
                status_path=status_path,
                status=status,
                payload=payload,
            )
            return
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

        multiple_testing = attach_deflated_sharpe(rows)
        overfitting = cscv_probability_of_backtest_overfitting(
            [list(item.get("_daily_returns") or []) for item in rows]
        )
        for row in rows:
            row["matrix_trial_count"] = int(multiple_testing["trial_count"])
            row["matrix_pbo_pct"] = overfitting.get("pbo_pct")
            row["matrix_cscv_split_count"] = int(
                overfitting.get("cscv_split_count") or 0
            )
        ranked = sorted(rows, key=lambda item: float(item["robust_score"]), reverse=True)
        for rank, row in enumerate(ranked, start=1):
            row["rank"] = rank
            strip_private_performance_fields(row)
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
            "statistical_validation": {
                **multiple_testing,
                **overfitting,
                "scope": "full_experiment_window",
            },
            "best": ranked[0] if ranked else None,
            "rows": ranked,
            "score_explanation": "稳健分综合总收益、验证/测试段收益、回撤、盈利因子、年度一致性和最小交易数惩罚；另提供逐日 Sharpe/Sortino/Expected Shortfall、DSR 与矩阵 PBO 交叉验证，用于筛选稳定区域，不代表未来收益。",
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


def _execute_path_experiment(
    *,
    project_root: Path,
    run_id: str,
    run_dir: Path,
    status_path: Path,
    status: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    spec = build_path_experiment_spec(project_root, payload)
    if spec.baseline_run_id is not None:
        source_dir = (
            project_root / "backtest_outputs" / "ui" / spec.baseline_run_id
        ).resolve()
        ui_root = (project_root / "backtest_outputs" / "ui").resolve()
        if ui_root not in source_dir.parents:
            raise ValueError("基准任务路径无效。")
        dataset_path = source_dir / "dataset" / "internal.json.gz"
        if not dataset_path.is_file():
            raise FileNotFoundError("所选基准任务没有可复用的信号路径数据。")
        status.update(phase="加载已有无风控信号路径", progress=38)
        _write_json(status_path, status)
        dataset = load_signal_path_dataset(dataset_path)
        if (
            str(dataset.manifest.get("symbol") or "").upper()
            != spec.base_request.symbol.upper()
            or int(dataset.manifest.get("duration_seconds") or 0)
            != spec.base_request.duration_seconds
        ):
            raise ValueError("所选基准样本与当前品种或执行周期不一致。")
        execution = dict(dataset.manifest.get("execution") or {})
        execution.update(
            initial_equity=spec.base_request.initial_equity,
            margin_amount=spec.base_request.margin_amount,
            margin_ratio_per_trade=spec.base_request.margin_ratio_per_trade,
            leverage=spec.base_request.leverage,
            fee_rate=spec.base_request.fee_rate,
            slippage_rate=spec.base_request.slippage_rate,
        )
        dataset.manifest["execution"] = execution
        _write_json(
            run_dir / "dataset_reference.json",
            {
                "baseline_run_id": spec.baseline_run_id,
                "dataset_id": dataset.manifest.get("dataset_id"),
                "source": str(dataset_path.relative_to(project_root)),
            },
        )
    else:
        application = BacktestApplication(project_root)
        resolved = application.resolve(spec.base_request)
        status.update(phase="准备 5m 与 1D 行情", progress=3)
        _write_json(status_path, status)
        prepared = application.prepare_market(resolved)
        status.update(phase="计算因果指标与有效信号", progress=12)
        _write_json(status_path, status)
        study = application.prepare_study(resolved, prepared)
        status.update(phase="提取无风控开平仓路径", progress=28)
        _write_json(status_path, status)
        dataset = build_signal_path_dataset(
            resolved,
            prepared,
            study,
            context_bars=spec.context_bars,
        )
        del study, prepared
        status.update(phase="导出 K 线、节点与模型样本", progress=42)
        _write_json(status_path, status)
        write_signal_path_artifacts(dataset, run_dir)

    if spec.action == "baseline":
        summary = baseline_summary(dataset)
        status.update(completed_combinations=1, progress=94)
    elif spec.action == "matrix":
        total = spec.combination_count

        def report_progress(completed: int, _total: int) -> None:
            report_every = max(total // 100, 1)
            if completed != total and completed % report_every:
                return
            progress = 45 + int(completed / max(total, 1) * 49)
            status.update(
                phase=f"重放止损止盈组合 {completed}/{total}",
                progress=progress,
                completed_combinations=completed,
            )
            _write_json(status_path, status)

        summary = run_path_matrix(
            dataset,
            stop_unit=spec.stop_unit,
            stop_values=spec.stop_values,
            take_values=spec.take_values,
            max_reentries=spec.max_reentries,
            reentry_cooldown_bars=spec.reentry_cooldown_bars,
            intrabar_policy=spec.intrabar_policy,
            reveal_test=spec.reveal_test,
            progress_callback=report_progress,
        )
        write_path_matrix_csv(run_dir / "matrix.csv", summary["rows"])
    else:
        status.update(
            phase=(
                f"重放 {spec.hard_stop_pct:g}%硬止损 + "
                f"{spec.trailing_activation_pct:g}%启动 / "
                f"{spec.trailing_drawdown_pct:g}%回撤"
            ),
            progress=72,
        )
        _write_json(status_path, status)
        summary = run_percent_trailing_strategy(
            dataset,
            hard_stop_pct=spec.hard_stop_pct,
            trailing_activation_pct=spec.trailing_activation_pct,
            trailing_drawdown_pct=spec.trailing_drawdown_pct,
            max_reentries=spec.max_reentries,
            reentry_cooldown_bars=spec.reentry_cooldown_bars,
            reveal_test=spec.reveal_test,
        )
        trade_records = list(summary.pop("_trade_records", []))
        write_path_matrix_csv(run_dir / "strategy.csv", summary["rows"])
        write_path_strategy_trades(
            run_dir / "strategy_trades.csv",
            trade_records,
        )

    summary.update(
        run_id=run_id,
        name=spec.name,
        profile=spec.profile,
        baseline_run_id=spec.baseline_run_id,
        artifacts=artifact_catalog(run_dir),
    )
    _write_json(run_dir / "summary.json", summary)
    status.update(
        status="succeeded",
        phase="完成",
        progress=100,
        completed_combinations=spec.combination_count,
        finished_at=_now_iso(),
        error=None,
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
    closed = [item for item in result.trades if item.exit_time is not None]
    data_window = result.config.get("resolved_data_window") or {}
    first_time = int(
        data_window.get("first_bar_time")
        or min((int(item.entry_time) for item in closed), default=0)
    )
    last_time = int(
        data_window.get("last_bar_time")
        or max(
            (int(item.exit_time or item.entry_time) for item in closed),
            default=first_time,
        )
    )
    performance_times, performance_equity = realized_daily_equity(
        initial_equity=float(metrics.get("initial_equity") or 1.0),
        pnl_events=(
            (int(item.exit_time or item.entry_time), float(item.net_pnl))
            for item in closed
        ),
        start_time=first_time,
        end_time=last_time,
    )
    advanced = performance_metrics(
        performance_equity,
        performance_times,
        max_drawdown_pct=drawdown_pct,
        return_basis="daily_realized_utc",
    )
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
        **advanced,
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


def _expand_parameter_values(key: str, raw_values: list[Any]) -> list[Any]:
    kind = PARAMETER_TYPES[key]
    tokens: list[Any] = []
    for raw in raw_values:
        if isinstance(raw, str) and "," in raw:
            tokens.extend(item.strip() for item in raw.split(",") if item.strip())
        elif str(raw).strip():
            tokens.append(raw)
    expanded: list[Any] = []
    for token in tokens:
        text = str(token).strip()
        if kind in {int, float} and text.count(":") == 2:
            expanded.extend(_numeric_parameter_range(key, kind, text))
        else:
            expanded.append(_coerce_parameter(kind, token))
        if len(expanded) > MAX_PARAMETER_VALUES:
            raise ValueError(f"参数 {key} 展开后超过 {MAX_PARAMETER_VALUES} 个候选值。")
    return expanded


def _path_axis_values(key: str, raw: Any, *, default: list[float]) -> list[float]:
    if raw in (None, "", []):
        return list(default)
    raw_values = raw if isinstance(raw, list) else [raw]
    tokens: list[Any] = []
    for value in raw_values:
        if isinstance(value, str) and "," in value:
            tokens.extend(item.strip() for item in value.split(",") if item.strip())
        elif str(value).strip():
            tokens.append(value)
    values: list[float] = []
    for token in tokens:
        text = str(token).strip()
        if text.count(":") == 2:
            values.extend(float(item) for item in _numeric_parameter_range(key, float, text))
        else:
            try:
                values.append(float(token))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{key} 包含无效数值: {token!r}") from exc
        if len(values) > MAX_PARAMETER_VALUES:
            raise ValueError(f"{key} 展开后超过 {MAX_PARAMETER_VALUES} 个候选值。")
    unique = list(dict.fromkeys(values))
    if not unique or any(not math.isfinite(value) or value <= 0 for value in unique):
        raise ValueError(f"{key} 必须由大于 0 的有限数值组成。")
    return unique


def _path_percent_value(label: str, raw: Any, default: float) -> float:
    source = default if raw in (None, "") else raw
    try:
        value = float(source)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}必须是数值。") from exc
    if not math.isfinite(value) or not 0 < value < 100:
        raise ValueError(f"{label}必须大于 0 且小于 100。")
    return value


def _numeric_parameter_range(key: str, kind: type, expression: str) -> list[int | float]:
    parts = [item.strip() for item in expression.split(":")]
    try:
        start, end, step = (Decimal(item) for item in parts)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"参数 {key} 的范围格式无效: {expression}，应为 起始:结束:步长。") from exc
    if step == 0:
        raise ValueError(f"参数 {key} 的步长不能为 0。")
    if (end - start) * step < 0:
        raise ValueError(f"参数 {key} 的步长方向与范围不一致: {expression}。")
    if kind is int and any(value != value.to_integral_value() for value in (start, end, step)):
        raise ValueError(f"整数参数 {key} 的起始、结束和步长必须都是整数。")

    values: list[int | float] = []
    current = start
    within = (lambda value: value <= end) if step > 0 else (lambda value: value >= end)
    while within(current):
        values.append(int(current) if kind is int else float(current))
        if len(values) > MAX_PARAMETER_VALUES:
            raise ValueError(f"参数 {key} 展开后超过 {MAX_PARAMETER_VALUES} 个候选值。")
        current += step
    return values


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


def _directory_usage(path: Path) -> tuple[int, int]:
    file_count = 0
    size_bytes = 0
    for item in path.rglob("*"):
        if not item.is_file() and not item.is_symlink():
            continue
        try:
            size_bytes += item.lstat().st_size
            file_count += 1
        except OSError:
            continue
    return file_count, size_bytes


def _time_label(timestamp_ms: int) -> str:
    return pd.Timestamp(timestamp_ms, unit="ms", tz="UTC").tz_convert("Asia/Shanghai").strftime("%Y-%m-%d %H:%M:%S")


def _jsonable_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(payload, ensure_ascii=False, default=str))


def _upgrade_legacy_path_baseline_summary(
    run_dir: Path,
    summary: dict[str, Any],
) -> dict[str, Any]:
    """Fill metrics added after a path dataset was generated.

    The immutable internal dataset is enough to rebuild these fields, so an
    existing multi-year baseline does not need to download candles or compute
    indicators again.
    """

    best = summary.get("best")
    if (
        summary.get("type") != "signal_path_baseline"
        or not isinstance(best, dict)
        or (
            "gross_profit" in best
            and best.get("path_stat_scope") == "research_and_validation"
            and "sharpe_ratio" in best
        )
    ):
        return summary
    dataset_path = run_dir / "dataset" / "internal.json.gz"
    if not dataset_path.is_file():
        return summary
    refreshed = baseline_summary(load_signal_path_dataset(dataset_path))
    result_keys = {
        "schema_version",
        "type",
        "dataset",
        "best",
        "rows",
        "grid",
        "score_explanation",
    }
    refreshed.update(
        {
            key: value
            for key, value in summary.items()
            if key not in result_keys
        }
    )
    refreshed["artifacts"] = artifact_catalog(run_dir)
    _write_json(run_dir / "summary.json", refreshed)
    return refreshed


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
