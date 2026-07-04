from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv

from tq_app.data_sources.binance import (
    BINANCE_INTERVAL_MAP,
    MAX_KLINE_LIMIT as BINANCE_MAX_KLINE_LIMIT,
    _binance_get_json,
)
from tq_app.data_sources.bitget import (
    BITGET_GRANULARITY_MAP,
    MAX_CANDLE_LIMIT,
    _bitget_get_json,
)


HISTORY_CANDLE_LIMIT = 200
HISTORY_MAX_TIME_RANGE_MS = 90 * 24 * 60 * 60 * 1000
MIN_VALID_CANDLE_TIME_MS = 946_684_800_000


def fetch_market_candles(
    *,
    provider: str,
    project_root: Path,
    symbol: str,
    product_type: str,
    duration_seconds: int,
    data_length: int,
    start_time_ms: int | None = None,
    end_time_ms: int | None = None,
    kline_type: str = "MARKET",
    cache_enabled: bool = False,
    cache_dir: Path | None = None,
) -> pd.DataFrame:
    provider_name = (provider or "binance").strip().lower()
    if provider_name == "binance":
        return fetch_binance_candles(
            project_root=project_root,
            symbol=symbol,
            product_type=product_type,
            duration_seconds=duration_seconds,
            data_length=data_length,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
            kline_type=kline_type,
            cache_enabled=cache_enabled,
            cache_dir=cache_dir,
        )
    if provider_name == "bitget":
        return fetch_bitget_candles(
            project_root=project_root,
            symbol=symbol,
            product_type=product_type,
            duration_seconds=duration_seconds,
            data_length=data_length,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
            kline_type=kline_type,
            cache_enabled=cache_enabled,
            cache_dir=cache_dir,
        )
    raise RuntimeError(f"未知回测行情 provider: {provider}")


