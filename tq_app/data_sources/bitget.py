from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import os
import threading
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd
from dotenv import load_dotenv
from websockets.asyncio.client import connect as ws_connect

from .base import DataSource

BITGET_API_BASE = "https://api.bitget.com"
BITGET_WS_PUBLIC_URL = "wss://ws.bitget.com/v2/ws/public"
DEFAULT_PRODUCT_TYPES = ["USDT-FUTURES"]
DEFAULT_KLINE_TYPE = "MARKET"
VALID_KLINE_TYPES = {"MARKET", "MARK", "INDEX"}
BITGET_GRANULARITY_MAP = {
    60: "1m",
    180: "3m",
    300: "5m",
    900: "15m",
    1800: "30m",
    3600: "1H",
    7200: "2H",
    14400: "4H",
    21600: "6H",
    43200: "12H",
    86400: "1D",
}
MAX_CANDLE_LIMIT = 1000
MAX_CANDLE_TIME_RANGE_MS = 90 * 24 * 60 * 60 * 1000
WS_RECV_TIMEOUT_SECONDS = 25
WS_RECONNECT_MIN_DELAY_SECONDS = 1
WS_RECONNECT_MAX_DELAY_SECONDS = 30
HISTORY_RETRY_MAX_DELAY_SECONDS = 60
PUBLIC_HTTP_RETRY_ATTEMPTS = 5
PUBLIC_HTTP_RETRY_BASE_DELAY_SECONDS = 0.8
PUBLIC_HTTP_RETRY_MAX_DELAY_SECONDS = 8.0


def _ws_channel_for_duration(duration_seconds: int) -> str | None:
    granularity = BITGET_GRANULARITY_MAP.get(duration_seconds)
    if granularity is None:
        return None
    return f"candle{granularity}"


def _floor_bitget_time_ms(timestamp_ms: int, duration_ms: int, granularity: str) -> int:
    safe_duration = max(int(duration_ms), 1)
    # Bitget's plain 1D/12H/6H candles are anchored to UTC+8. The separate
    # "*utc" granularities use UTC anchors, but this project requests plain
    # granularities such as 1D.
    anchor = 0 if str(granularity).lower().endswith("utc") else -8 * 60 * 60 * 1000
    return int((timestamp_ms - anchor) // safe_duration * safe_duration + anchor)


def _bitget_get_json(path: str, params: dict[str, Any] | None = None, project_root: Path | None = None) -> Any:
    if project_root is not None:
        load_dotenv(project_root / ".env")
    base = os.getenv("BITGET_API_BASE", "").strip().rstrip("/") or BITGET_API_BASE
    query = urlencode({key: value for key, value in (params or {}).items() if value is not None})
    url = f"{base}{path}?{query}" if query else f"{base}{path}"
    last_error: Exception | None = None
    for attempt in range(1, PUBLIC_HTTP_RETRY_ATTEMPTS + 1):
        try:
            with urlopen(url, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
            break
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            last_error = RuntimeError(f"Bitget API {path} HTTP {exc.code}: {body or exc.reason}")
            if exc.code not in {429, 500, 502, 503, 504} or attempt >= PUBLIC_HTTP_RETRY_ATTEMPTS:
                raise last_error from exc
        except (TimeoutError, URLError, OSError) as exc:
            last_error = exc
            if attempt >= PUBLIC_HTTP_RETRY_ATTEMPTS:
                raise RuntimeError(f"Bitget API {path} 请求失败，已重试 {PUBLIC_HTTP_RETRY_ATTEMPTS} 次: {exc}") from exc
        delay = min(PUBLIC_HTTP_RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1)), PUBLIC_HTTP_RETRY_MAX_DELAY_SECONDS)
        time.sleep(delay)
    else:
        raise RuntimeError(f"Bitget API {path} 请求失败: {last_error}")
    code = str(payload.get("code", "00000"))
    if code != "00000":
        message = payload.get("msg") or payload.get("message") or "Bitget API 请求失败"
        raise RuntimeError(f"Bitget API {path} 返回错误 {code}: {message}")
    return payload


