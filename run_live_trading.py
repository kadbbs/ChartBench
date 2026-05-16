from __future__ import annotations

import argparse
import json
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
    runtime_project_root,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one Bitget live-trading decision from computed chart signals.")
    parser.add_argument("--provider", default=DEFAULT_PROVIDER, choices=[DEFAULT_PROVIDER])
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--duration", type=int, default=DEFAULT_DURATION_SECONDS)
    parser.add_argument("--length", type=int, default=DEFAULT_DATA_LENGTH)
    parser.add_argument("--bar-mode", default=DEFAULT_BAR_MODE, choices=["time"])
    parser.add_argument("--range-ticks", type=int, default=DEFAULT_RANGE_TICKS)
    parser.add_argument("--brick-length", type=int, default=DEFAULT_BRICK_LENGTH)
    parser.add_argument("--refresh-ms", type=int, default=DEFAULT_REFRESH_MS)
    return parser.parse_args()


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
        engine = LiveTradingEngine(project_root, LiveTradingConfig.from_env(project_root))
        decision = engine.evaluate_snapshot(snapshot)
        result = engine.execute_decision(decision)
        print(json.dumps({"decision": asdict(decision), "result": asdict(result)}, ensure_ascii=False, default=str, indent=2))
    finally:
        service.stop()


if __name__ == "__main__":
    main()
