from __future__ import annotations

import argparse
import json
import signal
import time
from dataclasses import asdict
from pathlib import Path

from dotenv import load_dotenv

from tq_app.live_trading import LiveTradingConfig, LiveTradingEngine
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


def parse_args() -> argparse.Namespace:
    load_dotenv(runtime_project_root() / ".env")
    parser = argparse.ArgumentParser(description="Run one Bitget live-trading decision from computed chart signals.")
    parser.add_argument("--provider", default=env_default_str("TQ_DEFAULT_PROVIDER", DEFAULT_PROVIDER), choices=[DEFAULT_PROVIDER])
    parser.add_argument("--symbol", default=env_default_str("TQ_DEFAULT_SYMBOL", DEFAULT_SYMBOL))
    parser.add_argument("--duration", type=int, default=env_default_int("TQ_DEFAULT_DURATION_SECONDS", DEFAULT_DURATION_SECONDS))
    parser.add_argument("--length", type=int, default=env_default_int("TQ_DEFAULT_DATA_LENGTH", DEFAULT_DATA_LENGTH))
    parser.add_argument("--bar-mode", default=env_default_str("TQ_DEFAULT_BAR_MODE", DEFAULT_BAR_MODE), choices=["time"])
    parser.add_argument("--range-ticks", type=int, default=env_default_int("TQ_DEFAULT_RANGE_TICKS", DEFAULT_RANGE_TICKS))
    parser.add_argument("--brick-length", type=int, default=env_default_int("TQ_DEFAULT_BRICK_LENGTH", DEFAULT_BRICK_LENGTH))
    parser.add_argument("--refresh-ms", type=int, default=env_default_int("TQ_DEFAULT_REFRESH_MS", DEFAULT_REFRESH_MS))
    parser.add_argument("--continuous", action="store_true", help="常驻运行，收到新行情后连续执行实盘决策。")
    parser.add_argument("--poll-timeout", type=float, default=15.0, help="常驻模式等待行情更新的超时时间，单位秒。")
    parser.add_argument("--heartbeat-seconds", type=float, default=300.0, help="常驻模式心跳日志间隔，单位秒。")
    return parser.parse_args()


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
    decision = engine.evaluate_snapshot(snapshot)
    return snapshot, decision


def print_execution(decision: object, result: object) -> None:
    print(json.dumps({"decision": asdict(decision), "result": asdict(result)}, ensure_ascii=False, default=str, indent=2))


def main() -> None:
    args = parse_args()
    project_root = runtime_project_root()
    load_dotenv(project_root / ".env")

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
        if not args.continuous:
            _snapshot, decision = evaluate_snapshot(service, engine, args)
            result = engine.execute_decision(decision)
            print_execution(decision, result)
            return

        shutdown_requested = False

        def request_shutdown(signum=None, frame=None) -> None:
            nonlocal shutdown_requested
            shutdown_requested = True
            signal_name = signal.Signals(signum).name if signum is not None else "KeyboardInterrupt"
            print(f"收到退出信号: {signal_name}，正在停止常驻实盘执行...")

        for item in (signal.SIGINT, signal.SIGTERM):
            signal.signal(item, request_shutdown)

        print(f"常驻实盘执行已启动: provider={args.provider} symbol={args.symbol} duration={args.duration}s")
        engine.send_startup_email(symbol=args.symbol, duration_seconds=args.duration, continuous=True)
        last_version: int | None = None
        last_evaluated_bar_time: int | None = None
        last_heartbeat_at = time.monotonic()

        while not shutdown_requested:
            try:
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
                next_version = service.wait_for_update(
                    symbol=args.symbol,
                    provider=args.provider,
                    duration_seconds=args.duration,
                    bar_mode=args.bar_mode,
                    range_ticks=args.range_ticks,
                    brick_length=args.brick_length,
                    data_length=args.length,
                    last_version=last_version,
                    timeout=max(args.poll_timeout, 1.0),
                )
                if next_version != last_version:
                    break
                now = time.monotonic()
                if args.heartbeat_seconds > 0 and now - last_heartbeat_at >= args.heartbeat_seconds:
                    print(json.dumps({"heartbeat": True, "version": last_version, "ts": int(time.time() * 1000)}, ensure_ascii=False))
                    last_heartbeat_at = now
    finally:
        service.stop()


if __name__ == "__main__":
    main()