def _bitget_private_get_json(path: str, params: dict[str, Any], api_key: str, secret: str, passphrase: str) -> Any:
    base = os.getenv("BITGET_API_BASE", "").strip().rstrip("/") or BITGET_API_BASE
    query = urlencode({key: value for key, value in params.items() if value is not None})
    request_path = path if not query else f"{path}?{query}"
    url = f"{base}{request_path}"
    timestamp = str(int(time.time() * 1000))
    prehash = f"{timestamp}GET{request_path}"
    digest = hmac.new(secret.encode("utf-8"), prehash.encode("utf-8"), hashlib.sha256).digest()
    signature = base64.b64encode(digest).decode("utf-8")
    headers = {
        "ACCESS-KEY": api_key,
        "ACCESS-SIGN": signature,
        "ACCESS-TIMESTAMP": timestamp,
        "ACCESS-PASSPHRASE": passphrase,
        "locale": "zh-CN",
        "Content-Type": "application/json",
    }
    request = Request(url, headers=headers, method="GET")
    with urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def _configured_product_types(project_root: Path) -> list[str]:
    load_dotenv(project_root / ".env")
    raw = os.getenv("BITGET_PRODUCT_TYPES", "").strip()
    if not raw:
        return DEFAULT_PRODUCT_TYPES
    return [item.strip().upper() for item in raw.split(",") if item.strip()]


def _configured_kline_type() -> str:
    candidate = os.getenv("BITGET_KLINE_TYPE", DEFAULT_KLINE_TYPE).strip().upper() or DEFAULT_KLINE_TYPE
    if candidate not in VALID_KLINE_TYPES:
        raise RuntimeError(f"未知 Bitget K 线类型: {candidate}，可选值: {', '.join(sorted(VALID_KLINE_TYPES))}")
    return candidate


def load_bitget_contract_catalog(project_root: Path) -> list[dict[str, Any]]:
    contracts: list[dict[str, Any]] = []
    priority = {"BTCUSDT": 0, "ETHUSDT": 1, "BNBUSDT": 2, "SOLUSDT": 3}
    for product_type in _configured_product_types(project_root):
        payload = _bitget_get_json("/api/v2/mix/market/contracts", {"productType": product_type}, project_root=project_root)
        for item in payload.get("data", []) or []:
            symbol = str(item.get("symbol", "")).strip().upper()
            if not symbol:
                continue
            if str(item.get("symbolStatus", "")).lower() not in {"normal", ""}:
                continue
            base = str(item.get("baseCoin") or "").strip()
            quote = str(item.get("quoteCoin") or "").strip()
            label_name = f"{base}/{quote}" if base and quote else symbol
            contracts.append(
                {
                    "symbol": symbol,
                    "name": label_name,
                    "label": f"{label_name} · BITGET {product_type.upper()}",
                    "exchange_id": "BITGET",
                    "product_id": product_type.upper(),
                    "price_tick": float(item["priceEndStep"]) if item.get("priceEndStep") else None,
                    "volume_multiple": float(item["sizeMultiplier"]) if item.get("sizeMultiplier") else None,
                }
            )
    contracts.sort(key=lambda item: (priority.get(item["symbol"], 999), item["product_id"], item["symbol"]))
    return contracts


def load_bitget_account_summary(project_root: Path) -> dict[str, Any]:
    load_dotenv(project_root / ".env")
    api_key = os.getenv("BITGET_API_KEY", "").strip()
    secret = os.getenv("BITGET_API_SECRET", "").strip()
    passphrase = os.getenv("BITGET_API_PASSPHRASE", "").strip()
    product_type = os.getenv("BITGET_DEFAULT_PRODUCT_TYPE", "").strip().upper() or DEFAULT_PRODUCT_TYPES[0]
    if not api_key or not secret or not passphrase:
        return {}

    payload = _bitget_private_get_json(
        "/api/v2/mix/account/accounts",
        {"productType": product_type},
        api_key=api_key,
        secret=secret,
        passphrase=passphrase,
    )
    accounts = payload.get("data") or []
    if not accounts:
        return {}

    first = accounts[0]
    return {
        "product_type": product_type,
        "margin_coin": str(first.get("marginCoin", "") or ""),
        "account_equity": str(first.get("accountEquity", "") or ""),
        "usdt_equity": str(first.get("usdtEquity", "") or ""),
        "available": str(first.get("available", "") or ""),
        "locked": str(first.get("locked", "") or ""),
        "crossed_risk_rate": str(first.get("crossedRiskRate", "") or ""),
        "asset_mode": str(first.get("assetMode", "") or ""),
    }


