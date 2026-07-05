from __future__ import annotations

import os
import threading
import time
from typing import Any

import pandas as pd

from .base import DataSource


TIANQIN_PROVIDER_NAME = "tianqin"
TIANQIN_MAX_KLINE_LENGTH = 8000
TIANQIN_MAX_DURATION_SECONDS = 86400


def load_tianqin_contract_catalog(project_root: Any = None) -> list[dict[str, Any]]:
    del project_root
    symbols = _configured_symbols()
    return [
        {
            "symbol": symbol,
            "name": symbol,
            "label": f"{symbol} · TIANQIN",
            "exchange_id": symbol.split(".", 1)[0].upper() if "." in symbol else "TIANQIN",
            "product_id": "TQSDK",
        }
        for symbol in symbols
    ]


def load_tianqin_account_summary(project_root: Any = None) -> dict[str, Any]:
    del project_root
    username, password = _configured_auth()
    if not username or not password:
        return {"exchange": "TIANQIN", "configured": False}
    return {"exchange": "TIANQIN", "configured": True, "username": username}


def _configured_symbols() -> list[str]:
    raw = (
        os.getenv("TIANQIN_SYMBOLS", "").strip()
        or os.getenv("TQ_CHART_DEFAULT_SYMBOL", "").strip()
        or os.getenv("TIANQIN_DEFAULT_SYMBOL", "").strip()
    )
    symbols = [item.strip() for item in raw.split(",") if item.strip()]
    return symbols or ["SHFE.cu2607"]


def _configured_auth() -> tuple[str, str]:
    username = (
        os.getenv("TIANQIN_USERNAME", "").strip()
        or os.getenv("TQSDK_USERNAME", "").strip()
        or os.getenv("KQ_USERNAME", "").strip()
    )
    password = (
        os.getenv("TIANQIN_PASSWORD", "").strip()
        or os.getenv("TQSDK_PASSWORD", "").strip()
        or os.getenv("KQ_PASSWORD", "").strip()
    )
    return username, password


def _import_tqsdk() -> tuple[Any, Any]:
    try:
        from tqsdk import TqApi, TqAuth
    except ModuleNotFoundError as exc:
        raise RuntimeError("缺少天勤量化依赖：请先安装 tqsdk，或运行 pip install -r requirements.txt。") from exc
    return TqApi, TqAuth


def _create_api() -> Any:
    TqApi, TqAuth = _import_tqsdk()
    username, password = _configured_auth()
    if not username or not password:
        raise RuntimeError("缺少天勤量化账号配置：请在 .env 设置 TIANQIN_USERNAME / TIANQIN_PASSWORD。")
    return TqApi(auth=TqAuth(username, password))


def _wait_update(api: Any, timeout_seconds: float = 1.0) -> bool:
    try:
        return bool(api.wait_update(deadline=time.time() + timeout_seconds))
    except TypeError:
        return bool(api.wait_update())


def _serial_ready(api: Any, serial: pd.DataFrame) -> bool:
    try:
        return bool(api.is_serial_ready(serial))
    except Exception:
        return not serial_to_frame(serial).empty


def _serial_changed(api: Any, serial: pd.DataFrame) -> bool:
    try:
        return bool(api.is_changing(serial))
    except Exception:
        return True


def serial_to_frame(serial: pd.DataFrame) -> pd.DataFrame:
    if serial is None or serial.empty or "datetime" not in serial.columns:
        return pd.DataFrame(columns=["datetime", "open", "high", "low", "close", "volume"])
    frame = serial.copy()
    for column in ["datetime", "open", "high", "low", "close", "volume"]:
        if column not in frame.columns:
            frame[column] = pd.NA
    frame["timestamp_ns"] = pd.to_numeric(frame["datetime"], errors="coerce")
    frame = frame.dropna(subset=["timestamp_ns", "open", "high", "low", "close"])
    frame = frame[frame["timestamp_ns"] > 0]
    if frame.empty:
        return pd.DataFrame(columns=["datetime", "open", "high", "low", "close", "volume"])
    frame["datetime"] = pd.to_datetime(frame["timestamp_ns"].astype("int64"), unit="ns", utc=True, errors="coerce")
    for column in ["open", "high", "low", "close", "volume"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["datetime", "open", "high", "low", "close"])
    frame = frame.sort_values("datetime").drop_duplicates(subset=["datetime"], keep="last")
    return frame[["datetime", "open", "high", "low", "close", "volume"]].reset_index(drop=True)


