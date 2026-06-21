from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from tq_app.backtesting import BacktestConfig, BacktestEngine, build_strategy
from tq_app.backtesting.engine import DEFAULT_BACKTEST_FEE_RATE
from tq_app.backtesting.data import fetch_bitget_candles
from tq_app.config_profiles import available_backtest_profiles, load_backtest_profile, load_layered_env
from tq_app.live_trading import LiveTradingConfig
from web_tq_chart import DEFAULT_DATA_LENGTH, DEFAULT_DURATION_SECONDS, DEFAULT_PROVIDER, DEFAULT_SYMBOL, env_default_int, env_default_str, runtime_project_root


def parse_args() -> argparse.Namespace:
    project_root = runtime_project_root()
    load_layered_env(project_root)
    bootstrap = argparse.ArgumentParser(add_help=False)
    bootstrap.add_argument("--profile", default="")
    early_args, _unknown = bootstrap.parse_known_args()
    profile_values = load_backtest_profile(project_root, early_args.profile)

    def profile_str(key: str, default: str) -> str:
        return str(profile_values.get(key, default))

    def profile_int(key: str, default: int) -> int:
        try:
            return int(profile_values.get(key, default))
        except (TypeError, ValueError):
            return default

    def profile_float(key: str, default: float) -> float:
        try:
            return float(profile_values.get(key, default))
        except (TypeError, ValueError):
            return default

    def profile_bool(key: str, default: bool) -> bool:
        raw = profile_values.get(key)
        if raw is None:
            return default
        return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}

    parser = argparse.ArgumentParser(description="Run K-line level backtest with pluggable strategies.")
    parser.add_argument("--profile", default=early_args.profile, help="回测配置档案名称，对应 config/backtests/<name>.yaml")
    parser.add_argument("--list-profiles", action="store_true", help="列出可用回测配置档案后退出。")
    parser.add_argument("--provider", default=profile_str("provider", env_default_str("TQ_DEFAULT_PROVIDER", DEFAULT_PROVIDER)), choices=[DEFAULT_PROVIDER])
    parser.add_argument("--symbol", default=profile_str("symbol", env_default_str("TQ_DEFAULT_SYMBOL", DEFAULT_SYMBOL)))
    parser.add_argument("--duration", type=int, default=profile_int("duration", env_default_int("TQ_DEFAULT_DURATION_SECONDS", DEFAULT_DURATION_SECONDS)))
    parser.add_argument("--length", type=int, default=profile_int("length", env_default_int("TQ_DEFAULT_DATA_LENGTH", DEFAULT_DATA_LENGTH)))
    parser.add_argument("--strategy", default=profile_str("strategy", "live_decision"), help="回测策略名。默认复用当前实盘策略。")
    parser.add_argument("--product-type", default=profile_str("product_type", env_default_str("LIVE_TRADING_PRODUCT_TYPE", "USDT-FUTURES")))
    parser.add_argument("--kline-type", default=profile_str("kline_type", env_default_str("BITGET_KLINE_TYPE", "MARKET")))
    parser.add_argument("--start-time", default=profile_str("start_time", ""), help="回测开始时间，支持毫秒/秒时间戳或 ISO 时间；配合 --end-time 指定完整区间。")
    parser.add_argument("--end-time", default=profile_str("end_time", ""), help="回测结束时间，支持毫秒时间戳或 ISO 时间；为空则使用当前时间。")
    parser.add_argument("--initial-equity", type=float, default=profile_float("initial_equity", 1_000.0), help="回测初始权益，默认 1000U。")
    parser.add_argument("--risk-per-trade", type=float, default=profile_float("risk_per_trade", 0.01))
    parser.add_argument("--fee-rate", type=float, default=profile_float("fee_rate", DEFAULT_BACKTEST_FEE_RATE))
    parser.add_argument("--slippage-rate", type=float, default=profile_float("slippage_rate", 0.0))
    parser.add_argument("--warmup-bars", type=int, default=profile_int("warmup_bars", 80))
    parser.add_argument("--output-dir", default=profile_str("output_dir", "backtest_outputs/latest"))
    parser.add_argument("--cache", action="store_true", default=profile_bool("cache_enabled", False), help="启用回测 K 线本地缓存；默认关闭，不影响在线回测。")
    parser.add_argument("--no-cache", action="store_true", help="即使配置文件开启缓存，也强制使用在线 K 线。")
    parser.add_argument("--cache-dir", default=profile_str("cache_dir", "data_cache/backtest_klines"), help="回测 K 线缓存目录。")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    project_root = runtime_project_root()
    if args.list_profiles:
        print(json.dumps({"profiles": available_backtest_profiles(project_root)}, ensure_ascii=False, indent=2))
        return
    if args.no_cache:
        args.cache = False
    live_config = LiveTradingConfig.from_env(project_root)
    strategy = build_strategy(args.strategy, project_root, live_config)
    start_time_ms = _parse_time_ms(args.start_time)
    end_time_ms = _parse_time_ms(args.end_time)
    if start_time_ms is not None and end_time_ms is None:
        raise SystemExit("--start-time 需要同时指定 --end-time。")
    if start_time_ms is not None and end_time_ms is not None and start_time_ms >= end_time_ms:
        raise SystemExit("--start-time 必须早于 --end-time。")
    data_length = _resolve_data_length(
        requested_length=args.length,
        duration_seconds=args.duration,
        start_time_ms=start_time_ms,
        end_time_ms=end_time_ms,
    )

    bars = fetch_bitget_candles(
        project_root=project_root,
        symbol=args.symbol,
        product_type=args.product_type,
        duration_seconds=args.duration,
        data_length=data_length,
        start_time_ms=start_time_ms,
        end_time_ms=end_time_ms,
        kline_type=args.kline_type,
        cache_enabled=args.cache,
        cache_dir=Path(args.cache_dir),
    )

    htf_bars = None
    if live_config.htf_hull_filter_enabled:
        htf_length = max(int(data_length * args.duration / live_config.htf_hull_duration_seconds) + 120, 200)
        htf_start_time_ms = None
        if start_time_ms is not None:
            htf_start_time_ms = max(start_time_ms - 120 * live_config.htf_hull_duration_seconds * 1000, 0)
        htf_bars = fetch_bitget_candles(
            project_root=project_root,
            symbol=args.symbol,
            product_type=args.product_type,
            duration_seconds=live_config.htf_hull_duration_seconds,
            data_length=htf_length,
            start_time_ms=htf_start_time_ms,
            end_time_ms=end_time_ms,
            kline_type=args.kline_type,
            cache_enabled=args.cache,
            cache_dir=Path(args.cache_dir),
        )

    config = BacktestConfig(
        symbol=args.symbol.upper(),
        provider=args.provider,
        duration_seconds=args.duration,
        initial_equity=args.initial_equity,
        risk_per_trade=args.risk_per_trade,
        margin_amount=1_000.0,
        leverage=10.0,
        fee_rate=args.fee_rate,
        slippage_rate=args.slippage_rate,
        stop_atr_multiplier=float(live_config.stop_atr_multiplier),
        tp1_r_multiple=float(live_config.tp1_r_multiple),
        tp1_size_ratio=float(live_config.tp1_size_ratio),
        tp2_r_multiple=float(live_config.tp2_r_multiple),
        atr_period=live_config.atr_period,
        warmup_bars=args.warmup_bars,
        output_dir=Path(args.output_dir),
    )
    result = BacktestEngine(project_root=project_root, config=config, live_config=live_config, strategy=strategy).run(bars, htf_bars)
    print(json.dumps({"metrics": result.metrics, "output_dir": result.output_dir, "config": result.config}, ensure_ascii=False, default=str, indent=2))


def _parse_end_time_ms(raw: str) -> int | None:
    return _parse_time_ms(raw)


def _parse_time_ms(raw: str) -> int | None:
    text = raw.strip()
    if not text:
        return None
    if text.isdigit():
        value = int(text)
        return value if value > 10_000_000_000 else value * 1000
    timestamp = pd.Timestamp(text)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("Asia/Shanghai")
    return int(timestamp.tz_convert("UTC").timestamp() * 1000)


def _resolve_data_length(
    *,
    requested_length: int,
    duration_seconds: int,
    start_time_ms: int | None,
    end_time_ms: int | None,
) -> int:
    if start_time_ms is None or end_time_ms is None:
        return requested_length
    duration_ms = max(int(duration_seconds), 1) * 1000
    bars = int((end_time_ms - start_time_ms) // duration_ms) + 1
    return max(bars, 1)


if __name__ == "__main__":
    main()
