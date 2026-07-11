from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from .data import fetch_market_candles


@dataclass(frozen=True, slots=True)
class BacktestMarketRequest:
    provider: str
    symbol: str
    product_type: str
    duration_seconds: int
    data_length: int
    start_time_ms: int | None
    end_time_ms: int | None
    kline_type: str = "MARKET"
    cache_enabled: bool = False
    cache_dir: Path = Path("data_cache/backtest_klines")


@dataclass(slots=True)
class PreparedBacktestMarket:
    bars: pd.DataFrame
    htf_bars: pd.DataFrame | None
    reentry_htf_bars: pd.DataFrame | None
    primary_htf_duration_seconds: int | None
    reentry_htf_duration_seconds: int | None


def prepare_backtest_market(
    *,
    project_root: Path,
    request: BacktestMarketRequest,
    live_config: Any,
    strategy: Any,
) -> PreparedBacktestMarket:
    bars = _fetch(project_root, request, request.duration_seconds, request.data_length, request.start_time_ms)
    htf_bars = None
    reentry_htf_bars = None
    primary_duration: int | None = None
    reentry_duration: int | None = None
    if bool(live_config.htf_hull_filter_enabled):
        primary_duration = int(strategy.primary_htf_duration_seconds)
        htf_bars = _fetch_higher_timeframe(project_root, request, primary_duration)
        raw_reentry_duration = strategy.reentry_confirmation_duration_seconds
        if raw_reentry_duration is not None:
            reentry_duration = int(raw_reentry_duration)
            reentry_htf_bars = _fetch_higher_timeframe(project_root, request, reentry_duration)
    return PreparedBacktestMarket(
        bars=bars,
        htf_bars=htf_bars,
        reentry_htf_bars=reentry_htf_bars,
        primary_htf_duration_seconds=primary_duration,
        reentry_htf_duration_seconds=reentry_duration,
    )


def parse_time_ms(raw: str) -> int | None:
    text = str(raw or "").strip()
    if not text:
        return None
    if text.isdigit():
        value = int(text)
        return value if value > 10_000_000_000 else value * 1000
    timestamp = pd.Timestamp(text)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("Asia/Shanghai")
    return int(timestamp.tz_convert("UTC").timestamp() * 1000)


def resolve_data_length(
    *,
    requested_length: int,
    duration_seconds: int,
    start_time_ms: int | None,
    end_time_ms: int | None,
) -> int:
    if start_time_ms is None or end_time_ms is None:
        return requested_length
    duration_ms = max(int(duration_seconds), 1) * 1000
    return max(int((end_time_ms - start_time_ms) // duration_ms) + 1, 1)


def _fetch_higher_timeframe(
    project_root: Path,
    request: BacktestMarketRequest,
    duration_seconds: int,
) -> pd.DataFrame:
    length = max(int(request.data_length * request.duration_seconds / duration_seconds) + 120, 200)
    start_time_ms = None
    if request.start_time_ms is not None:
        start_time_ms = max(request.start_time_ms - 120 * duration_seconds * 1000, 0)
    return _fetch(project_root, request, duration_seconds, length, start_time_ms)


def _fetch(
    project_root: Path,
    request: BacktestMarketRequest,
    duration_seconds: int,
    data_length: int,
    start_time_ms: int | None,
) -> pd.DataFrame:
    return fetch_market_candles(
        provider=request.provider,
        project_root=project_root,
        symbol=request.symbol,
        product_type=request.product_type,
        duration_seconds=duration_seconds,
        data_length=data_length,
        start_time_ms=start_time_ms,
        end_time_ms=request.end_time_ms,
        kline_type=request.kline_type,
        cache_enabled=request.cache_enabled,
        cache_dir=request.cache_dir,
    )
