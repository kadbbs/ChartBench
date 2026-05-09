from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen

import pandas as pd
from dotenv import load_dotenv

from .base import DataSource

BINANCE_FAPI_BASE = "https://fapi.binance.com"
BINANCE_GRANULARITY_MAP = {
    60: "1m",
    300: "5m",
    900: "15m",
    1800: "30m",
    3600: "1h",
    7200: "2h",
    14400: "4h",
    21600: "6h",
    43200: "12h",
    86400: "1d",
}
MAX_KLINE_LIMIT = 1500
LATEST_KLINE_LIMIT = 5


def _configured_rest_bases() -> list[str]:
    raw = os.getenv("BINANCE_FAPI_BASES", "").strip()
    if raw:
        bases = [item.strip().rstrip("/") for item in raw.split(",") if item.strip()]
        if bases:
            return bases
    single = os.getenv("BINANCE_FAPI_BASE", "").strip().rstrip("/")
    if single:
        return [single]
    return [
        BINANCE_FAPI_BASE,
        "https://fapi1.binance.com",
        "https://fapi2.binance.com",
        "https://fapi3.binance.com",
    ]


def _binance_get_json(path: str, params: dict[str, Any] | None = None, project_root: Path | None = None) -> Any:
    if project_root is not None:
        load_dotenv(project_root / ".env")
    query = urlencode({key: value for key, value in (params or {}).items() if value is not None})
    errors: list[str] = []
    for base in _configured_rest_bases():
        url = f"{base}{path}?{query}" if query else f"{base}{path}"
        try:
            with urlopen(url, timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            errors.append(f"{base}: {exc}")
    joined_errors = " | ".join(errors)
    raise RuntimeError(f"Binance REST 请求失败: {joined_errors}")


def load_binance_contract_catalog(project_root: Path) -> list[dict[str, Any]]:
    payload = _binance_get_json("/fapi/v1/exchangeInfo", project_root=project_root)
    contracts: list[dict[str, Any]] = []
    priority = {"BTCUSDT": 0, "ETHUSDT": 1, "BNBUSDT": 2, "SOLUSDT": 3}
    for item in payload.get("symbols", []) or []:
        symbol = str(item.get("symbol", "")).strip().upper()
        if not symbol:
            continue
        if str(item.get("status", "")).upper() != "TRADING":
            continue
        contract_type = str(item.get("contractType", "")).upper()
        if contract_type not in {"PERPETUAL", "CURRENT_QUARTER", "NEXT_QUARTER", "TRADIFI_PERPETUAL"}:
            continue
        base = str(item.get("baseAsset", "")).strip()
        quote = str(item.get("quoteAsset", "")).strip()
        label_name = f"{base}/{quote}" if base and quote else symbol
        price_tick = None
        volume_multiple = None
        for filt in item.get("filters", []) or []:
            filter_type = str(filt.get("filterType", "")).upper()
            if filter_type == "PRICE_FILTER" and filt.get("tickSize") is not None:
                price_tick = float(filt["tickSize"])
            if filter_type == "LOT_SIZE" and filt.get("stepSize") is not None:
                volume_multiple = float(filt["stepSize"])
        contracts.append(
            {
                "symbol": symbol,
                "name": label_name,
                "label": f"{label_name} · BINANCE {contract_type}",
                "exchange_id": "BINANCE",
                "product_id": contract_type,
                "price_tick": price_tick,
                "volume_multiple": volume_multiple,
            }
        )
    contracts.sort(
        key=lambda item: (
            priority.get(item["symbol"], 999),
            0 if item["product_id"] in {"PERPETUAL", "TRADIFI_PERPETUAL"} else 1,
            item["symbol"],
        )
    )
    return contracts


class BinanceDataSource(DataSource):
    provider_name = "binance"

    def __init__(
        self,
        symbol: str,
        duration_seconds: int,
        data_length: int,
        brick_length: int,
        refresh_ms: int,
        bar_mode: str,
        range_ticks: int,
    ) -> None:
        self.symbol = symbol.upper()
        self.duration_seconds = duration_seconds
        self.data_length = data_length
        self.brick_length = brick_length
        self.refresh_ms = refresh_ms
        self.bar_mode = bar_mode
        self.range_ticks = range_ticks
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._stop_event = threading.Event()
        self._bars: pd.DataFrame | None = None
        self._error: str | None = None
        self._version = 0
        self._last_refresh_at = 0.0

    def start(self) -> None:
        self._stop_event.clear()
        self._ready.set()

    def stop(self) -> None:
        self._stop_event.set()

    def wait_for_update(self, last_version: int | None, timeout: float) -> int:
        time.sleep(max(timeout, 0))
        with self._lock:
            return self._version

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "version": self._version,
                "last_refresh_at": self._last_refresh_at or None,
                "stream_state": "rest_polling",
                "stream_url": None,
                "error": self._error,
            }

    def get_bars(self) -> pd.DataFrame:
        self.start()
        self._ready.wait(timeout=10)
        refresh_interval = max(float(self.refresh_ms or 0) / 1000.0, 0.5)
        has_cached_bars = False
        with self._lock:
            if self._error:
                raise RuntimeError(self._error)
            if self._bars is not None and not self._bars.empty:
                if time.monotonic() - self._last_refresh_at < refresh_interval:
                    return self._bars.copy()
                has_cached_bars = True

        frame = self._fetch_latest_bars() if has_cached_bars else self._fetch_history_bars()
        with self._lock:
            if has_cached_bars and self._bars is not None and not self._bars.empty:
                merged = pd.concat([self._bars, frame], ignore_index=True)
                merged = merged.sort_values("datetime").drop_duplicates(subset=["datetime"], keep="last")
                self._bars = merged.tail(self.data_length).reset_index(drop=True)
            else:
                self._bars = frame.copy()
            self._error = None
            self._last_refresh_at = time.monotonic()
            self._version += 1
        self._ready.set()
        with self._lock:
            return self._bars.copy()

    def _fetch_history_bars(self) -> pd.DataFrame:
        if self.bar_mode != "time":
            raise RuntimeError("Binance 数据源当前只支持时间 K 线。")
        interval = BINANCE_GRANULARITY_MAP.get(self.duration_seconds)
        if interval is None:
            raise RuntimeError(f"Binance 暂不支持 {self.duration_seconds} 秒周期。")

        remaining = max(int(self.data_length), 1)
        end_time = int(time.time() * 1000)
        rows: list[list[Any]] = []
        seen: set[int] = set()
        while remaining > 0:
            limit = min(remaining, MAX_KLINE_LIMIT)
            batch = _binance_get_json(
                "/fapi/v1/klines",
                {
                    "symbol": self.symbol,
                    "interval": interval,
                    "limit": limit,
                    "endTime": end_time,
                },
            )
            if not batch:
                break
            oldest_ts = None
            for item in batch:
                if not item or len(item) < 6:
                    continue
                ts = int(item[0])
                if ts in seen:
                    continue
                seen.add(ts)
                oldest_ts = ts if oldest_ts is None else min(oldest_ts, ts)
                rows.append(item[:6])
            if oldest_ts is None:
                break
            end_time = oldest_ts - 1
            remaining = self.data_length - len(rows)
            if len(batch) < limit:
                break

        if not rows:
            raise RuntimeError(f"Binance 中暂无 {self.symbol} 的可用 K 线。")
        frame = self._rows_to_frame(rows)
        if frame.empty:
            raise RuntimeError(f"Binance 中暂无 {self.symbol} 的可用 K 线。")
        return frame.tail(self.data_length).reset_index(drop=True)

    def _fetch_latest_bars(self) -> pd.DataFrame:
        if self.bar_mode != "time":
            raise RuntimeError("Binance 数据源当前只支持时间 K 线。")
        interval = BINANCE_GRANULARITY_MAP.get(self.duration_seconds)
        if interval is None:
            raise RuntimeError(f"Binance 暂不支持 {self.duration_seconds} 秒周期。")
        rows = _binance_get_json(
            "/fapi/v1/klines",
            {
                "symbol": self.symbol,
                "interval": interval,
                "limit": LATEST_KLINE_LIMIT,
            },
        )
        frame = self._rows_to_frame(rows)
        if frame.empty:
            raise RuntimeError(f"Binance 中暂无 {self.symbol} 的可用 K 线。")
        return frame.reset_index(drop=True)

    @staticmethod
    def _rows_to_frame(rows: list[list[Any]]) -> pd.DataFrame:
        normalized_rows = [list(item[:6]) for item in rows if item and len(item) >= 6]
        if not normalized_rows:
            return pd.DataFrame(columns=["datetime", "open", "high", "low", "close", "volume"])
        frame = pd.DataFrame(
            normalized_rows,
            columns=["timestamp", "open", "high", "low", "close", "volume"],
        )
        frame["datetime"] = pd.to_datetime(frame["timestamp"].astype("int64"), unit="ms", utc=True)
        for column in ["open", "high", "low", "close", "volume"]:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame = frame.dropna(subset=["open", "high", "low", "close"])
        frame = frame.sort_values("datetime").drop_duplicates(subset=["datetime"], keep="last")
        return frame[["datetime", "open", "high", "low", "close", "volume"]].reset_index(drop=True)
