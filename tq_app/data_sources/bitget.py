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
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd
from dotenv import load_dotenv
from websockets.asyncio.client import connect as ws_connect

from .base import DataSource

BITGET_API_BASE = "https://api.bitget.com"
BITGET_WS_PUBLIC_URL = "wss://ws.bitget.com/v2/ws/public"
DEFAULT_PRODUCT_TYPES = ["USDT-FUTURES"]
GRANULARITY_MAP = {
    60: "1m",
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
MAX_CANDLE_LIMIT = 200
WS_RECV_TIMEOUT_SECONDS = 25
WS_RECONNECT_DELAY_SECONDS = 2


def _ws_channel_for_duration(duration_seconds: int) -> str | None:
    granularity = GRANULARITY_MAP.get(duration_seconds)
    if granularity is None:
        return None
    return f"candle{granularity}"


def _bitget_get_json(path: str, params: dict[str, Any]) -> Any:
    query = urlencode({key: value for key, value in params.items() if value is not None})
    url = f"{BITGET_API_BASE}{path}?{query}" if query else f"{BITGET_API_BASE}{path}"
    with urlopen(url, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload


def _bitget_private_get_json(path: str, params: dict[str, Any], api_key: str, secret: str, passphrase: str) -> Any:
    query = urlencode({key: value for key, value in params.items() if value is not None})
    request_path = path if not query else f"{path}?{query}"
    url = f"{BITGET_API_BASE}{request_path}"
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
        payload = json.loads(response.read().decode("utf-8"))
    return payload


def _configured_product_types(project_root: Path) -> list[str]:
    load_dotenv(project_root / ".env")
    raw = os.getenv("BITGET_PRODUCT_TYPES", "").strip()
    if not raw:
        return DEFAULT_PRODUCT_TYPES
    return [item.strip().upper() for item in raw.split(",") if item.strip()]


def load_bitget_contract_catalog(project_root: Path) -> list[dict[str, Any]]:
    contracts: list[dict[str, Any]] = []
    for product_type in _configured_product_types(project_root):
        payload = _bitget_get_json("/api/v2/mix/market/contracts", {"productType": product_type})
        for item in payload.get("data", []) or []:
            symbol = str(item.get("symbol", "")).strip()
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
                    "label": f"{label_name} · {product_type.upper()}",
                    "exchange_id": "BITGET",
                    "product_id": product_type.upper(),
                    "price_tick": float(item["priceEndStep"]) if item.get("priceEndStep") else None,
                    "volume_multiple": float(item["sizeMultiplier"]) if item.get("sizeMultiplier") else None,
                }
            )
    contracts.sort(key=lambda item: (item["product_id"], item["symbol"]))
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
        self.symbol = symbol
        self.duration_seconds = duration_seconds
        self.data_length = data_length
        self.brick_length = brick_length
        self.refresh_ms = refresh_ms
        self.bar_mode = bar_mode
        self.range_ticks = range_ticks
        self.product_type = (os.getenv("BITGET_DEFAULT_PRODUCT_TYPE", "").strip().upper() or DEFAULT_PRODUCT_TYPES[0])
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
        self._thread = threading.Thread(target=self._run, name="bitget-data-source", daemon=True)
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

        while not self._stop_event.is_set():
            try:
                async with ws_connect(BITGET_WS_PUBLIC_URL, ping_interval=None, close_timeout=1) as websocket:
                    await websocket.send(subscribe_message)
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
                if self._stop_event.wait(WS_RECONNECT_DELAY_SECONDS):
                    break

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
        with self._lock:
            base = self._bars.copy() if self._bars is not None else pd.DataFrame(columns=updates.columns)
            merged = pd.concat([base, updates], ignore_index=True)
            merged = merged.sort_values("datetime").drop_duplicates(subset=["datetime"], keep="last")
            self._bars = merged.tail(self.data_length).reset_index(drop=True)
            self._error = None
        self._ready.set()

    def _fetch_history_bars(self) -> pd.DataFrame:
        if self.bar_mode != "time":
            raise RuntimeError("Bitget 数据源当前只支持时间 K 线。")
        granularity = GRANULARITY_MAP.get(self.duration_seconds)
        if granularity is None:
            raise RuntimeError(f"Bitget 暂不支持 {self.duration_seconds} 秒周期。")

        remaining = max(int(self.data_length), 1)
        end_time = int(time.time() * 1000)
        rows: list[list[Any]] = []
        seen = set()
        while remaining > 0:
            limit = min(remaining, MAX_CANDLE_LIMIT)
            payload = _bitget_get_json(
                "/api/v2/mix/market/history-candles",
                {
                    "symbol": self.symbol,
                    "productType": self.product_type,
                    "granularity": granularity,
                    "endTime": str(end_time),
                    "limit": str(limit),
                },
            )
            batch = payload.get("data", []) if isinstance(payload, dict) else payload
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
                rows.append(item)
            if oldest_ts is None:
                break
            end_time = oldest_ts - 1
            remaining = self.data_length - len(rows)
            if len(batch) < limit:
                break

        if not rows:
            raise RuntimeError(f"Bitget 中暂无 {self.symbol} 的可用 K 线。")

        frame = self._rows_to_frame(rows)
        if frame.empty:
            raise RuntimeError(f"Bitget 中暂无 {self.symbol} 的可用 K 线。")
        return frame.tail(self.data_length).reset_index(drop=True)

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
