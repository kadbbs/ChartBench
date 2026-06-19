from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from tq_app.backtesting import BacktestConfig, BacktestEngine, build_strategy
from tq_app.backtesting.data import fetch_bitget_candles
from tq_app.config_profiles import load_layered_env
from tq_app.live_trading import LiveTradingConfig
from web_tq_chart import DEFAULT_DATA_LENGTH, DEFAULT_DURATION_SECONDS, DEFAULT_PROVIDER, DEFAULT_SYMBOL, env_default_int, env_default_str, runtime_project_root


def parse_args() -> argparse.Namespace:
    project_root = runtime_project_root()
    load_layered_env(project_root)
    parser = argparse.ArgumentParser(description="Run K-line level backtest with pluggable strategies.")
    parser.add_argument("--provider", default=env_default_str("TQ_DEFAULT_PROVIDER", DEFAULT_PROVIDER), choices=[DEFAULT_PROVIDER])
    parser.add_argument("--symbol", default=env_default_str("TQ_DEFAULT_SYMBOL", DEFAULT_SYMBOL))
    parser.add_argument("--duration", type=int, default=env_default_int("TQ_DEFAULT_DURATION_SECONDS", DEFAULT_DURATION_SECONDS))
    parser.add_argument("--length", type=int, default=env_default_int("TQ_DEFAULT_DATA_LENGTH", DEFAULT_DATA_LENGTH))
    parser.add_argument("--strategy", default="live_decision", help="回测策略名。默认复用当前实盘策略。")
    parser.add_argument("--product-type", default=env_default_str("LIVE_TRADING_PRODUCT_TYPE", "USDT-FUTURES"))
    parser.add_argument("--kline-type", default=env_default_str("BITGET_KLINE_TYPE", "MARKET"))
    parser.add_argument("--end-time", default="", help="回测结束时间，支持毫秒时间戳或 ISO 时间；为空则使用当前时间。")
    parser.add_argument("--initial-equity", type=float, default=10_000.0)
    parser.add_argument("--risk-per-trade", type=float, default=0.01)
    parser.add_argument("--order-size", type=float, default=None, help="固定下单数量；默认读取 LIVE_TRADING_ORDER_SIZE，空则按权益比例兜底。")
    parser.add_argument("--fee-rate", type=float, default=0.0006)
    parser.add_argument("--slippage-rate", type=float, default=0.0)
    parser.add_argument("--warmup-bars", type=int, default=80)
    parser.add_argument("--output-dir", default="backtest_outputs/latest")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    project_root = runtime_project_root()
    live_config = LiveTradingConfig.from_env(project_root)
    strategy = build_strategy(args.strategy, project_root, live_config)
    end_time_ms = _parse_end_time_ms(args.end_time)

    bars = fetch_bitget_candles(
        project_root=project_root,
        symbol=args.symbol,
        product_type=args.product_type,
        duration_seconds=args.duration,
        data_length=args.length,
        end_time_ms=end_time_ms,
        kline_type=args.kline_type,
    )

    htf_bars = None
    if live_config.htf_hull_filter_enabled:
        htf_length = max(int(args.length * args.duration / live_config.htf_hull_duration_seconds) + 120, 200)
        htf_bars = fetch_bitget_candles(
            project_root=project_root,
            symbol=args.symbol,
            product_type=args.product_type,
            duration_seconds=live_config.htf_hull_duration_seconds,
            data_length=htf_length,
            end_time_ms=end_time_ms,
            kline_type=args.kline_type,
        )

    config = BacktestConfig(
        symbol=args.symbol.upper(),
        provider=args.provider,
        duration_seconds=args.duration,
        initial_equity=args.initial_equity,
        risk_per_trade=args.risk_per_trade,
        order_size=_resolve_order_size(args.order_size, live_config),
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


def _resolve_order_size(arg_value: float | None, live_config: LiveTradingConfig) -> float:
    if arg_value is not None:
        return max(float(arg_value), 0.0)
    try:
        return max(float(live_config.size), 0.0)
    except (TypeError, ValueError):
        return 0.0


if __name__ == "__main__":
    main()
