from __future__ import annotations

import argparse
import json
import os
import signal
import time
from dataclasses import asdict
from pathlib import Path

from tq_app.config_profiles import available_profiles, effective_config_snapshot, load_layered_env
from tq_app.live_trading import LiveTradingConfig, LiveTradingEngine, create_ticker_websocket
from tq_app.service import MarketDataService
from web_tq_chart import (
    DEFAULT_BAR_MODE,
    DEFAULT_BRICK_LENGTH,
    DEFAULT_DATA_LENGTH,
    DEFAULT_DURATION_SECONDS,
    DEFAULT_PROVIDER,
    DEFAULT_RANGE_TICKS,
    DEFAULT_REFRESH_MS,
    DEFAULT_SYMBOL,
    env_default_int,
    env_default_str,
    runtime_project_root,
)


CONFIG_SNAPSHOT_KEYS = [
    "LIVE_TRADING_MODE",
    "LIVE_TRADING_PROVIDER",
    "LIVE_TRADING_PRODUCT_TYPE",
    "LIVE_TRADING_MARGIN_AMOUNT",
    "LIVE_TRADING_LEVERAGE",
    "LIVE_TRADING_MARGIN_MODE",
    "LIVE_TRADING_POSITION_MODE",
    "LIVE_TRADING_AUTO_TRANSFER_FROM_SPOT",
    "LIVE_TRADING_AUTO_TRANSFER_MULTIPLIER",
    "LIVE_TRADING_AUTO_TRANSFER_BUFFER",
    "LIVE_TRADING_STRATEGY",
    "LIVE_TRADING_SIGNAL_MODE",
    "LIVE_TRADING_USE_CLOSED_BAR",
    "LIVE_TRADING_HTF_HULL_FILTER_ENABLED",
    "LIVE_TRADING_HTF_HULL_DURATION_SECONDS",
    "LIVE_TRADING_ENTRY_TIME_FILTER_ENABLED",
    "LIVE_TRADING_POSITION_SYNC_ENABLED",
    "LIVE_TRADING_RISK_EXITS_ENABLED",
    "LIVE_TRADING_RISK_CHECK_INTERVAL_SECONDS",
    "LIVE_TRADING_RISK_WEBSOCKET_TICKER_ENABLED",
    "LIVE_TRADING_RISK_WEBSOCKET_TICKER_STALE_SECONDS",
    "LIVE_TRADING_RISK_ERROR_EMAIL_COOLDOWN_SECONDS",
    "LIVE_TRADING_EXCHANGE_DISASTER_SL_ENABLED",
    "LIVE_TRADING_RISK_CLOSE_MANAGED_SIZE_ONLY",
    "LIVE_TRADING_RISK_PRICE_SOURCE",
    "LIVE_TRADING_RISK_STARTUP_CHECK_BARS_5M",
    "LIVE_TRADING_RISK_STARTUP_MAX_FAVORABLE_POINTS",
    "LIVE_TRADING_RISK_STARTUP_CURRENT_POINTS",
    "LIVE_TRADING_RISK_DISASTER_STOP_POINTS",
    "LIVE_TRADING_RISK_BREAKEVEN_TRIGGER_POINTS",
    "LIVE_TRADING_RISK_BREAKEVEN_STOP_POINTS",
    "LIVE_TRADING_RISK_TRAILING_TRIGGER_1_POINTS",
    "LIVE_TRADING_RISK_TRAILING_PROTECT_1_RATIO",
    "LIVE_TRADING_RISK_TRAILING_TRIGGER_2_POINTS",
    "LIVE_TRADING_RISK_TRAILING_PROTECT_2_RATIO",
    "LIVE_TRADING_RISK_TRAILING_TRIGGER_3_POINTS",
    "LIVE_TRADING_RISK_TRAILING_PROTECT_3_RATIO",
    "LIVE_TRADING_EMAIL_ENABLED",
    "LIVE_TRADING_EMAIL_TO",
    "TQ_DEFAULT_PROVIDER",
    "TQ_DEFAULT_SYMBOL",
    "TQ_DEFAULT_DURATION_SECONDS",
    "TQ_DEFAULT_DATA_LENGTH",
    "BINANCE_API_KEY",
    "BINANCE_API_SECRET",
    "BITGET_API_KEY",
    "BITGET_API_SECRET",
    "BITGET_API_PASSPHRASE",
]


