from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv

from tq_app.data_sources.bitget import (
    BITGET_GRANULARITY_MAP,
    MAX_CANDLE_LIMIT,
    _bitget_get_json,
)


HISTORY_CANDLE_LIMIT = 200


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

    rows: list[list[Any]] = []
    seen: set[int] = set()
    cursor = max(start_time - duration_ms, 0) if start_time_ms is not None else start_time
    while cursor < end_time:
        use_history_endpoint = start_time_ms is not None
        limit_per_request = HISTORY_CANDLE_LIMIT if use_history_endpoint else MAX_CANDLE_LIMIT
        path = "/api/v2/mix/market/history-candles" if use_history_endpoint else "/api/v2/mix/market/candles"
        chunk_end = min(cursor + limit_per_request * duration_ms, end_time)
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
