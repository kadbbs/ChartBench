from __future__ import annotations

from pathlib import Path
from typing import Any

from flask import Blueprint, jsonify, render_template, request, send_file

from tq_app.config_profiles import available_backtest_profiles, load_backtest_profile
from tq_app.domain import get_strategy_catalog
from tq_app.indicators import build_indicator_registry

from .experiments import (
    MAX_EXPERIMENT_COMBINATIONS,
    MAX_PATH_MATRIX_COMBINATIONS,
    PARAMETER_TYPES,
    BacktestExperimentManager,
    build_experiment_spec,
    build_path_experiment_spec,
    cache_coverage,
)


def create_backtest_blueprint(
    manager: BacktestExperimentManager,
    project_root: Path,
) -> Blueprint:
    blueprint = Blueprint("backtests", __name__)

    @blueprint.get("/backtests")
    def page() -> str:
        css_path = project_root / "static" / "backtests.css"
        js_path = project_root / "static" / "backtests.js"
        return render_template(
            "backtests.html",
            asset_versions={
                "css": str(int(css_path.stat().st_mtime)) if css_path.exists() else "0",
                "js": str(int(js_path.stat().st_mtime)) if js_path.exists() else "0",
            },
        )

    @blueprint.get("/api/backtests/catalog")
    def catalog() -> Any:
        profiles = [
            {"name": name, "values": load_backtest_profile(project_root, name)}
            for name in available_backtest_profiles(project_root)
        ]
        registry = build_indicator_registry(project_root)
        indicator_meta = {
            item.id: {
                "id": item.id,
                "name": item.name,
                "params": item.params,
            }
            for item in registry.list_meta()
            if item.id in {"merged_dkx_hull_ut", "stc"}
        }
        return jsonify(
            {
                "profiles": profiles,
                "strategies": get_strategy_catalog(project_root),
                "providers": ["bitget", "binance"],
                "durations": [60, 180, 300, 900, 1800, 3600, 14400, 86400],
                "parameter_keys": sorted(PARAMETER_TYPES),
                "indicators": indicator_meta,
                "max_combinations": MAX_EXPERIMENT_COMBINATIONS,
                "max_path_combinations": MAX_PATH_MATRIX_COMBINATIONS,
            }
        )

    @blueprint.post("/api/backtests/estimate")
    def estimate() -> Any:
        try:
            payload = request.get_json(force=True)
            is_path = str(payload.get("workflow") or "") == "signal_path"
            spec = (
                build_path_experiment_spec(project_root, payload)
                if is_path
                else build_experiment_spec(project_root, payload)
            )
            coverage = cache_coverage(project_root, payload)
            bars = _estimated_bars(spec.base_request.start_time, spec.base_request.end_time, spec.base_request.duration_seconds)
            return jsonify(
                {
                    "combinations": spec.combination_count if is_path else len(spec.combinations),
                    "estimated_bars": bars,
                    "cache": coverage,
                    "execution_class": (
                        "复用已有信号路径，只运行参数重放"
                        if is_path and spec.action == "matrix" and spec.baseline_run_id
                        else "指标与信号只计算一次，再快速重放路径"
                        if is_path and spec.action == "matrix"
                        else "生成无风控路径和大模型样本"
                        if is_path
                        else _execution_class(spec.grid)
                    ),
                }
            )
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400

    @blueprint.post("/api/backtests/runs")
    def create_run() -> Any:
        try:
            payload = request.get_json(force=True)
            return jsonify(manager.submit(payload)), 202
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400

    @blueprint.get("/api/backtests/runs")
    def list_runs() -> Any:
        return jsonify({"runs": manager.list(request.args.get("limit", 30, type=int))})

    @blueprint.get("/api/backtests/runs/<run_id>")
    def get_run(run_id: str) -> Any:
        return _manager_response(lambda: manager.get(run_id))

    @blueprint.get("/api/backtests/runs/<run_id>/request")
    def get_run_request(run_id: str) -> Any:
        return _manager_response(lambda: manager.request_payload(run_id))

    @blueprint.get("/api/backtests/runs/<run_id>/result")
    def get_result(run_id: str) -> Any:
        return _manager_response(lambda: manager.result(run_id))

    @blueprint.get("/api/backtests/runs/<run_id>/report")
    def get_report(run_id: str) -> Any:
        return _manager_response(lambda: manager.report(run_id))

    @blueprint.get("/api/backtests/runs/<run_id>/chart")
    def get_chart(run_id: str) -> Any:
        return _manager_response(lambda: manager.chart_preview(run_id))

    @blueprint.get("/api/backtests/runs/<run_id>/artifacts")
    def get_artifacts(run_id: str) -> Any:
        return _manager_response(lambda: {"artifacts": manager.artifacts(run_id)})

    @blueprint.get("/api/backtests/runs/<run_id>/artifacts/<path:artifact_name>")
    def download_artifact(run_id: str, artifact_name: str) -> Any:
        try:
            path = manager.artifact_path(run_id, artifact_name)
            return send_file(path, as_attachment=True, download_name=path.name)
        except FileNotFoundError as exc:
            return jsonify({"error": str(exc)}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    return blueprint


def _manager_response(callback) -> Any:
    try:
        return jsonify(callback())
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


def _estimated_bars(start_time: str, end_time: str, duration_seconds: int) -> int | None:
    if not start_time or not end_time:
        return None
    import pandas as pd

    start = pd.Timestamp(start_time)
    end = pd.Timestamp(end_time)
    return max(int((end - start).total_seconds() // max(duration_seconds, 1)) + 1, 0)


def _execution_class(grid: dict[str, list[Any]]) -> str:
    if any(key.startswith("indicator.") for key in grid):
        return "指标与信号需要按组合重算"
    if grid:
        return "行情只加载一次，逐组合执行风控撮合"
    return "单次完整回测"