def fetch_binance_candles(
    *,
    project_root: Path,
    symbol: str,
    product_type: str,
    duration_seconds: int,
    data_length: int,
    start_time_ms: int | None = None,
    end_time_ms: int | None = None,
    kline_type: str = "MARKET",
    cache_enabled: bool = False,
    cache_dir: Path | None = None,
) -> pd.DataFrame:
    load_dotenv(project_root / ".env")
    interval = BINANCE_INTERVAL_MAP.get(duration_seconds)
    if interval is None:
        raise RuntimeError(f"Binance 暂不支持 {duration_seconds} 秒周期。")
    if kline_type.upper() != "MARKET":
        raise RuntimeError("Binance 回测当前只支持 MARKET K 线。")

    duration_ms = int(duration_seconds) * 1000
    requested_count = max(int(data_length), 1)
    end_time = end_time_ms or (int(time.time() * 1000) // duration_ms) * duration_ms
    start_time = start_time_ms if start_time_ms is not None else end_time - requested_count * duration_ms
    if start_time >= end_time:
        raise RuntimeError("回测开始时间必须早于结束时间。")

    if cache_enabled:
        cache_path = _cache_path(
            project_root=project_root,
            cache_dir=cache_dir,
            symbol=symbol,
            product_type=f"BINANCE_{product_type}",
            duration_seconds=duration_seconds,
            kline_type=kline_type,
        )
        cached_frame = _read_cache_frame(cache_path)
        cached_slice = _slice_frame(cached_frame, start_time, end_time, requested_count, start_time_ms)
        if _cache_covers(cached_slice, start_time, end_time, duration_ms, requested_count, start_time_ms):
            return cached_slice
        fetched_frames = [
            _fetch_binance_candles_online(
                project_root=project_root,
                symbol=symbol,
                duration_seconds=duration_seconds,
                requested_count=_range_count(missing_start, missing_end, duration_ms),
                start_time=missing_start,
                end_time=missing_end,
                start_time_ms=missing_start,
            )
            for missing_start, missing_end in _missing_ranges(cached_frame, start_time, end_time, duration_ms)
        ]
        frame = _merge_frames(cached_frame, *fetched_frames)
        _write_cache_frame(cache_path, frame)
        final_slice = _slice_frame(frame, start_time, end_time, requested_count, start_time_ms)
        if not _cache_covers(final_slice, start_time, end_time, duration_ms, requested_count, start_time_ms):
            _assert_time_range_covered(final_slice, start_time, end_time, duration_ms, symbol)
        return final_slice

    return _fetch_binance_candles_online(
        project_root=project_root,
        symbol=symbol,
        duration_seconds=duration_seconds,
        requested_count=requested_count,
        start_time=start_time,
        end_time=end_time,
        start_time_ms=start_time_ms,
    )


def _fetch_binance_candles_online(
    *,
    project_root: Path,
    symbol: str,
    duration_seconds: int,
    requested_count: int,
    start_time: int,
    end_time: int,
    start_time_ms: int | None,
) -> pd.DataFrame:
    interval = BINANCE_INTERVAL_MAP.get(duration_seconds)
    if interval is None:
        raise RuntimeError(f"Binance 暂不支持 {duration_seconds} 秒周期。")
    duration_ms = int(duration_seconds) * 1000
    rows: list[list[Any]] = []
    seen: set[int] = set()
    cursor = max(start_time - duration_ms, 0) if start_time_ms is not None else start_time
    cursor = (cursor // duration_ms) * duration_ms
    effective_end_time = (end_time // duration_ms) * duration_ms
    if effective_end_time <= cursor:
        effective_end_time = cursor + duration_ms
    while cursor < effective_end_time:
        limit = min(BINANCE_MAX_KLINE_LIMIT, max(int((effective_end_time - cursor) // duration_ms), 1))
        payload = _binance_get_json(
            "/fapi/v1/klines",
            {
                "symbol": symbol.upper(),
                "interval": interval,
                "startTime": cursor,
                "endTime": effective_end_time,
                "limit": limit,
            },
            project_root=project_root,
        )
        batch = payload if isinstance(payload, list) else []
        if not batch:
            break
        for item in batch:
            if not item or len(item) < 6:
                continue
            ts = int(item[0])
            if ts in seen or ts < start_time or ts > end_time:
                continue
            seen.add(ts)
            rows.append(item)
        next_cursor = int(batch[-1][0]) + duration_ms
        if next_cursor <= cursor:
            break
        cursor = next_cursor

    if not rows:
        raise RuntimeError(f"Binance 中暂无 {symbol} 的可用 K 线。")
    frame = rows_to_frame(rows)
    if start_time_ms is None:
        return frame.tail(requested_count).reset_index(drop=True)
    _assert_time_range_covered(frame, start_time, end_time, duration_ms, symbol)
    return frame.reset_index(drop=True)


def fetch_bitget_candles(
    *,
    project_root: Path,
    symbol: str,
    product_type: str,
    duration_seconds: int,
    data_length: int,
    start_time_ms: int | None = None,
    end_time_ms: int | None = None,
    kline_type: str = "MARKET",
    cache_enabled: bool = False,
    cache_dir: Path | None = None,
) -> pd.DataFrame:
    load_dotenv(project_root / ".env")
    granularity = BITGET_GRANULARITY_MAP.get(duration_seconds)
    if granularity is None:
        raise RuntimeError(f"Bitget 暂不支持 {duration_seconds} 秒周期。")

    duration_ms = int(duration_seconds) * 1000
    requested_count = max(int(data_length), 1)
    end_time = end_time_ms or (int(time.time() * 1000) // duration_ms) * duration_ms
    start_time = start_time_ms if start_time_ms is not None else end_time - requested_count * duration_ms
    if start_time >= end_time:
        raise RuntimeError("回测开始时间必须早于结束时间。")

    if cache_enabled:
        cache_path = _cache_path(
            project_root=project_root,
            cache_dir=cache_dir,
            symbol=symbol,
            product_type=product_type,
            duration_seconds=duration_seconds,
            kline_type=kline_type,
        )
        cached_frame = _read_cache_frame(cache_path)
        cached_slice = _slice_frame(cached_frame, start_time, end_time, requested_count, start_time_ms)
        if _cache_covers(cached_slice, start_time, end_time, duration_ms, requested_count, start_time_ms):
            return cached_slice

        fetched_frames = [
            _fetch_bitget_candles_online(
                project_root=project_root,
                symbol=symbol,
                product_type=product_type,
                duration_seconds=duration_seconds,
                requested_count=_range_count(missing_start, missing_end, duration_ms),
                start_time=missing_start,
                end_time=missing_end,
                start_time_ms=missing_start,
                kline_type=kline_type,
            )
            for missing_start, missing_end in _missing_ranges(
                cached_frame,
                start_time,
                end_time,
                duration_ms,
            )
        ]
        frame = _merge_frames(cached_frame, *fetched_frames)
        _write_cache_frame(cache_path, frame)
        final_slice = _slice_frame(frame, start_time, end_time, requested_count, start_time_ms)
        if not _cache_covers(final_slice, start_time, end_time, duration_ms, requested_count, start_time_ms):
            _assert_time_range_covered(final_slice, start_time, end_time, duration_ms, symbol)
        return final_slice

    return _fetch_bitget_candles_online(
        project_root=project_root,
        symbol=symbol,
        product_type=product_type,
        duration_seconds=duration_seconds,
        requested_count=requested_count,
        start_time=start_time,
        end_time=end_time,
        start_time_ms=start_time_ms,
        kline_type=kline_type,
    )


def _fetch_bitget_candles_online(
    *,
    project_root: Path,
    symbol: str,
    product_type: str,
    duration_seconds: int,
    requested_count: int,
    start_time: int,
    end_time: int,
    start_time_ms: int | None,
    kline_type: str,
) -> pd.DataFrame:
    granularity = BITGET_GRANULARITY_MAP.get(duration_seconds)
    if granularity is None:
        raise RuntimeError(f"Bitget 暂不支持 {duration_seconds} 秒周期。")
    duration_ms = int(duration_seconds) * 1000
    rows: list[list[Any]] = []
    seen: set[int] = set()
    cursor_start = max(start_time - duration_ms, 0) if start_time_ms is not None else start_time
    cursor = _floor_bitget_time_ms(cursor_start, duration_ms, granularity)
    effective_end_time = _floor_bitget_time_ms(end_time, duration_ms, granularity)
    if effective_end_time <= cursor:
        effective_end_time = cursor + duration_ms
    while cursor < effective_end_time:
        use_history_endpoint = start_time_ms is not None
        limit_per_request = HISTORY_CANDLE_LIMIT if use_history_endpoint else MAX_CANDLE_LIMIT
        path = "/api/v2/mix/market/history-candles" if use_history_endpoint else "/api/v2/mix/market/candles"
        max_chunk_span = min(limit_per_request * duration_ms, HISTORY_MAX_TIME_RANGE_MS) if use_history_endpoint else limit_per_request * duration_ms
        chunk_end = min(cursor + max_chunk_span, effective_end_time)
        chunk_end = max(_floor_bitget_time_ms(chunk_end, duration_ms, granularity), cursor + duration_ms)
        limit = max(int((chunk_end - cursor) // duration_ms), 1)
        params = {
            "symbol": symbol.upper(),
            "productType": product_type.upper(),
            "granularity": granularity,
            "startTime": str(cursor),
            "endTime": str(chunk_end),
            "limit": str(limit),
        }
        if not use_history_endpoint:
            params["kLineType"] = kline_type.upper()
        elif kline_type.upper() != "MARKET":
            raise RuntimeError("长区间历史回测当前只支持 MARKET K 线；MARK/INDEX 需要接入 Bitget 对应的历史标记价/指数价接口。")
        payload = _bitget_get_json(path, params, project_root=project_root)
        batch = payload.get("data", []) if isinstance(payload, dict) else payload
        for item in batch:
            if not item or len(item) < 6:
                continue
            ts = int(item[0])
            if ts in seen or ts < start_time or ts > end_time:
                continue
            seen.add(ts)
            rows.append(item)
        cursor = chunk_end

    if not rows:
        raise RuntimeError(f"Bitget 中暂无 {symbol} 的可用 K 线。")
    frame = rows_to_frame(rows)
    if start_time_ms is None:
        return frame.tail(requested_count).reset_index(drop=True)
    _assert_time_range_covered(frame, start_time, end_time, duration_ms, symbol)
    return frame.reset_index(drop=True)


def rows_to_frame(rows: list[list[Any]]) -> pd.DataFrame:
    normalized_rows = [list(item[:7]) for item in rows if item and len(item) >= 6]
    if not normalized_rows:
        return pd.DataFrame(columns=["datetime", "open", "high", "low", "close", "volume"])
    frame = pd.DataFrame(
        normalized_rows,
        columns=["timestamp", "open", "high", "low", "close", "volume", "quote_volume"],
    )
    frame["datetime"] = pd.to_datetime(frame["timestamp"].astype("int64"), unit="ms", utc=True)
    for column in ["open", "high", "low", "close", "volume"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["open", "high", "low", "close"])
    frame = frame.sort_values("datetime").drop_duplicates(subset=["datetime"], keep="last")
    return frame[["datetime", "open", "high", "low", "close", "volume"]].reset_index(drop=True)


def _cache_path(
    *,
    project_root: Path,
    cache_dir: Path | None,
    symbol: str,
    product_type: str,
    duration_seconds: int,
    kline_type: str,
) -> Path:
    root = cache_dir if cache_dir is not None else project_root / "data_cache" / "backtest_klines"
    if not root.is_absolute():
        root = project_root / root
    filename = f"{product_type.upper()}_{symbol.upper()}_{duration_seconds}s_{kline_type.upper()}.csv"
    return root / filename


def _read_cache_frame(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["datetime", "open", "high", "low", "close", "volume"])
    frame = pd.read_csv(path)
    if frame.empty or "timestamp" not in frame.columns:
        return pd.DataFrame(columns=["datetime", "open", "high", "low", "close", "volume"])
    frame["timestamp"] = pd.to_numeric(frame["timestamp"], errors="coerce")
    frame = frame.dropna(subset=["timestamp"])
    frame = frame[frame["timestamp"] >= MIN_VALID_CANDLE_TIME_MS]
    if frame.empty:
        return pd.DataFrame(columns=["datetime", "open", "high", "low", "close", "volume"])
    frame["datetime"] = pd.to_datetime(frame["timestamp"].astype("int64"), unit="ms", utc=True)
    for column in ["open", "high", "low", "close", "volume"]:
        if column not in frame.columns:
            frame[column] = pd.NA
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["open", "high", "low", "close"])
    frame = frame.sort_values("datetime").drop_duplicates(subset=["datetime"], keep="last")
    return frame[["datetime", "open", "high", "low", "close", "volume"]].reset_index(drop=True)


def _write_cache_frame(path: Path, frame: pd.DataFrame) -> None:
    if frame.empty:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    output = frame.copy()
    output["timestamp"] = _datetime_ms(output["datetime"])
    output = output.sort_values("timestamp").drop_duplicates(subset=["timestamp"], keep="last")
    temp_path = path.with_suffix(path.suffix + ".tmp")
    output[["timestamp", "open", "high", "low", "close", "volume"]].to_csv(temp_path, index=False)
    temp_path.replace(path)


def _merge_frames(*frames_to_merge: pd.DataFrame) -> pd.DataFrame:
    frames = [frame for frame in frames_to_merge if not frame.empty]
    if not frames:
        return pd.DataFrame(columns=["datetime", "open", "high", "low", "close", "volume"])
    frame = pd.concat(frames, ignore_index=True)
    frame = frame.sort_values("datetime").drop_duplicates(subset=["datetime"], keep="last")
    return frame[["datetime", "open", "high", "low", "close", "volume"]].reset_index(drop=True)


def _slice_frame(
    frame: pd.DataFrame,
    start_time: int,
    end_time: int,
    requested_count: int,
    start_time_ms: int | None,
) -> pd.DataFrame:
    if frame.empty:
        return frame
    timestamps = _datetime_ms(frame["datetime"])
    sliced = frame[(timestamps >= start_time) & (timestamps <= end_time)]
    if start_time_ms is None:
        sliced = sliced.tail(requested_count)
    return sliced.reset_index(drop=True)


def _cache_covers(
    frame: pd.DataFrame,
    start_time: int,
    end_time: int,
    duration_ms: int,
    requested_count: int,
    start_time_ms: int | None,
) -> bool:
    if frame.empty:
        return False
    timestamps = _datetime_ms(frame["datetime"])
    first_ms = int(timestamps.iloc[0])
    last_ms = int(timestamps.iloc[-1])
    if not _has_regular_spacing(timestamps, duration_ms):
        return False
    if start_time_ms is None:
        return len(frame) >= requested_count and first_ms <= start_time + duration_ms and last_ms >= end_time - duration_ms
    return first_ms <= start_time + duration_ms and last_ms >= end_time - duration_ms


def _missing_ranges(
    frame: pd.DataFrame,
    start_time: int,
    end_time: int,
    duration_ms: int,
) -> list[tuple[int, int]]:
    sliced = _slice_frame(frame, start_time, end_time, requested_count=10**12, start_time_ms=start_time)
    if sliced.empty:
        return [(start_time, end_time)]
    timestamps = _datetime_ms(sliced["datetime"]).sort_values().reset_index(drop=True)
    ranges: list[tuple[int, int]] = []
    first_ms = int(timestamps.iloc[0])
    if first_ms > start_time + duration_ms:
        ranges.append((start_time, first_ms))

    for index in range(1, len(timestamps)):
        previous_ms = int(timestamps.iloc[index - 1])
        current_ms = int(timestamps.iloc[index])
        if current_ms - previous_ms > duration_ms:
            ranges.append((previous_ms + duration_ms, current_ms))

    last_ms = int(timestamps.iloc[-1])
    if last_ms < end_time - duration_ms:
        ranges.append((last_ms + duration_ms, end_time))
    return [(left, right) for left, right in ranges if left < right]


def _range_count(start_time: int, end_time: int, duration_ms: int) -> int:
    return max(int((end_time - start_time) // duration_ms) + 1, 1)


def _floor_bitget_time_ms(timestamp_ms: int, duration_ms: int, granularity: str) -> int:
    safe_duration = max(int(duration_ms), 1)
    # Bitget's plain 1D/12H/6H candles are anchored to UTC+8. The separate
    # "*utc" granularities use UTC anchors, but this project requests plain
    # granularities such as 1D.
    anchor = 0 if str(granularity).lower().endswith("utc") else -8 * 60 * 60 * 1000
    return int((timestamp_ms - anchor) // safe_duration * safe_duration + anchor)


def _datetime_ms(values: pd.Series) -> pd.Series:
    return pd.to_datetime(values, utc=True).map(lambda value: int(value.timestamp() * 1000))


def _has_regular_spacing(timestamps: pd.Series, duration_ms: int) -> bool:
    if len(timestamps) <= 1:
        return True
    gaps = timestamps.diff().dropna()
    return bool((gaps <= duration_ms).all())


def _assert_time_range_covered(frame: pd.DataFrame, start_time: int, end_time: int, duration_ms: int, symbol: str) -> None:
    if frame.empty:
        raise RuntimeError(f"Bitget 中暂无 {symbol} 指定区间的可用 K 线。")
    first_ms = int(pd.Timestamp(frame.iloc[0]["datetime"]).timestamp() * 1000)
    last_ms = int(pd.Timestamp(frame.iloc[-1]["datetime"]).timestamp() * 1000)
    if first_ms > start_time + duration_ms:
        raise RuntimeError(
            "Bitget 返回的历史 K 线没有覆盖请求起点："
            f"requested_start={pd.Timestamp(start_time, unit='ms', tz='UTC')} "
            f"actual_start={pd.Timestamp(first_ms, unit='ms', tz='UTC')}"
        )
    if last_ms < end_time - duration_ms:
        raise RuntimeError(
            "Bitget 返回的历史 K 线没有覆盖请求终点："
            f"requested_end={pd.Timestamp(end_time, unit='ms', tz='UTC')} "
            f"actual_end={pd.Timestamp(last_ms, unit='ms', tz='UTC')}"
        )
