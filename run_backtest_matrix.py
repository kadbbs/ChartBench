from __future__ import annotations

import argparse
import csv
import itertools
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pandas as pd

from run_backtest import _parse_time_ms, _resolve_data_length, runtime_project_root
from tq_app.backtesting import BacktestConfig, BacktestEngine, build_strategy
from tq_app.backtesting.config import build_backtest_live_config
from tq_app.backtesting.data import fetch_market_candles
from tq_app.backtesting.engine import DEFAULT_BACKTEST_FEE_RATE
from tq_app.config_profiles import load_backtest_profile, load_layered_env


MATRIX_DIR = "config/backtest_matrices"
MATRIX_KEYS = (
    "risk_exits_enabled",
    "startup_check_bars_5m",
    "startup_max_favorable_points",
    "startup_current_points",
    "disaster_stop_points",
    "breakeven_trigger_points",
    "breakeven_stop_points",
    "trailing_trigger_1_points",
    "trailing_protect_1_ratio",
    "trailing_trigger_2_points",
    "trailing_protect_2_ratio",
    "trailing_trigger_3_points",
    "trailing_protect_3_ratio",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a parameter matrix for backtest risk controls.")
    parser.add_argument("--matrix", required=True, help="矩阵配置名称，对应 config/backtest_matrices/<name>.yaml")
    parser.add_argument("--top", type=int, default=20, help="命令行输出前 N 个结果。")
    parser.add_argument("--dry-run", action="store_true", help="只解析矩阵并输出组合，不实际执行回测。")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    project_root = runtime_project_root()
    load_layered_env(project_root)
    matrix = _read_flat_yaml(project_root / MATRIX_DIR / f"{args.matrix}.yaml")
    base_profile = str(matrix.get("base_profile") or "").strip()
    if not base_profile:
        raise SystemExit("矩阵配置必须设置 base_profile。")
    profile = load_backtest_profile(project_root, base_profile)
    output_dir = Path(str(matrix.get("output_dir") or f"backtest_outputs/matrix/{args.matrix}"))
    if not output_dir.is_absolute():
        output_dir = project_root / output_dir
    combinations = _matrix_combinations(matrix)
    if args.dry_run:
        print(json.dumps({"matrix": args.matrix, "base_profile": base_profile, "runs": len(combinations), "combinations": combinations}, ensure_ascii=False, indent=2))
        return
    output_dir.mkdir(parents=True, exist_ok=True)

    live_config = build_backtest_live_config(project_root, profile)
    symbol = _str(profile, "symbol", "BTCUSDT").upper()
    provider = _str(profile, "provider", "binance")
    product_type = _str(profile, "product_type", "UM-FUTURES")
    duration = _int(profile, "duration", 300)
    kline_type = _str(profile, "kline_type", "MARKET")
    start_time_ms = _parse_time_ms(_str(profile, "start_time", ""))
    end_time_ms = _parse_time_ms(_str(profile, "end_time", ""))
    length = _resolve_data_length(
        requested_length=_int(profile, "length", 800),
        duration_seconds=duration,
        start_time_ms=start_time_ms,
        end_time_ms=end_time_ms,
    )
    cache_enabled = _bool(profile, "cache_enabled", False)
    cache_dir = Path(_str(profile, "cache_dir", "data_cache/backtest_klines"))

    bars = fetch_market_candles(
        provider=provider,
        project_root=project_root,
        symbol=symbol,
        product_type=product_type,
        duration_seconds=duration,
        data_length=length,
        start_time_ms=start_time_ms,
        end_time_ms=end_time_ms,
        kline_type=kline_type,
        cache_enabled=cache_enabled,
        cache_dir=cache_dir,
    )
    htf_bars = None
    if live_config.htf_hull_filter_enabled:
        htf_length = max(int(length * duration / live_config.htf_hull_duration_seconds) + 120, 200)
        htf_start_time_ms = None
        if start_time_ms is not None:
            htf_start_time_ms = max(start_time_ms - 120 * live_config.htf_hull_duration_seconds * 1000, 0)
        htf_bars = fetch_market_candles(
            provider=provider,
            project_root=project_root,
            symbol=symbol,
            product_type=product_type,
            duration_seconds=live_config.htf_hull_duration_seconds,
            data_length=htf_length,
            start_time_ms=htf_start_time_ms,
            end_time_ms=end_time_ms,
            kline_type=kline_type,
            cache_enabled=cache_enabled,
            cache_dir=cache_dir,
        )

    base_config = BacktestConfig(
        symbol=symbol,
        provider=provider,
        duration_seconds=duration,
        initial_equity=_float(profile, "initial_equity", 20_000.0),
        risk_per_trade=_float(profile, "risk_per_trade", 0.01),
        margin_amount=1_000.0,
        margin_ratio_per_trade=_float(profile, "margin_ratio_per_trade", 0.0),
        leverage=10.0,
        fee_rate=_float(profile, "fee_rate", DEFAULT_BACKTEST_FEE_RATE),
        slippage_rate=_float(profile, "slippage_rate", 0.0),
        stop_atr_multiplier=float(live_config.stop_atr_multiplier),
        tp1_r_multiple=float(live_config.tp1_r_multiple),
        tp1_size_ratio=float(live_config.tp1_size_ratio),
        tp2_r_multiple=float(live_config.tp2_r_multiple),
        atr_period=live_config.atr_period,
        warmup_bars=_int(profile, "warmup_bars", 80),
        risk_exits_enabled=_bool(profile, "risk_exits_enabled", True),
        startup_check_bars_5m=_int(profile, "startup_check_bars_5m", 24),
        startup_max_favorable_points=_float(profile, "startup_max_favorable_points", 300.0),
        startup_current_points=_float(profile, "startup_current_points", -150.0),
        disaster_stop_points=_float(profile, "disaster_stop_points", -1800.0),
        breakeven_trigger_points=_float(profile, "breakeven_trigger_points", 800.0),
        breakeven_stop_points=_float(profile, "breakeven_stop_points", 100.0),
        trailing_trigger_1_points=_float(profile, "trailing_trigger_1_points", 2000.0),
        trailing_protect_1_ratio=_float(profile, "trailing_protect_1_ratio", 0.40),
        trailing_trigger_2_points=_float(profile, "trailing_trigger_2_points", 4000.0),
        trailing_protect_2_ratio=_float(profile, "trailing_protect_2_ratio", 0.50),
        trailing_trigger_3_points=_float(profile, "trailing_trigger_3_points", 8000.0),
        trailing_protect_3_ratio=_float(profile, "trailing_protect_3_ratio", 0.60),
        run_context={
            "profile": base_profile,
            "profile_values": profile,
            "matrix": args.matrix,
            "market_data": {
                "provider": provider,
                "symbol": symbol,
                "product_type": product_type,
                "kline_type": kline_type,
                "duration_seconds": duration,
                "data_length": length,
                "start_time_ms": start_time_ms,
                "end_time_ms": end_time_ms,
                "cache_enabled": cache_enabled,
                "cache_dir": str(cache_dir),
            },
        },
        output_dir=output_dir / "runs",
    )

    rows: list[dict[str, Any]] = []
    for index, params in enumerate(combinations, start=1):
        run_dir = output_dir / "runs" / f"run_{index:04d}"
        config = replace(base_config, output_dir=run_dir, **params)
        strategy = build_strategy(_str(profile, "strategy", "live_decision"), project_root, live_config)
        result = BacktestEngine(project_root=project_root, config=config, live_config=live_config, strategy=strategy).run(bars, htf_bars)
        rows.append(_summary_row(index, params, result.metrics, run_dir))

    ranked = sorted(rows, key=_rank_key, reverse=True)
    _write_summary(output_dir, ranked)
    print(json.dumps({"output_dir": str(output_dir), "runs": len(ranked), "top": ranked[: max(args.top, 0)]}, ensure_ascii=False, indent=2))


def _matrix_combinations(matrix: dict[str, str]) -> list[dict[str, Any]]:
    values: list[tuple[str, list[Any]]] = []
    for key in MATRIX_KEYS:
        if key not in matrix:
            continue
        values.append((key, [_coerce_matrix_value(key, item) for item in _split_list(matrix[key])]))
    if not values:
        return [{}]
    keys = [item[0] for item in values]
    value_lists = [item[1] for item in values]
    return [dict(zip(keys, items)) for items in itertools.product(*value_lists)]


def _summary_row(index: int, params: dict[str, Any], metrics: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    row = {"rank": 0, "run": index, "output_dir": str(run_dir), **params}
    for key in (
        "net_profit",
        "return_pct",
        "max_drawdown",
        "trade_count",
        "win_rate",
        "profit_factor",
        "total_points",
        "total_net_points",
        "best_points",
        "worst_points",
        "total_fees",
    ):
        row[key] = metrics.get(key)
    row["score"] = _score(metrics)
    return row


def _rank_key(row: dict[str, Any]) -> tuple[float, float, float, float]:
    return (
        float(row.get("score") or 0.0),
        float(row.get("total_net_points") or 0.0),
        float(row.get("profit_factor") or 0.0),
        -abs(float(row.get("max_drawdown") or 0.0)),
    )


def _score(metrics: dict[str, Any]) -> float:
    net_points = float(metrics.get("total_net_points") or 0.0)
    max_drawdown_pct = abs(float(metrics.get("max_drawdown") or 0.0)) * 100
    trade_count = int(metrics.get("trade_count") or 0)
    profit_factor = float(metrics.get("profit_factor") or 0.0)
    if trade_count < 20:
        return -1_000_000.0 + net_points
    return net_points - max_drawdown_pct * 50 + profit_factor * 100


def _write_summary(output_dir: Path, rows: list[dict[str, Any]]) -> None:
    ranked_rows = []
    for rank, row in enumerate(rows, start=1):
        ranked = dict(row)
        ranked["rank"] = rank
        ranked_rows.append(ranked)
    (output_dir / "summary.json").write_text(json.dumps(ranked_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    if not ranked_rows:
        return
    with (output_dir / "summary.csv").open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(ranked_rows[0].keys()))
        writer.writeheader()
        writer.writerows(ranked_rows)


def _read_flat_yaml(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise ValueError(f"{path}:{line_number} 只支持 KEY: value 格式")
        key, value = line.split(":", 1)
        values[key.strip()] = _strip_comment(value.strip())
    return values


def _strip_comment(value: str) -> str:
    if not value:
        return ""
    quote = value[0]
    if quote in {"'", '"'} and value.endswith(quote):
        return value[1:-1]
    if " #" in value:
        return value.split(" #", 1)[0].rstrip()
    return value


def _split_list(value: str) -> list[str]:
    return [item.strip() for item in str(value).split(",") if item.strip()]


def _coerce_matrix_value(key: str, value: str) -> bool | int | float:
    if key == "risk_exits_enabled":
        return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}
    if key.endswith("_bars_5m"):
        return int(value)
    return float(value)


def _str(values: dict[str, str], key: str, default: str) -> str:
    return str(values.get(key, default))


def _int(values: dict[str, str], key: str, default: int) -> int:
    try:
        return int(values.get(key, default))
    except (TypeError, ValueError):
        return default


def _float(values: dict[str, str], key: str, default: float) -> float:
    try:
        return float(values.get(key, default))
    except (TypeError, ValueError):
        return default


def _bool(values: dict[str, str], key: str, default: bool) -> bool:
    raw = values.get(key)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


if __name__ == "__main__":
    main()
