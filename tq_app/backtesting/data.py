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


def fetch_bitget_candles(
    *,
    project_root: Path,
    symbol: str,
    product_type: str,
    duration_seconds: int,
    data_length: int,
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
    start_time = end_time - requested_count * duration_ms

    rows: list[list[Any]] = []
    seen: set[int] = set()
    cursor = start_time
    while cursor < end_time:
        chunk_end = min(cursor + MAX_CANDLE_LIMIT * duration_ms, end_time)
        limit = max(int((chunk_end - cursor) // duration_ms), 1)
        payload = _bitget_get_json(
            "/api/v2/mix/market/candles",
            {
                "symbol": symbol.upper(),
                "productType": product_type.upper(),
                "granularity": granularity,
                "kLineType": kline_type.upper(),
                "startTime": str(cursor),
                "endTime": str(chunk_end),
                "limit": str(limit),
            },
            project_root=project_root,
        )
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
    return rows_to_frame(rows).tail(requested_count).reset_index(drop=True)


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
