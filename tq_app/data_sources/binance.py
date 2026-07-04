from __future__ import annotations

import asyncio
import contextlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

import pandas as pd
from dotenv import load_dotenv
from websockets.asyncio.client import connect as ws_connect

from .base import DataSource


BINANCE_FAPI_BASE = "https://fapi.binance.com"
BINANCE_WS_MARKET_BASE = "wss://fstream.binance.com/market"
BINANCE_INTERVAL_MAP = {
    60: "1m",
    180: "3m",
    300: "5m",
    900: "15m",
    1800: "30m",
    3600: "1h",
    7200: "2h",
    14400: "4h",
    21600: "6h",
    28800: "8h",
    43200: "12h",
    86400: "1d",
    259200: "3d",
}
MAX_KLINE_LIMIT = 1500
PUBLIC_HTTP_RETRY_ATTEMPTS = 3
WS_RECV_TIMEOUT_SECONDS = 45
WS_RECONNECT_MIN_DELAY_SECONDS = 1.0
WS_RECONNECT_MAX_DELAY_SECONDS = 30.0
HISTORY_RETRY_MAX_DELAY_SECONDS = 60.0


def _binance_get_json(path: str, params: dict[str, Any] | None = None, project_root: Path | None = None) -> Any:
    if project_root is not None:
        load_dotenv(project_root / ".env")
    base = os.getenv("BINANCE_FAPI_BASE", "").strip().rstrip("/") or BINANCE_FAPI_BASE
    query = urlencode({key: value for key, value in (params or {}).items() if value not in (None, "")})
    url = f"{base}{path}" if not query else f"{base}{path}?{query}"
    last_error: Exception | None = None
    for attempt in range(1, PUBLIC_HTTP_RETRY_ATTEMPTS + 1):
        try:
            with urlopen(url, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            last_error = RuntimeError(f"Binance API {path} HTTP {exc.code}: {body or exc.reason}")
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
        else:
            if isinstance(payload, dict) and int(payload.get("code", 0) or 0) < 0:
                raise RuntimeError(f"Binance API {path} 返回错误 {payload.get('code')}: {payload.get('msg') or payload}")
            return payload
        if attempt < PUBLIC_HTTP_RETRY_ATTEMPTS:
            time.sleep(min(2 ** (attempt - 1), 5))
    raise RuntimeError(f"Binance API {path} 请求失败: {last_error}")


def load_binance_contract_catalog(project_root: Path) -> list[dict[str, Any]]:
    load_dotenv(project_root / ".env")
    payload = _binance_get_json("/fapi/v1/exchangeInfo", project_root=project_root)
    symbols = payload.get("symbols") if isinstance(payload, dict) else []
    contracts: list[dict[str, Any]] = []
    for item in symbols or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("status") or "").upper() != "TRADING":
            continue
        if str(item.get("contractType") or "").upper() != "PERPETUAL":
            continue
        symbol = str(item.get("symbol") or "").upper()
        if not symbol:
            continue
        filters = {str(entry.get("filterType")): entry for entry in item.get("filters") or [] if isinstance(entry, dict)}
        lot_filter = filters.get("LOT_SIZE") or {}
        price_filter = filters.get("PRICE_FILTER") or {}
        base_asset = str(item.get("baseAsset") or "")
        quote_asset = str(item.get("quoteAsset") or "")
        contracts.append(
            {
                "symbol": symbol,
                "name": f"{base_asset}/{quote_asset}".strip("/") or symbol,
                "label": f"{base_asset}/{quote_asset} · BINANCE USD-M PERP" if base_asset and quote_asset else f"{symbol} · BINANCE USD-M PERP",
                "exchange_id": "BINANCE",
                "product_id": "USD-M",
                "symbolStatus": item.get("status"),
                "baseCoin": base_asset,
                "quoteCoin": quote_asset,
                "minTradeNum": lot_filter.get("minQty"),
                "sizeMultiplier": lot_filter.get("stepSize"),
                "priceEndStep": price_filter.get("tickSize"),
                "volumePlace": item.get("quantityPrecision"),
                "pricePlace": item.get("pricePrecision"),
            }
        )
    return sorted(contracts, key=lambda value: value["symbol"])


def load_binance_account_summary(project_root: Path) -> dict[str, Any]:
    load_dotenv(project_root / ".env")
    if not os.getenv("BINANCE_API_KEY", "").strip() or not os.getenv("BINANCE_API_SECRET", "").strip():
        return {}
    return {"exchange": "BINANCE", "product_type": "USD-M", "configured": True}