def parse_args() -> argparse.Namespace:
    project_root = runtime_project_root()
    bootstrap = argparse.ArgumentParser(add_help=False)
    bootstrap.add_argument("--profile", default="")
    bootstrap.add_argument("--mode", choices=["off", "email", "dry_run", "live"], default="")
    early_args, _unknown = bootstrap.parse_known_args()
    load_layered_env(project_root, early_args.profile, profile_overrides_env=bool(early_args.profile))
    if early_args.mode:
        os.environ["LIVE_TRADING_MODE"] = early_args.mode

    parser = argparse.ArgumentParser(description="Run one Binance/Bitget live-trading decision from computed chart signals.")
    parser.add_argument("--profile", default=early_args.profile, help="运行配置档案名称，对应 config/profiles/<name>.yaml")
    parser.add_argument("--list-profiles", action="store_true", help="列出可用配置档案后退出。")
    parser.add_argument("--show-config", action="store_true", help="打印最终生效的非密钥配置后退出。")
    parser.add_argument("--mode", choices=["off", "email", "dry_run", "live"], default=early_args.mode, help="临时覆盖 LIVE_TRADING_MODE。")
    parser.add_argument("--provider", default=env_default_str("LIVE_TRADING_PROVIDER", env_default_str("TQ_DEFAULT_PROVIDER", DEFAULT_PROVIDER)), choices=["binance", "bitget"])
    parser.add_argument("--symbol", default=env_default_str("TQ_DEFAULT_SYMBOL", DEFAULT_SYMBOL))
    parser.add_argument("--duration", type=int, default=env_default_int("TQ_DEFAULT_DURATION_SECONDS", DEFAULT_DURATION_SECONDS))
    parser.add_argument("--length", type=int, default=env_default_int("TQ_DEFAULT_DATA_LENGTH", DEFAULT_DATA_LENGTH))
    parser.add_argument("--bar-mode", default=env_default_str("TQ_DEFAULT_BAR_MODE", DEFAULT_BAR_MODE), choices=["time"])
    parser.add_argument("--range-ticks", type=int, default=env_default_int("TQ_DEFAULT_RANGE_TICKS", DEFAULT_RANGE_TICKS))
    parser.add_argument("--brick-length", type=int, default=env_default_int("TQ_DEFAULT_BRICK_LENGTH", DEFAULT_BRICK_LENGTH))
    parser.add_argument("--refresh-ms", type=int, default=env_default_int("TQ_DEFAULT_REFRESH_MS", DEFAULT_REFRESH_MS))
    parser.add_argument("--continuous", action="store_true", help="常驻运行，收到新行情后连续执行实盘决策。")
    parser.add_argument("--preflight", action="store_true", help="只执行 Binance/Bitget 实盘预检查，不进行信号评估或下单。")
    parser.add_argument("--poll-timeout", type=float, default=15.0, help="常驻模式等待行情更新的超时时间，单位秒。")
    parser.add_argument("--heartbeat-seconds", type=float, default=300.0, help="常驻模式心跳日志间隔，单位秒。")
    args = parser.parse_args()
    if args.mode:
        os.environ["LIVE_TRADING_MODE"] = args.mode
    return args


def evaluate_snapshot(
    service: MarketDataService,
    engine: LiveTradingEngine,
    args: argparse.Namespace,
) -> tuple[dict, object]:
    snapshot = service.get_snapshot(
        provider=args.provider,
        symbol=args.symbol,
        duration_seconds=args.duration,
        bar_mode=args.bar_mode,
        range_ticks=args.range_ticks,
        brick_length=args.brick_length,
        data_length=args.length,
        indicator_ids=["merged_dkx_hull_ut", "stc", "macd"],
    )
    if engine.config.htf_hull_filter_enabled:
        snapshot["higher_timeframe"] = service.get_snapshot(
            provider=args.provider,
            symbol=args.symbol,
            duration_seconds=engine.config.htf_hull_duration_seconds,
            bar_mode=args.bar_mode,
            range_ticks=args.range_ticks,
            brick_length=args.brick_length,
            data_length=args.length,
            indicator_ids=["merged_dkx_hull_ut", "stc"],
        )
    decision = engine.evaluate_snapshot(snapshot)
    return snapshot, decision