class TianqinDataSource(DataSource):
    provider_name = TIANQIN_PROVIDER_NAME

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
        self.symbol = symbol.strip()
        self.duration_seconds = int(duration_seconds)
        self.data_length = min(max(int(data_length), 1), TIANQIN_MAX_KLINE_LENGTH)
        self.brick_length = brick_length
        self.refresh_ms = refresh_ms
        self.bar_mode = bar_mode
        self.range_ticks = range_ticks
        self.product_type = "TQSDK"
        self.kline_type = "MARKET"
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
        self._last_frame_signature: tuple[Any, ...] | None = None
        self._stream_state = "starting"

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            if self._bars is None or self._bars.empty:
                self._ready.clear()
            self._error = None
            self._stream_state = "starting"
        self._thread = threading.Thread(target=self._run, name=f"tianqin-{self.symbol}-{self.duration_seconds}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        with self._condition:
            self._condition.notify_all()
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
            raise RuntimeError("天勤量化数据源尚未就绪，请确认账号、合约代码和网络连接。")

    def _status_locked(self) -> dict[str, Any]:
        return {
            "version": self._version,
            "last_refresh_at": self._last_refresh_at or None,
            "last_update_at": self._last_update_at,
            "last_message_at": self._last_message_at,
            "last_kline_at": self._last_kline_at,
            "stream_state": self._stream_state,
            "stream_url": "tqsdk://wait_update",
            "product_type": self.product_type,
            "kline_type": self.kline_type,
            "last_ticker_price": self._last_ticker_price,
            "last_ticker_ts": self._last_ticker_ts,
            "error": self._error,
        }

    def _run(self) -> None:
        api: Any | None = None
        try:
            if self.bar_mode != "time":
                raise RuntimeError("天勤量化数据源当前只支持时间 K 线。")
            if self.duration_seconds <= 0 or self.duration_seconds > TIANQIN_MAX_DURATION_SECONDS:
                raise RuntimeError(f"天勤量化 K 线周期必须在 1 到 {TIANQIN_MAX_DURATION_SECONDS} 秒之间。")
            with self._lock:
                self._stream_state = "connecting"
                self._condition.notify_all()
            api = _create_api()
            serial = api.get_kline_serial(self.symbol, self.duration_seconds, data_length=self.data_length)
            while not self._stop_event.is_set():
                updated = _wait_update(api, timeout_seconds=1.0)
                if not updated and self._bars is not None:
                    continue
                if self._bars is not None and not _serial_changed(api, serial):
                    continue
                frame = serial_to_frame(serial.tail(self.data_length))
                if frame.empty:
                    continue
                if not _serial_ready(api, serial) and self._bars is None:
                    continue
                with self._lock:
                    signature = self._frame_signature(frame)
                    if signature == self._last_frame_signature:
                        continue
                    self._commit_bars_locked(frame)
        except Exception as exc:
            with self._lock:
                self._error = str(exc)
                self._stream_state = "stopped_error"
                self._condition.notify_all()
            self._ready.set()
        finally:
            if api is not None:
                try:
                    api.close()
                except Exception:
                    pass
            with self._lock:
                if self._stream_state != "stopped_error":
                    self._stream_state = "stopped" if self._stop_event.is_set() else self._stream_state
                self._condition.notify_all()

    def _commit_bars_locked(self, frame: pd.DataFrame) -> None:
        frame = frame.sort_values("datetime").drop_duplicates(subset=["datetime"], keep="last")
        self._bars = frame.tail(self.data_length).reset_index(drop=True)
        self._last_frame_signature = self._frame_signature(self._bars)
        self._error = None
        self._version += 1
        self._last_message_at = time.time()
        self._last_update_at = self._last_message_at
        self._last_kline_at = self._last_message_at
        self._last_refresh_at = time.monotonic()
        self._stream_state = "live"
        if self._bars is not None and not self._bars.empty:
            last = self._bars.iloc[-1]
            self._last_ticker_price = float(last["close"])
            self._last_ticker_ts = int(pd.Timestamp(last["datetime"]).timestamp() * 1000)
        self._condition.notify_all()
        self._ready.set()

    @staticmethod
    def _frame_signature(frame: pd.DataFrame) -> tuple[Any, ...] | None:
        if frame.empty:
            return None
        last = frame.iloc[-1]
        timestamp = pd.Timestamp(last["datetime"]).value
        return (
            len(frame),
            int(timestamp),
            float(last["open"]),
            float(last["high"]),
            float(last["low"]),
            float(last["close"]),
            float(last.get("volume", 0) or 0),
        )