def rows_to_frame(rows: list[list[Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["datetime", "open", "high", "low", "close", "volume"])
    normalized = [[item[0], item[1], item[2], item[3], item[4], item[5]] for item in rows if item and len(item) >= 6]
    frame = pd.DataFrame(normalized, columns=["timestamp", "open", "high", "low", "close", "volume"])
    frame["datetime"] = pd.to_datetime(frame["timestamp"].astype("int64"), unit="ms", utc=True)
    for column in ["open", "high", "low", "close", "volume"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["open", "high", "low", "close"])
    frame = frame.sort_values("datetime").drop_duplicates(subset=["datetime"], keep="last")
    return frame[["datetime", "open", "high", "low", "close", "volume"]].reset_index(drop=True)


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
        self.product_type = os.getenv("BINANCE_DEFAULT_PRODUCT_TYPE", "").strip().upper() or "UM-FUTURES"
        self.kline_type = os.getenv("BINANCE_KLINE_TYPE", "MARKET").strip().upper() or "MARKET"
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._ready = threading.Event()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._bars: pd.DataFrame | None = None
        self._error: str | None = None
        self._version = 0
        self._last_refresh_at = 0.0
        self._last_update_at: float | None = None
        self._last_message_at: float | None = None
        self._last_kline_at: float | None = None
        self._last_ticker_price: float | None = None
        self._last_ticker_ts: int | None = None
        self._stream_state = "starting"
        self._stream_url: str | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            if self._bars is None or self._bars.empty:
                self._ready.clear()
            self._error = None
            self._stream_state = "starting"
        self._thread = threading.Thread(target=self._run, name=f"binance-{self.symbol}-{self.duration_seconds}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(lambda: None)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

    def wait_for_update(self, last_version: int | None, timeout: float) -> int:
        self.start()
        self._ready.wait(timeout=10)
        with self._condition:
            if last_version is None or self._version != last_version:
                return self._version
            self._condition.wait_for(lambda: self._version != last_version or self._stop_event.is_set(), timeout=timeout)
            return self._version

    def status(self) -> dict[str, Any]:
        with self._lock:
            return self._status_locked()

    def get_bars(self) -> pd.DataFrame:
        frame, _status = self.get_bars_with_status()
        return frame

    def get_bars_with_status(self) -> tuple[pd.DataFrame, dict[str, Any]]:
        self.start()
        self._ready.wait(timeout=10)
        with self._lock:
            if self._error:
                raise RuntimeError(self._error)
            if self._bars is not None and not self._bars.empty:
                return self._bars.copy(), self._status_locked()
        frame = self._fetch_history_bars()
        with self._lock:
            self._bars = frame.copy()
            self._error = None
            self._last_refresh_at = time.monotonic()
            self._last_update_at = time.time()
            self._version += 1
            status = self._status_locked()
            self._condition.notify_all()
        self._ready.set()
        return frame, status

    def _status_locked(self) -> dict[str, Any]:
        return {
            "version": self._version,
            "last_refresh_at": self._last_refresh_at or None,
            "last_update_at": self._last_update_at,
            "last_message_at": self._last_message_at,
            "last_kline_at": self._last_kline_at,
            "stream_state": self._stream_state,
            "stream_url": self._stream_url,
            "product_type": self.product_type,
            "kline_type": self.kline_type,
            "last_ticker_price": self._last_ticker_price,
            "last_ticker_ts": self._last_ticker_ts,
            "error": self._error,
        }

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
        await self._load_initial_history()
        if self._stop_event.is_set():
            return
        if self.kline_type != "MARKET":
            with self._lock:
                self._stream_state = "history_ready"
            return
        interval = BINANCE_INTERVAL_MAP.get(self.duration_seconds)
        if not interval:
            return
        stream_url = f"{os.getenv('BINANCE_WS_MARKET_BASE', '').strip().rstrip('/') or BINANCE_WS_MARKET_BASE}/ws/{self.symbol.lower()}@kline_{interval}"
        failure_count = 0
        while not self._stop_event.is_set():
            try:
                with self._lock:
                    self._stream_state = "connecting"
                    self._stream_url = stream_url
                async with ws_connect(stream_url, ping_interval=20, close_timeout=1) as websocket:
                    with self._lock:
                        self._stream_state = "connected"
                        self._error = None
                    failure_count = 0
                    while not self._stop_event.is_set():
                        message = await asyncio.wait_for(websocket.recv(), timeout=WS_RECV_TIMEOUT_SECONDS)
                        self._handle_ws_message(message)
            except Exception as exc:
                with self._lock:
                    self._error = None if self._bars is not None else str(exc)
                    self._stream_state = "reconnecting"
                failure_count += 1
                delay = min(WS_RECONNECT_MAX_DELAY_SECONDS, WS_RECONNECT_MIN_DELAY_SECONDS * (2 ** min(failure_count - 1, 5)))
                if await asyncio.to_thread(self._stop_event.wait, delay):
                    break

    async def _load_initial_history(self) -> None:
        failure_count = 0
        while not self._stop_event.is_set():
            try:
                frame = self._fetch_history_bars()
            except Exception as exc:
                failure_count += 1
                delay = min(HISTORY_RETRY_MAX_DELAY_SECONDS, WS_RECONNECT_MIN_DELAY_SECONDS * (2 ** min(failure_count - 1, 6)))
                with self._lock:
                    self._error = str(exc)
                    self._stream_state = "reconnecting"
                    self._condition.notify_all()
                    self._ready.set()
                await asyncio.to_thread(self._stop_event.wait, delay)
                continue
            with self._lock:
                self._bars = frame.copy()
                self._error = None
                self._last_refresh_at = time.monotonic()
                self._last_update_at = time.time()
                self._version += 1
                self._stream_state = "history_ready"
                self._condition.notify_all()
                self._ready.set()
            return

    def _handle_ws_message(self, message: Any) -> None:
        if isinstance(message, bytes):
            message = message.decode("utf-8")
        payload = json.loads(message)
        item = payload.get("k") if isinstance(payload, dict) else None
        if not isinstance(item, dict):
            return
        if str(item.get("s") or "").upper() != self.symbol:
            return
        rows = [[item.get("t"), item.get("o"), item.get("h"), item.get("l"), item.get("c"), item.get("v")]]
        updates = rows_to_frame(rows)
        if updates.empty:
            return
        with self._lock:
            base = self._bars.copy() if self._bars is not None else pd.DataFrame(columns=updates.columns)
            merged = pd.concat([base, updates], ignore_index=True)
            self._last_ticker_price = float(updates.iloc[-1]["close"])
            self._last_ticker_ts = int(pd.Timestamp(updates.iloc[-1]["datetime"]).timestamp() * 1000)
            self._commit_bars_locked(merged, from_kline=True)

    def _fetch_history_bars(self) -> pd.DataFrame:
        if self.bar_mode != "time":
            raise RuntimeError("Binance 数据源当前只支持时间 K 线。")
        interval = BINANCE_INTERVAL_MAP.get(self.duration_seconds)
        if interval is None:
            raise RuntimeError(f"Binance 暂不支持 {self.duration_seconds} 秒周期。")
        duration_ms = int(self.duration_seconds) * 1000
        requested_count = max(int(self.data_length), 1)
        end_time = (int(time.time() * 1000) // duration_ms) * duration_ms
        start_time = end_time - requested_count * duration_ms
        rows: list[list[Any]] = []
        seen: set[int] = set()
        cursor = start_time
        while cursor < end_time:
            limit = min(MAX_KLINE_LIMIT, max(int((end_time - cursor) // duration_ms), 1))
            payload = _binance_get_json(
                "/fapi/v1/klines",
                {"symbol": self.symbol, "interval": interval, "startTime": cursor, "endTime": end_time, "limit": limit},
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
            last_ts = int(batch[-1][0])
            next_cursor = last_ts + duration_ms
            if next_cursor <= cursor:
                break
            cursor = next_cursor
        if not rows:
            raise RuntimeError(f"Binance 中暂无 {self.symbol} 的可用 K 线。")
        frame = rows_to_frame(rows)
        if frame.empty:
            raise RuntimeError(f"Binance 中暂无 {self.symbol} 的可用 K 线。")
        return frame.tail(self.data_length).reset_index(drop=True)

    def _commit_bars_locked(self, frame: pd.DataFrame, from_kline: bool = False) -> None:
        frame = frame.sort_values("datetime").drop_duplicates(subset=["datetime"], keep="last")
        self._bars = frame.tail(self.data_length).reset_index(drop=True)
        self._error = None
        self._version += 1
        self._last_message_at = time.time()
        self._last_update_at = self._last_message_at
        if from_kline:
            self._last_kline_at = self._last_message_at
        self._stream_state = "live"
        self._condition.notify_all()
        self._ready.set()