def _ticker_price_for_kline_type(ticker: dict[str, Any], kline_type: str) -> float | None:
    field_by_type = {
        "MARKET": "lastPr",
        "MARK": "markPrice",
        "INDEX": "indexPrice",
    }
    field = field_by_type.get(kline_type)
    if not field:
        return None
    raw_value = ticker.get(field)
    if raw_value is None:
        return None
    try:
        return float(raw_value)
    except (TypeError, ValueError):
        return None


class BitgetDataSource(DataSource):
    provider_name = "bitget"

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
        self.product_type = os.getenv("BITGET_DEFAULT_PRODUCT_TYPE", "").strip().upper() or DEFAULT_PRODUCT_TYPES[0]
        self.kline_type = _configured_kline_type()
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
        self._thread = threading.Thread(target=self._run, name=f"bitget-{self.symbol}-{self.duration_seconds}", daemon=True)
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

        channel = _ws_channel_for_duration(self.duration_seconds)
        if channel is None:
            return

        subscribe_message = json.dumps(
            {
                "op": "subscribe",
                "args": [
                    {
                        "instType": self.product_type,
                        "channel": channel,
                        "instId": self.symbol,
                    }
                ],
            }
        )
        stream_url = os.getenv("BITGET_WS_PUBLIC_URL", "").strip() or BITGET_WS_PUBLIC_URL
        failure_count = 0
        while not self._stop_event.is_set():
            try:
                with self._lock:
                    self._stream_state = "connecting"
                    self._stream_url = stream_url
                async with ws_connect(stream_url, ping_interval=None, close_timeout=1) as websocket:
                    await websocket.send(subscribe_message)
                    with self._lock:
                        self._stream_state = "connected"
                        self._error = None
                    failure_count = 0
                    while not self._stop_event.is_set():
                        try:
                            message = await asyncio.wait_for(websocket.recv(), timeout=WS_RECV_TIMEOUT_SECONDS)
                        except asyncio.TimeoutError:
                            await websocket.send("ping")
                            continue
                        self._handle_ws_message(message)
            except Exception as exc:
                with self._lock:
                    self._error = None if self._bars is not None else str(exc)
                    self._stream_state = "reconnecting"
                failure_count += 1
                delay = min(WS_RECONNECT_MAX_DELAY_SECONDS, WS_RECONNECT_MIN_DELAY_SECONDS * (2 ** min(failure_count - 1, 5)))
                if await self._sleep_or_stop(delay):
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
                await self._sleep_or_stop(delay)
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

    async def _sleep_or_stop(self, delay: float) -> bool:
        return await asyncio.to_thread(self._stop_event.wait, delay)

    def _handle_ws_message(self, message: Any) -> None:
        if isinstance(message, bytes):
            message = message.decode("utf-8")
        if not message or message == "pong":
            return
        payload = json.loads(message)
        if payload.get("event") in {"subscribe", "unsubscribe"}:
            return
        if payload.get("event") == "error":
            raise RuntimeError(payload.get("msg") or payload.get("code") or "Bitget WebSocket 订阅失败。")
        rows = payload.get("data") or []
        if not rows:
            return
        updates = self._rows_to_frame(rows)
        if updates.empty:
            return
        backfill_frame: pd.DataFrame | None = None
        update_start = pd.Timestamp(updates.iloc[0]["datetime"])
        with self._lock:
            if self._bars is not None and not self._bars.empty:
                last_local_time = pd.Timestamp(self._bars.iloc[-1]["datetime"])
                if update_start - last_local_time > pd.Timedelta(seconds=self.duration_seconds):
                    backfill_frame = pd.DataFrame()
        if backfill_frame is not None:
            try:
                backfill_frame = self._fetch_history_bars()
            except Exception:
                backfill_frame = None
        with self._lock:
            if backfill_frame is not None and not backfill_frame.empty:
                base = backfill_frame
            else:
                base = self._bars.copy() if self._bars is not None else pd.DataFrame(columns=updates.columns)
            merged = pd.concat([base, updates], ignore_index=True)
            self._last_ticker_price = float(updates.iloc[-1]["close"])
            self._last_ticker_ts = int(pd.Timestamp(updates.iloc[-1]["datetime"]).timestamp() * 1000)
            self._commit_bars_locked(merged, from_kline=True)

    def _fetch_history_bars(self) -> pd.DataFrame:
        if self.bar_mode != "time":
            raise RuntimeError("Bitget 数据源当前只支持时间 K 线。")
        granularity = BITGET_GRANULARITY_MAP.get(self.duration_seconds)
        if granularity is None:
            raise RuntimeError(f"Bitget 暂不支持 {self.duration_seconds} 秒周期。")

        duration_ms = int(self.duration_seconds) * 1000
        requested_count = max(int(self.data_length), 1)
        end_time = _floor_bitget_time_ms(int(time.time() * 1000), duration_ms, granularity)
        start_time = end_time - requested_count * duration_ms
        rows: list[list[Any]] = []
        seen: set[int] = set()
        cursor = _floor_bitget_time_ms(start_time, duration_ms, granularity)
        while cursor < end_time:
            max_chunk_span = min(MAX_CANDLE_LIMIT * duration_ms, MAX_CANDLE_TIME_RANGE_MS)
            chunk_end = min(cursor + max_chunk_span, end_time)
            chunk_end = max(_floor_bitget_time_ms(chunk_end, duration_ms, granularity), cursor + duration_ms)
            limit = max(int((chunk_end - cursor) // duration_ms), 1)
            payload = _bitget_get_json(
                "/api/v2/mix/market/candles",
                {
                    "symbol": self.symbol,
                    "productType": self.product_type,
                    "granularity": granularity,
                    "kLineType": self.kline_type,
                    "startTime": str(cursor),
                    "endTime": str(chunk_end),
                    "limit": str(limit),
                },
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
            raise RuntimeError(f"Bitget 中暂无 {self.symbol} 的可用 K 线。")
        frame = self._rows_to_frame(rows)
        if frame.empty:
            raise RuntimeError(f"Bitget 中暂无 {self.symbol} 的可用 K 线。")
        frame = self._sync_current_bar_with_ticker(frame)
        gaps = self._find_time_gaps(frame)
        if gaps:
            previous_time, next_time = gaps[0]
            raise RuntimeError(
                f"Bitget K 线断档: {self.symbol} {previous_time.isoformat()} -> {next_time.isoformat()}"
            )
        return frame.tail(self.data_length).reset_index(drop=True)

    def _sync_current_bar_with_ticker(self, frame: pd.DataFrame) -> pd.DataFrame:
        if frame.empty:
            return frame
        ticker = self._fetch_ticker()
        ticker_price = _ticker_price_for_kline_type(ticker, self.kline_type)
        ticker_ts = int(float(ticker.get("ts") or 0))
        if ticker_price is None or ticker_ts <= 0:
            return frame

        duration_ms = int(self.duration_seconds) * 1000
        ticker_bucket_ms = (ticker_ts // duration_ms) * duration_ms
        last_index = frame.index[-1]
        last_open_ms = int(pd.Timestamp(frame.at[last_index, "datetime"]).timestamp() * 1000)
        if last_open_ms != ticker_bucket_ms:
            return frame

        synced = frame.copy()
        synced.at[last_index, "close"] = ticker_price
        synced.at[last_index, "high"] = max(float(synced.at[last_index, "high"]), ticker_price)
        synced.at[last_index, "low"] = min(float(synced.at[last_index, "low"]), ticker_price)
        with self._lock:
            self._last_ticker_price = ticker_price
            self._last_ticker_ts = ticker_ts
        return synced

    def _fetch_ticker(self) -> dict[str, Any]:
        payload = _bitget_get_json(
            "/api/v2/mix/market/ticker",
            {
                "symbol": self.symbol,
                "productType": self.product_type,
            },
        )
        data = payload.get("data") or []
        if not data:
            raise RuntimeError(f"Bitget 中暂无 {self.symbol} 的 ticker。")
        return dict(data[0])

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

    def _find_time_gaps(self, frame: pd.DataFrame) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
        if frame.empty or self.duration_seconds <= 0:
            return []
        datetimes = pd.to_datetime(frame["datetime"], utc=True, errors="coerce").dropna().sort_values()
        gaps: list[tuple[pd.Timestamp, pd.Timestamp]] = []
        expected_delta = pd.Timedelta(seconds=self.duration_seconds)
        previous: pd.Timestamp | None = None
        for current in datetimes:
            if previous is not None and current - previous != expected_delta:
                gaps.append((previous, current))
            previous = current
        return gaps

    @staticmethod
    def _rows_to_frame(rows: list[list[Any]]) -> pd.DataFrame:
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