def print_execution(decision: object, result: object) -> None:
    print(json.dumps({"decision": asdict(decision), "result": asdict(result)}, ensure_ascii=False, default=str, indent=2))


def _sync_live_provider_env(provider: str) -> None:
    provider_name = str(provider or "").strip().lower()
    if provider_name not in {"binance", "bitget"}:
        raise ValueError("实盘 provider 只支持 binance / bitget；天勤仅支持图表模块。")
    os.environ["LIVE_TRADING_PROVIDER"] = provider_name
    product_type = os.getenv("LIVE_TRADING_PRODUCT_TYPE", "").strip().upper()
    if provider_name == "bitget" and product_type == "UM-FUTURES":
        os.environ["LIVE_TRADING_PRODUCT_TYPE"] = os.getenv("BITGET_DEFAULT_PRODUCT_TYPE", "USDT-FUTURES").strip().upper() or "USDT-FUTURES"
    elif provider_name == "binance" and product_type == "USDT-FUTURES":
        os.environ["LIVE_TRADING_PRODUCT_TYPE"] = os.getenv("BINANCE_DEFAULT_PRODUCT_TYPE", "UM-FUTURES").strip().upper() or "UM-FUTURES"


def main() -> None:
    args = parse_args()
    project_root = runtime_project_root()
    load_layered_env(project_root, args.profile, profile_overrides_env=bool(args.profile))
    if args.mode:
        os.environ["LIVE_TRADING_MODE"] = args.mode
    _sync_live_provider_env(args.provider)

    if args.list_profiles:
        print(json.dumps({"profiles": available_profiles(project_root)}, ensure_ascii=False, indent=2))
        return

    if args.show_config:
        print(
            json.dumps(
                {
                    "profile": args.profile,
                    "available_profiles": available_profiles(project_root),
                    "effective_config": effective_config_snapshot(CONFIG_SNAPSHOT_KEYS),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    service = MarketDataService(
        provider=args.provider,
        symbol=args.symbol,
        duration_seconds=args.duration,
        data_length=args.length,
        brick_length=args.brick_length,
        refresh_ms=args.refresh_ms,
        project_root=project_root,
        bar_mode=args.bar_mode,
        range_ticks=args.range_ticks,
    )
    try:
        service.start()
        engine = LiveTradingEngine(project_root, LiveTradingConfig.from_env(project_root))
        if args.preflight:
            preflight = engine.run_preflight(symbol=args.symbol, configure_account=True)
            print(json.dumps(asdict(preflight), ensure_ascii=False, default=str, indent=2))
            if not preflight.ok:
                raise SystemExit(1)
            return

        if not args.continuous:
            _snapshot, decision = evaluate_snapshot(service, engine, args)
            result = engine.execute_decision(decision)
            print_execution(decision, result)
            return

        shutdown_requested = False
        ticker_stream = None

        def request_shutdown(signum=None, frame=None) -> None:
            nonlocal shutdown_requested
            shutdown_requested = True
            signal_name = signal.Signals(signum).name if signum is not None else "KeyboardInterrupt"
            print(f"收到退出信号: {signal_name}，正在停止常驻实盘执行...")

        for item in (signal.SIGINT, signal.SIGTERM):
            signal.signal(item, request_shutdown)

        print(f"常驻实盘执行已启动: provider={args.provider} symbol={args.symbol} duration={args.duration}s")
        engine.send_startup_email(symbol=args.symbol, duration_seconds=args.duration, continuous=True)
        if engine.config.risk_exits_enabled and engine.config.risk_websocket_ticker_enabled and engine.config.mode == "live":
            ticker_stream = create_ticker_websocket(
                provider=engine.config.provider,
                symbol=args.symbol,
                product_type=engine.config.product_type,
                logger=engine.logger,
            )
            ticker_stream.start()
            print(json.dumps({"ticker_websocket": "started", "symbol": args.symbol.upper(), "ts": int(time.time() * 1000)}, ensure_ascii=False))
        last_version: int | None = None
        last_ticker_version: int | None = None
        last_evaluated_bar_time: int | None = None
        last_heartbeat_at = time.monotonic()
        last_position_sync_at = 0.0
        last_rest_risk_check_at = 0.0

        while not shutdown_requested:
            try:
                now = time.monotonic()
                latest_ticker = ticker_stream.latest() if ticker_stream else None
                ticker_map = {args.symbol.upper(): latest_ticker} if latest_ticker else None
                if now - last_position_sync_at >= max(engine.config.position_sync_interval_seconds, 1.0):
                    engine.check_runtime_state(tickers=ticker_map, sync_positions=True)
                    last_position_sync_at = now
                elif now - last_rest_risk_check_at >= engine.config.runtime_check_interval_seconds():
                    engine.check_runtime_state(tickers=ticker_map, sync_positions=False)
                    last_rest_risk_check_at = now
                snapshot, decision = evaluate_snapshot(service, engine, args)
                stream_meta = snapshot.get("stream") or {}
                last_version = int(stream_meta.get("version") or 0)
                bar_time = decision.bar_time
                if bar_time != last_evaluated_bar_time:
                    result = engine.execute_decision(decision)
                    print_execution(decision, result)
                    last_evaluated_bar_time = bar_time
            except Exception as exc:
                print(json.dumps({"error": str(exc), "ts": int(time.time() * 1000)}, ensure_ascii=False))
                time.sleep(min(max(args.poll_timeout, 1.0), 30.0))
                continue

            while not shutdown_requested:
                runtime_check_interval = engine.config.runtime_check_interval_seconds()
                ticker_updated = False
                if ticker_stream is not None:
                    next_ticker_version = ticker_stream.wait_for_update(
                        last_ticker_version,
                        timeout=min(runtime_check_interval, 1.0),
                    )
                    if next_ticker_version != last_ticker_version:
                        last_ticker_version = next_ticker_version
                        ticker_updated = True
                        latest_ticker = ticker_stream.latest()
                        if latest_ticker:
                            engine.check_runtime_state(
                                tickers={args.symbol.upper(): latest_ticker},
                                sync_positions=False,
                            )
                next_version = service.wait_for_update(
                    symbol=args.symbol,
                    provider=args.provider,
                    duration_seconds=args.duration,
                    bar_mode=args.bar_mode,
                    range_ticks=args.range_ticks,
                    brick_length=args.brick_length,
                    data_length=args.length,
                    last_version=last_version,
                    timeout=0.0 if ticker_updated else max(min(args.poll_timeout, runtime_check_interval), 1.0),
                )
                if next_version != last_version:
                    break
                now = time.monotonic()
                if args.heartbeat_seconds > 0 and now - last_heartbeat_at >= args.heartbeat_seconds:
                    print(json.dumps({"heartbeat": True, "version": last_version, "ts": int(time.time() * 1000)}, ensure_ascii=False))
                    last_heartbeat_at = now
                latest_ticker = ticker_stream.latest() if ticker_stream else None
                ticker_map = {args.symbol.upper(): latest_ticker} if latest_ticker else None
                if now - last_position_sync_at >= max(engine.config.position_sync_interval_seconds, 1.0):
                    engine.check_runtime_state(tickers=ticker_map, sync_positions=True)
                    last_position_sync_at = now
                elif now - last_rest_risk_check_at >= engine.config.runtime_check_interval_seconds():
                    engine.check_runtime_state(tickers=ticker_map, sync_positions=False)
                    last_rest_risk_check_at = now
    finally:
        if "ticker_stream" in locals() and ticker_stream is not None:
            ticker_stream.stop()
        service.stop()


if __name__ == "__main__":
    main()
