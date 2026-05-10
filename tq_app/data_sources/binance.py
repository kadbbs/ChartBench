from __future__ import annotations

import asyncio
import contextlib
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
from websockets.asyncio.client import connect as ws_connect

from .base import DataSource

BINANCE_FAPI_BASE = "https://fapi.binance.com"
BINANCE_WS_BASE = "wss://fstream.binance.com"
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
WS_RECV_TIMEOUT_SECONDS = 8
WS_RECONNECT_DELAY_SECONDS = 2


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


def _configured_ws_bases() -> list[str]:
    raw = os.getenv("BINANCE_WS_BASES", "").strip()
    if raw:
        bases = [item.strip().rstrip("/") for item in raw.split(",") if item.strip()]
        if bases:
            return bases
    single = os.getenv("BINANCE_WS_BASE", "").strip().rstrip("/")
    if single:
        return [single]
    return [BINANCE_WS_BASE]


def _market_stream_url(base: str, streams: str) -> str:
    normalized = base.strip().rstrip("/")
    if normalized.endswith(("/market", "/public", "/private")):
        return f"{normalized}/stream?streams={streams}"
    return f"{normalized}/market/stream?streams={streams}"


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
        self._condition = threading.Condition(self._lock)
        self._ready = threading.Event()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._bars: pd.DataFrame | None = None
        self._error: str | None = None
        self._version = 0
        self._last_refresh_at = 0.0
        self._last_message_at: float | None = None
        self._last_kline_at: float | None = None
        self._stream_state = "starting"
        self._stream_url: str | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name=f"binance-{self.symbol}-{self.duration_seconds}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
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
            self._last_refresh_at = time.monotonic()
            self._version += 1
            self._condition.notify_all()
        self._ready.set()
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
            self._version += 1
            status = self._status_locked()
            self._condition.notify_all()
        self._ready.set()
        return frame, status

    def _status_locked(self) -> dict[str, Any]:
        return {
            "version": self._version,
            "last_refresh_at": self._last_refresh_at or None,
            "last_message_at": self._last_message_at,
            "last_kline_at": self._last_kline_at,
            "stream_state": self._stream_state,
            "stream_url": self._stream_url,
            "error": self._error,
        }

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._stream_loop())
        except Exception as exc:
            with self._lock:
                self._error = str(exc)
            self._ready.set()
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            with contextlib.suppress(Exception):
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()

    async def _stream_loop(self) -> None:
        frame = self._fetch_history_bars()
        with self._lock:
            self._bars = frame.copy()
            self._error = None
            self._last_refresh_at = time.monotonic()
            self._version += 1
            self._condition.notify_all()
        self._ready.set()

        interval = BINANCE_GRANULARITY_MAP.get(self.duration_seconds)
        if interval is None:
            return

        bases = _configured_ws_bases()
        stream_index = 0
        symbol = self.symbol.lower()
        streams = f"{symbol}@aggTrade/{symbol}@kline_{interval}"
        while not self._stop_event.is_set():
            base = bases[stream_index % len(bases)]
            stream_url = _market_stream_url(base, streams)
            try:
                with self._lock:
                    self._stream_state = "connecting"
                    self._stream_url = stream_url
                async with ws_connect(stream_url, ping_interval=None, close_timeout=1, compression=None) as websocket:
                    with self._lock:
                        self._stream_state = "connected"
                        self._error = None
                    while not self._stop_event.is_set():
                        try:
                            message = await asyncio.wait_for(websocket.recv(), timeout=WS_RECV_TIMEOUT_SECONDS)
                        except asyncio.TimeoutError:
                            raise RuntimeError(f"Binance WS 无行情消息: {stream_url}")
                        self._handle_ws_message(message)
            except Exception as exc:
                with self._lock:
                    self._error = None if self._bars is not None else str(exc)
                    self._stream_state = "reconnecting"
                stream_index += 1
                if self._stop_event.wait(WS_RECONNECT_DELAY_SECONDS):
                    break

    def _fetch_history_bars(self) -> pd.DataFrame:
        if self.bar_mode != "time":
            raise RuntimeError("Binance 数据源当前只支持时间 K 线。")
        interval = BINANCE_GRANULARITY_MAP.get(self.duration_seconds)
        if interval is None:
            raise RuntimeError(f"Binance 暂不支持 {self.duration_seconds} 秒周期。")

        remaining = max(int(self.data_length), 1) + 1
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
            remaining = (self.data_length + 1) - len(rows)
            if len(batch) < limit:
                break

        if not rows:
            raise RuntimeError(f"Binance 中暂无 {self.symbol} 的可用 K 线。")
        frame = self._rows_to_frame(rows)
        if frame.empty:
            raise RuntimeError(f"Binance 中暂无 {self.symbol} 的可用 K 线。")
        frame = self._drop_open_bar(frame)
        if frame.empty:
            raise RuntimeError(f"Binance 中暂无 {self.symbol} 的已收盘 K 线。")
        return frame.tail(self.data_length).reset_index(drop=True)

    def _drop_open_bar(self, frame: pd.DataFrame) -> pd.DataFrame:
        if frame.empty:
            return frame
        duration_ms = int(self.duration_seconds) * 1000
        now_ms = int(time.time() * 1000)
        last_open_ms = int(pd.Timestamp(frame.iloc[-1]["datetime"]).timestamp() * 1000)
        if last_open_ms + duration_ms > now_ms:
            return frame.iloc[:-1].reset_index(drop=True)
        return frame

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

    def _handle_ws_message(self, message: Any) -> None:
        if isinstance(message, bytes):
            message = message.decode("utf-8")
        if not message:
            return
        payload = json.loads(message)
        stream = str(payload.get("stream", "")).lower() if isinstance(payload, dict) else ""
        data = payload.get("data", payload) if isinstance(payload, dict) else {}
        if not isinstance(data, dict):
            return
        if stream.endswith("@aggtrade") or data.get("e") == "aggTrade":
            self._apply_agg_trade(data)
            return
        if "@kline_" in stream or data.get("e") == "kline":
            kline = data.get("k", data)
            if isinstance(kline, dict):
                self._apply_kline(kline)

    def _apply_agg_trade(self, trade: dict[str, Any]) -> None:
        timestamp_ms = int(float(trade.get("T") or trade.get("E") or 0))
        price = float(trade.get("p"))
        if timestamp_ms <= 0:
            return
        bucket_start_ms = (timestamp_ms // (self.duration_seconds * 1000)) * self.duration_seconds * 1000
        bar_time = pd.to_datetime(bucket_start_ms, unit="ms", utc=True)

        with self._lock:
            base = self._bars.copy() if self._bars is not None else pd.DataFrame(columns=["datetime", "open", "high", "low", "close", "volume"])
            existing = base.index[base["datetime"] == bar_time].tolist() if not base.empty else []
            if not existing:
                # Let the official kline stream create each candle so open/volume match Binance.
                return
            index = existing[-1]
            base.at[index, "high"] = max(float(base.at[index, "high"]), price)
            base.at[index, "low"] = min(float(base.at[index, "low"]), price)
            base.at[index, "close"] = price
            self._commit_bars_locked(base)

    def _apply_kline(self, kline: dict[str, Any]) -> None:
        row = [
            kline.get("t"),
            kline.get("o"),
            kline.get("h"),
            kline.get("l"),
            kline.get("c"),
            kline.get("v"),
        ]
        updates = self._rows_to_frame([row])
        if updates.empty:
            return
        if self._last_kline_at is None:
            self._ensure_kline_matches_rest(updates.iloc[-1])
        with self._lock:
            base = self._bars.copy() if self._bars is not None else pd.DataFrame(columns=updates.columns)
            merged = pd.concat([base, updates], ignore_index=True)
            self._commit_bars_locked(merged, from_kline=True)

    def _ensure_kline_matches_rest(self, update: pd.Series) -> None:
        latest = self._fetch_latest_bars()
        if latest.empty:
            return
        update_time = pd.Timestamp(update["datetime"])
        matched = latest[latest["datetime"] == update_time]
        if matched.empty:
            return
        rest_open = float(matched.iloc[-1]["open"])
        ws_open = float(update["open"])
        if abs(rest_open - ws_open) > 1e-9:
            raise RuntimeError(
                f"Binance WS 与 REST K 线不一致: {self.symbol} {update_time.isoformat()} "
                f"ws_open={ws_open} rest_open={rest_open}"
            )

    def _commit_bars_locked(self, frame: pd.DataFrame, from_kline: bool = False) -> None:
        frame = frame.sort_values("datetime").drop_duplicates(subset=["datetime"], keep="last")
        self._bars = frame.tail(self.data_length).reset_index(drop=True)
        self._error = None
        self._version += 1
        self._last_message_at = time.time()
        if from_kline:
            self._last_kline_at = self._last_message_at
        self._stream_state = "live"
        self._condition.notify_all()
        self._ready.set()

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
