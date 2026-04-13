from __future__ import annotations

import asyncio
import contextlib
import json
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen

import pandas as pd
from websockets.asyncio.client import connect as ws_connect

from .base import DataSource

BINANCE_FAPI_BASE = "https://fapi.binance.com"
BINANCE_WS_BASE = "wss://fstream.binance.com/ws"
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
WS_RECV_TIMEOUT_SECONDS = 25
WS_RECONNECT_DELAY_SECONDS = 2


def _binance_get_json(path: str, params: dict[str, Any] | None = None) -> Any:
    query = urlencode({key: value for key, value in (params or {}).items() if value is not None})
    url = f"{BINANCE_FAPI_BASE}{path}?{query}" if query else f"{BINANCE_FAPI_BASE}{path}"
    with urlopen(url, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def load_binance_contract_catalog(_: Path) -> list[dict[str, Any]]:
    payload = _binance_get_json("/fapi/v1/exchangeInfo")
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
        self._thread: threading.Thread | None = None
        self._bars: pd.DataFrame | None = None
        self._error: str | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="binance-data-source", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(lambda: None)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

    def get_bars(self) -> pd.DataFrame:
        self.start()
        self._ready.wait(timeout=10)
        with self._lock:
            if self._error:
                raise RuntimeError(self._error)
            if self._bars is not None and not self._bars.empty:
                return self._bars.copy()

        frame = self._fetch_history_bars()
        with self._lock:
            self._bars = frame.copy()
            self._error = None
        self._ready.set()
        return frame

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self._stream_loop())
        except Exception as exc:
            with self._lock:
                self._error = str(exc)
            self._ready.set()
        finally:
            pending = asyncio.all_tasks(self._loop)
            for task in pending:
                task.cancel()
            with contextlib.suppress(Exception):
                self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self._loop.close()
            self._loop = None

    async def _stream_loop(self) -> None:
        frame = self._fetch_history_bars()
        with self._lock:
            self._bars = frame.copy()
            self._error = None
        self._ready.set()

        interval = BINANCE_GRANULARITY_MAP.get(self.duration_seconds)
        if interval is None:
            return

        stream_url = f"{BINANCE_WS_BASE}/{self.symbol.lower()}@kline_{interval}"
        while not self._stop_event.is_set():
            try:
                async with ws_connect(stream_url, ping_interval=None, close_timeout=1) as websocket:
                    while not self._stop_event.is_set():
                        try:
                            message = await asyncio.wait_for(websocket.recv(), timeout=WS_RECV_TIMEOUT_SECONDS)
                        except asyncio.TimeoutError:
                            await websocket.ping()
                            continue
                        self._handle_ws_message(message)
            except Exception as exc:
                with self._lock:
                    self._error = None if self._bars is not None else str(exc)
                if self._stop_event.wait(WS_RECONNECT_DELAY_SECONDS):
                    break

    def _handle_ws_message(self, message: Any) -> None:
        if isinstance(message, bytes):
            message = message.decode("utf-8")
        if not message:
            return
        payload = json.loads(message)
        data = payload.get("k") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            return
        row = [
            data.get("t"),
            data.get("o"),
            data.get("h"),
            data.get("l"),
            data.get("c"),
            data.get("v"),
        ]
        updates = self._rows_to_frame([row])
        if updates.empty:
            return
        with self._lock:
            base = self._bars.copy() if self._bars is not None else pd.DataFrame(columns=updates.columns)
            merged = pd.concat([base, updates], ignore_index=True)
            merged = merged.sort_values("datetime").drop_duplicates(subset=["datetime"], keep="last")
            self._bars = merged.tail(self.data_length).reset_index(drop=True)
            self._error = None
        self._ready.set()

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
