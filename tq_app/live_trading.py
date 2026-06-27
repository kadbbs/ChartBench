from __future__ import annotations

import base64
import hashlib
import hmac
import asyncio
import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import websockets
from dotenv import load_dotenv

from tq_app.notifications import send_resend_email


BITGET_API_BASE = "https://api.bitget.com"
DEFAULT_LOG_PATH = Path("logs/live_trading.log")
DEFAULT_ORDER_LOG_PATH = Path("logs/live_trading_orders.jsonl")
DEFAULT_STATE_PATH = Path("logs/live_trading_state.json")
DISPLAY_TIMEZONE = ZoneInfo("Asia/Shanghai")
SIGNAL_TEXT_BY_SIDE = {
    "buy": {"Buy", "买"},
    "sell": {"Sell", "卖"},
}


@dataclass(slots=True)
class LiveTradingConfig:
    mode: str = "email"
    enabled: bool = False
    dry_run: bool = True
    log_only: bool = True
    product_type: str = "USDT-FUTURES"
    margin_coin: str = "USDT"
    margin_mode: str = "isolated"
    position_mode: str = "hedge_mode"
    margin_amount: str = "5"
    auto_transfer_enabled: bool = True
    auto_transfer_multiplier: str = "1.1"
    auto_transfer_buffer: str = "0"
    entry_time_filter_enabled: bool = False
    entry_time_start: str = "20:00"
    entry_time_end: str = "24:00"
    htf_hull_filter_enabled: bool = True
    htf_hull_duration_seconds: int = 3600
    local_position_enabled: bool = True
    local_position_record_observation: bool = True
    position_sync_enabled: bool = True
    position_sync_real_only: bool = True
    size: str = ""
    leverage: str = "10"
    signal_mode: str = "any"
    strategy: str = "stc_extreme_contrarian"
    use_closed_bar: bool = True
    entry_price_source: str = "mark_price"
    atr_period: int = 14
    stop_atr_multiplier: str = "2"
    tp1_r_multiple: str = "1"
    tp1_size_ratio: str = "0.5"
    tp2_r_multiple: str = "1.5"
    price_decimals: int = 2
    size_decimals: int = 6
    position_sync_interval_seconds: float = 30.0
    risk_exits_enabled: bool = False
    risk_check_interval_seconds: float = 5.0
    risk_websocket_ticker_enabled: bool = True
    risk_websocket_ticker_stale_seconds: float = 5.0
    risk_error_email_cooldown_seconds: float = 300.0
    exchange_disaster_sl_enabled: bool = True
    risk_close_managed_size_only: bool = True
    risk_price_source: str = "mark_price"
    risk_startup_check_bars_5m: int = 24
    risk_startup_max_favorable_points: str = "300"
    risk_startup_current_points: str = "-120"
    risk_disaster_stop_points: str = "-1800"
    risk_breakeven_trigger_points: str = "800"
    risk_breakeven_stop_points: str = "100"
    risk_trailing_trigger_1_points: str = "2000"
    risk_trailing_protect_1_ratio: str = "0.4"
    risk_trailing_trigger_2_points: str = "4000"
    risk_trailing_protect_2_ratio: str = "0.5"
    risk_trailing_trigger_3_points: str = "8000"
    risk_trailing_protect_3_ratio: str = "0.6"
    email_enabled: bool = True
    email_to: str = ""
    log_path: Path = DEFAULT_LOG_PATH
    order_log_path: Path = DEFAULT_ORDER_LOG_PATH
    state_path: Path = DEFAULT_STATE_PATH

    @classmethod
    def from_env(cls, project_root: Path) -> "LiveTradingConfig":
        load_dotenv(project_root / ".env")
        mode = _env_live_trading_mode()
        if mode:
            enabled, dry_run, log_only = _mode_flags(mode)
        else:
            enabled = _env_bool("LIVE_TRADING_ENABLED", False)
            dry_run = _env_bool("LIVE_TRADING_DRY_RUN", True)
            log_only = _env_bool("LIVE_TRADING_LOG_ONLY", True)
            mode = _mode_from_flags(enabled=enabled, dry_run=dry_run, log_only=log_only)
        email_enabled = _env_bool("LIVE_TRADING_EMAIL_ENABLED", True)
        if mode == "off":
            email_enabled = False
        return cls(
            mode=mode,
            enabled=enabled,
            dry_run=dry_run,
            log_only=log_only,
            product_type=os.getenv("LIVE_TRADING_PRODUCT_TYPE", os.getenv("BITGET_DEFAULT_PRODUCT_TYPE", "USDT-FUTURES")).strip().upper(),
            margin_coin=os.getenv("LIVE_TRADING_MARGIN_COIN", "USDT").strip().upper(),
            margin_mode="isolated",
            position_mode=os.getenv("LIVE_TRADING_POSITION_MODE", "hedge_mode").strip().lower(),
            margin_amount=os.getenv("LIVE_TRADING_MARGIN_AMOUNT", "5").strip() or "5",
            auto_transfer_enabled=_env_bool("LIVE_TRADING_AUTO_TRANSFER_FROM_SPOT", True),
            auto_transfer_multiplier=os.getenv("LIVE_TRADING_AUTO_TRANSFER_MULTIPLIER", "1.1").strip() or "1.1",
            auto_transfer_buffer=os.getenv("LIVE_TRADING_AUTO_TRANSFER_BUFFER", "0").strip() or "0",
            entry_time_filter_enabled=_env_bool("LIVE_TRADING_ENTRY_TIME_FILTER_ENABLED", False),
            entry_time_start=os.getenv("LIVE_TRADING_ENTRY_TIME_START", "20:00").strip(),
            entry_time_end=os.getenv("LIVE_TRADING_ENTRY_TIME_END", "24:00").strip(),
            htf_hull_filter_enabled=_env_bool("LIVE_TRADING_HTF_HULL_FILTER_ENABLED", True),
            htf_hull_duration_seconds=_env_int("LIVE_TRADING_HTF_HULL_DURATION_SECONDS", 3600),
            local_position_enabled=_env_bool("LIVE_TRADING_LOCAL_POSITION_ENABLED", True),
            local_position_record_observation=_env_bool("LIVE_TRADING_LOCAL_POSITION_RECORD_OBSERVATION", True),
            position_sync_enabled=_env_bool("LIVE_TRADING_POSITION_SYNC_ENABLED", True),
            position_sync_real_only=_env_bool("LIVE_TRADING_POSITION_SYNC_REAL_ONLY", True),
            size=os.getenv("LIVE_TRADING_ORDER_SIZE", "").strip(),
            leverage=os.getenv("LIVE_TRADING_LEVERAGE", "10").strip() or "10",
            signal_mode=os.getenv("LIVE_TRADING_SIGNAL_MODE", "any").strip().lower(),
            strategy=os.getenv("LIVE_TRADING_STRATEGY", "stc_extreme_contrarian").strip().lower(),
            use_closed_bar=_env_bool("LIVE_TRADING_USE_CLOSED_BAR", True),
            entry_price_source=os.getenv("LIVE_TRADING_ENTRY_PRICE_SOURCE", "mark_price").strip().lower(),
            atr_period=_env_int("LIVE_TRADING_ATR_PERIOD", 14),
            stop_atr_multiplier=os.getenv("LIVE_TRADING_STOP_ATR_MULTIPLIER", "2").strip(),
            tp1_r_multiple=os.getenv("LIVE_TRADING_TP1_R_MULTIPLE", "1").strip(),
            tp1_size_ratio=os.getenv("LIVE_TRADING_TP1_SIZE_RATIO", "0.5").strip(),
            tp2_r_multiple=os.getenv("LIVE_TRADING_TP2_R_MULTIPLE", "1.5").strip(),
            price_decimals=_env_int("LIVE_TRADING_PRICE_DECIMALS", 2),
            size_decimals=_env_int("LIVE_TRADING_SIZE_DECIMALS", 6),
            position_sync_interval_seconds=_env_float("LIVE_TRADING_POSITION_SYNC_INTERVAL_SECONDS", 30.0),
            risk_exits_enabled=_env_bool("LIVE_TRADING_RISK_EXITS_ENABLED", False),
            risk_check_interval_seconds=_env_float("LIVE_TRADING_RISK_CHECK_INTERVAL_SECONDS", 5.0),
            risk_websocket_ticker_enabled=_env_bool("LIVE_TRADING_RISK_WEBSOCKET_TICKER_ENABLED", True),
            risk_websocket_ticker_stale_seconds=_env_float("LIVE_TRADING_RISK_WEBSOCKET_TICKER_STALE_SECONDS", 5.0),
            risk_error_email_cooldown_seconds=_env_float("LIVE_TRADING_RISK_ERROR_EMAIL_COOLDOWN_SECONDS", 300.0),
            exchange_disaster_sl_enabled=_env_bool("LIVE_TRADING_EXCHANGE_DISASTER_SL_ENABLED", True),
            risk_close_managed_size_only=_env_bool("LIVE_TRADING_RISK_CLOSE_MANAGED_SIZE_ONLY", True),
            risk_price_source=os.getenv("LIVE_TRADING_RISK_PRICE_SOURCE", "mark_price").strip().lower(),
            risk_startup_check_bars_5m=_env_int("LIVE_TRADING_RISK_STARTUP_CHECK_BARS_5M", 24),
            risk_startup_max_favorable_points=os.getenv("LIVE_TRADING_RISK_STARTUP_MAX_FAVORABLE_POINTS", "300").strip() or "300",
            risk_startup_current_points=os.getenv("LIVE_TRADING_RISK_STARTUP_CURRENT_POINTS", "-120").strip() or "-120",
            risk_disaster_stop_points=os.getenv("LIVE_TRADING_RISK_DISASTER_STOP_POINTS", "-1800").strip() or "-1800",
            risk_breakeven_trigger_points=os.getenv("LIVE_TRADING_RISK_BREAKEVEN_TRIGGER_POINTS", "800").strip() or "800",
            risk_breakeven_stop_points=os.getenv("LIVE_TRADING_RISK_BREAKEVEN_STOP_POINTS", "100").strip() or "100",
            risk_trailing_trigger_1_points=os.getenv("LIVE_TRADING_RISK_TRAILING_TRIGGER_1_POINTS", "2000").strip() or "2000",
            risk_trailing_protect_1_ratio=os.getenv("LIVE_TRADING_RISK_TRAILING_PROTECT_1_RATIO", "0.4").strip() or "0.4",
            risk_trailing_trigger_2_points=os.getenv("LIVE_TRADING_RISK_TRAILING_TRIGGER_2_POINTS", "4000").strip() or "4000",
            risk_trailing_protect_2_ratio=os.getenv("LIVE_TRADING_RISK_TRAILING_PROTECT_2_RATIO", "0.5").strip() or "0.5",
            risk_trailing_trigger_3_points=os.getenv("LIVE_TRADING_RISK_TRAILING_TRIGGER_3_POINTS", "8000").strip() or "8000",
            risk_trailing_protect_3_ratio=os.getenv("LIVE_TRADING_RISK_TRAILING_PROTECT_3_RATIO", "0.6").strip() or "0.6",
            email_enabled=email_enabled,
            email_to=os.getenv("LIVE_TRADING_EMAIL_TO", "").strip(),
            log_path=project_root / os.getenv("LIVE_TRADING_LOG_PATH", str(DEFAULT_LOG_PATH)).strip(),
            order_log_path=project_root / os.getenv("LIVE_TRADING_ORDER_LOG_PATH", str(DEFAULT_ORDER_LOG_PATH)).strip(),
            state_path=project_root / os.getenv("LIVE_TRADING_STATE_PATH", str(DEFAULT_STATE_PATH)).strip(),
        )

    def status_label(self) -> str:
        labels = {
            "off": "关闭模式",
            "email": "邮件观察模式",
            "dry_run": "DRY-RUN",
            "live": "真实交易",
        }
        return labels.get(self.mode, "观察模式" if self.log_only else ("DRY-RUN" if self.dry_run or not self.enabled else "真实交易"))

    def runtime_check_interval_seconds(self) -> float:
        intervals = [max(self.position_sync_interval_seconds, 1.0)]
        if self.risk_exits_enabled:
            intervals.append(max(self.risk_check_interval_seconds, 1.0))
        return min(intervals)


@dataclass(slots=True)
class TradeDecision:
    action: str
    symbol: str
    side: str | None
    bar_time: int | None
    marker_texts: list[str] = field(default_factory=list)
    indicator_values: dict[str, float] = field(default_factory=dict)
    indicator_colors: dict[str, str] = field(default_factory=dict)
    reason: str = ""
    last_close: float | None = None
    bar_open: float | None = None
    bar_high: float | None = None
    bar_low: float | None = None
    bar_close: float | None = None
    bar_time_label: str = ""
    atr_value: float | None = None
    client_oid: str | None = None
    htf_lock_key: str | None = None
    htf_context: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TradeExecutionResult:
    decision: TradeDecision
    dry_run: bool
    enabled: bool
    request: dict[str, Any] | None = None
    response: dict[str, Any] | None = None
    error: str | None = None
    already_executed: bool = False


@dataclass(slots=True)
class PreflightResult:
    ok: bool
    checks: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


class BitgetFuturesTradeClient:
    def __init__(self, project_root: Path, api_base: str | None = None) -> None:
        load_dotenv(project_root / ".env")
        self.api_base = (api_base or os.getenv("BITGET_API_BASE", "").strip() or BITGET_API_BASE).rstrip("/")
        self.api_key = os.getenv("BITGET_API_KEY", "").strip()
        self.secret = os.getenv("BITGET_API_SECRET", "").strip()
        self.passphrase = os.getenv("BITGET_API_PASSPHRASE", "").strip()
        if not self.api_key or not self.secret or not self.passphrase:
            raise RuntimeError("缺少 Bitget API 配置：BITGET_API_KEY / BITGET_API_SECRET / BITGET_API_PASSPHRASE。")

    def get_all_positions(self, *, product_type: str, margin_coin: str = "USDT") -> dict[str, Any]:
        return self._request(
            "GET",
            "/api/v2/mix/position/all-position",
            params={"productType": product_type, "marginCoin": margin_coin},
        )

    def get_futures_accounts(self, *, product_type: str) -> dict[str, Any]:
        return self._request(
            "GET",
            "/api/v2/mix/account/accounts",
            params={"productType": product_type},
        )

    def get_spot_assets(self, *, coin: str) -> dict[str, Any]:
        return self._request(
            "GET",
            "/api/v2/spot/account/assets",
            params={"coin": coin.upper()},
        )

    def transfer_between_accounts(
        self,
        *,
        from_type: str,
        to_type: str,
        amount: str,
        coin: str,
        client_oid: str | None = None,
    ) -> dict[str, Any]:
        body = {
            "fromType": from_type,
            "toType": to_type,
            "amount": amount,
            "coin": coin.upper(),
        }
        if client_oid:
            body["clientOid"] = client_oid
        return self._request(
            "POST",
            "/api/v2/spot/wallet/transfer",
            body=body,
        )

    def place_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/v2/mix/order/place-order", body=payload)

    def close_position_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/v2/mix/order/close-positions", body=payload)

    def place_position_tpsl_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/v2/mix/order/place-pos-tpsl", body=payload)

    def place_tpsl_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/v2/mix/order/place-tpsl-order", body=payload)

    def modify_tpsl_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/v2/mix/order/modify-tpsl-order", body=payload)

    def cancel_plan_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/v2/mix/order/cancel-plan-order", body=payload)

    def get_ticker(self, *, symbol: str, product_type: str) -> dict[str, Any]:
        query = urlencode({"symbol": symbol, "productType": product_type})
        with urlopen(f"{self.api_base}/api/v2/mix/market/ticker?{query}", timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
        code = str(payload.get("code", ""))
        if code and code != "00000":
            raise RuntimeError(f"Bitget ticker 返回错误 {code}: {payload.get('msg') or payload}")
        data = payload.get("data") or []
        if not data:
            raise RuntimeError(f"Bitget 中暂无 {symbol} 的 ticker。")
        return dict(data[0])

    def get_contracts(self, *, product_type: str) -> list[dict[str, Any]]:
        query = urlencode({"productType": product_type})
        with urlopen(f"{self.api_base}/api/v2/mix/market/contracts?{query}", timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
        code = str(payload.get("code", ""))
        if code and code != "00000":
            raise RuntimeError(f"Bitget contracts 返回错误 {code}: {payload.get('msg') or payload}")
        data = payload.get("data") or []
        return [dict(item) for item in data if isinstance(item, dict)]

    def get_order_detail(self, *, symbol: str, product_type: str, client_oid: str | None = None, order_id: str | None = None) -> dict[str, Any]:
        return self._request(
            "GET",
            "/api/v2/mix/order/detail",
            params={"symbol": symbol, "productType": product_type, "clientOid": client_oid, "orderId": order_id},
        )

    def set_leverage(
        self,
        *,
        symbol: str,
        product_type: str,
        margin_coin: str,
        leverage: str,
        hold_side: str | None = None,
    ) -> dict[str, Any]:
        body = {
            "symbol": symbol,
            "productType": product_type,
            "marginCoin": margin_coin,
            "leverage": leverage,
        }
        if hold_side:
            body["holdSide"] = hold_side
        return self._request("POST", "/api/v2/mix/account/set-leverage", body=body)

    def set_margin_mode(
        self,
        *,
        symbol: str,
        product_type: str,
        margin_coin: str,
        margin_mode: str,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/v2/mix/account/set-margin-mode",
            body={
                "symbol": symbol,
                "productType": product_type,
                "marginCoin": margin_coin,
                "marginMode": margin_mode,
            },
        )

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        method = method.upper()
        query = urlencode({key: value for key, value in (params or {}).items() if value not in (None, "")})
        request_path = path if not query else f"{path}?{query}"
        body_text = json.dumps(body or {}, separators=(",", ":"), ensure_ascii=False) if method != "GET" else ""
        timestamp = str(int(time.time() * 1000))
        prehash = f"{timestamp}{method}{request_path}{body_text}"
        digest = hmac.new(self.secret.encode("utf-8"), prehash.encode("utf-8"), hashlib.sha256).digest()
        headers = {
            "ACCESS-KEY": self.api_key,
            "ACCESS-SIGN": base64.b64encode(digest).decode("utf-8"),
            "ACCESS-TIMESTAMP": timestamp,
            "ACCESS-PASSPHRASE": self.passphrase,
            "locale": "zh-CN",
            "Content-Type": "application/json",
        }
        request = Request(
            f"{self.api_base}{request_path}",
            data=body_text.encode("utf-8") if body_text else None,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Bitget API {path} HTTP {exc.code}: {body or exc.reason}") from exc
        code = str(payload.get("code", ""))
        if code and code != "00000":
            raise RuntimeError(f"Bitget API {path} 返回错误 {code}: {payload.get('msg') or payload}")
        return payload


class BitgetTickerWebSocket:
    WS_URL = "wss://ws.bitget.com/v2/ws/public"

    def __init__(
        self,
        *,
        symbol: str,
        product_type: str,
        logger: logging.Logger,
    ) -> None:
        self.symbol = symbol.upper()
        self.product_type = product_type.upper()
        self.logger = logger
        self._stop_event = threading.Event()
        self._condition = threading.Condition()
        self._thread: threading.Thread | None = None
        self._latest: dict[str, Any] | None = None
        self._version = 0
        self._status = "stopped"
        self._last_error = ""

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name=f"bitget-ticker-{self.symbol}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        with self._condition:
            self._condition.notify_all()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

    def latest(self) -> dict[str, Any] | None:
        with self._condition:
            return dict(self._latest) if self._latest else None

    def version(self) -> int:
        with self._condition:
            return self._version

    def wait_for_update(self, last_version: int | None, timeout: float) -> int:
        with self._condition:
            if last_version is None or self._version != last_version:
                return self._version
            self._condition.wait_for(lambda: self._version != last_version or self._stop_event.is_set(), timeout=timeout)
            return self._version

    def status(self) -> dict[str, Any]:
        with self._condition:
            return {
                "status": self._status,
                "version": self._version,
                "last_error": self._last_error,
                "latest_ts": self._latest.get("ts") if self._latest else None,
                "latest_received_at_ms": self._latest.get("_received_at_ms") if self._latest else None,
            }

    def _run(self) -> None:
        try:
            asyncio.run(self._run_forever())
        except Exception as exc:
            with self._condition:
                self._status = "stopped_error"
                self._last_error = str(exc)
                self._condition.notify_all()

    async def _run_forever(self) -> None:
        backoff = 1.0
        while not self._stop_event.is_set():
            try:
                with self._condition:
                    self._status = "connecting"
                    self._condition.notify_all()
                async with websockets.connect(self.WS_URL, ping_interval=None, close_timeout=5) as websocket:
                    await websocket.send(json.dumps({
                        "op": "subscribe",
                        "args": [
                            {
                                "instType": self.product_type,
                                "channel": "ticker",
                                "instId": self.symbol,
                            }
                        ],
                    }, separators=(",", ":")))
                    with self._condition:
                        self._status = "connected"
                        self._last_error = ""
                        self._condition.notify_all()
                    backoff = 1.0
                    ping_task = asyncio.create_task(self._ping_loop(websocket))
                    try:
                        while not self._stop_event.is_set():
                            message = await asyncio.wait_for(websocket.recv(), timeout=45)
                            self._handle_message(message)
                    finally:
                        ping_task.cancel()
            except Exception as exc:
                with self._condition:
                    self._status = "reconnecting"
                    self._last_error = str(exc)
                    self._condition.notify_all()
                self.logger.warning("Bitget ticker WebSocket 断开，准备重连: %s", exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _ping_loop(self, websocket: Any) -> None:
        while not self._stop_event.is_set():
            await asyncio.sleep(30)
            await websocket.send("ping")

    def _handle_message(self, message: Any) -> None:
        if message == "pong":
            return
        try:
            payload = json.loads(message)
        except (TypeError, json.JSONDecodeError):
            return
        if payload.get("event") == "error":
            raise RuntimeError(f"Bitget ticker WebSocket error: {payload}")
        arg = payload.get("arg") if isinstance(payload, dict) else {}
        if not isinstance(arg, dict) or arg.get("channel") != "ticker":
            return
        data = payload.get("data")
        if not isinstance(data, list) or not data:
            return
        item = next((entry for entry in data if isinstance(entry, dict)), None)
        if not item:
            return
        if str(item.get("instId") or item.get("symbol") or "").upper() != self.symbol:
            return
        ticker = dict(item)
        ticker["_received_at_ms"] = int(time.time() * 1000)
        ticker["_source"] = "bitget_ws_ticker"
        with self._condition:
            self._latest = ticker
            self._version += 1
            self._status = "streaming"
            self._condition.notify_all()


class LiveTradingEngine:
    def __init__(self, project_root: Path, config: LiveTradingConfig | None = None) -> None:
        self.project_root = project_root
        self.config = config or LiveTradingConfig.from_env(project_root)
        self.logger = _build_logger(self.config.log_path)
        self._last_position_sync_warning_at = 0.0

    def send_startup_email(self, *, symbol: str, duration_seconds: int, continuous: bool) -> None:
        if not self.config.email_enabled or not self.config.email_to:
            return
        mode = "常驻实盘" if continuous else "单次实盘"
        status = self.config.status_label()
        subject = f"[TQ Live] {mode}已启动 {symbol.upper()} {duration_seconds}s {status}"
        html = (
            "<h3>TQ Live Trading Started</h3>"
            f"<p><b>Mode:</b> {mode}</p>"
            f"<p><b>Status:</b> {status}</p>"
            f"<p><b>Trading Mode:</b> {self.config.mode}</p>"
            f"<p><b>Symbol:</b> {symbol.upper()}</p>"
            f"<p><b>Duration:</b> {duration_seconds}s</p>"
            f"<p><b>Enabled:</b> {self.config.enabled}</p>"
            f"<p><b>Dry Run:</b> {self.config.dry_run}</p>"
            f"<p><b>Log Only:</b> {self.config.log_only}</p>"
            f"<p><b>Strategy:</b> {self.config.strategy}</p>"
            f"<p><b>Use Closed Bar:</b> {self.config.use_closed_bar}</p>"
            f"<p><b>Risk Exits:</b> {self.config.risk_exits_enabled}</p>"
            f"<p><b>Exchange Disaster SL:</b> {self.config.exchange_disaster_sl_enabled}</p>"
            f"<p><b>Started At:</b> {datetime.now(DISPLAY_TIMEZONE).strftime('%Y-%m-%d %H:%M:%S')}</p>"
        )
        try:
            send_resend_email(to=self.config.email_to, subject=subject, html=html, project_root=self.project_root)
        except Exception as exc:
            self.logger.warning("启动邮件发送失败: %s", exc)

    def run_preflight(self, *, symbol: str, configure_account: bool = False) -> PreflightResult:
        checks: list[dict[str, Any]] = []

        def add_check(name: str, ok: bool, detail: Any = None) -> None:
            checks.append({"name": name, "ok": ok, "detail": detail})

        try:
            margin_amount = _to_decimal(self.config.margin_amount)
            leverage = _to_decimal(self.config.leverage)
            add_check("margin_mode", self.config.margin_mode == "isolated", self.config.margin_mode)
            if self.config.margin_mode != "isolated":
                return PreflightResult(ok=False, checks=checks, error="实盘只允许逐仓 isolated。")
            add_check("margin_amount", margin_amount > 0, self.config.margin_amount)
            if margin_amount <= 0:
                return PreflightResult(ok=False, checks=checks, error="LIVE_TRADING_MARGIN_AMOUNT 必须大于 0")
            add_check("leverage", leverage == Decimal("10"), self.config.leverage)
            if leverage != Decimal("10"):
                return PreflightResult(ok=False, checks=checks, error="当前实盘固定要求 LIVE_TRADING_LEVERAGE=10")
            add_check(
                "position_mode",
                self.config.position_mode == "hedge_mode",
                {
                    "configured": self.config.position_mode,
                    "required": "hedge_mode",
                    "note": "实盘下单前请在 Bitget 后台确认 USDT-FUTURES 已是双向持仓；程序不会在信号触发时临时切换持仓模式。",
                },
            )
            if self.config.position_mode != "hedge_mode":
                return PreflightResult(ok=False, checks=checks, error="当前实盘固定要求 LIVE_TRADING_POSITION_MODE=hedge_mode")
            risk_ok, risk_detail = self._preflight_live_risk()
            add_check("live_risk_config", risk_ok, risk_detail)
            if not risk_ok:
                return PreflightResult(ok=False, checks=checks, error="实盘持仓风控配置不安全，请先修正 LIVE_TRADING_RISK_* 配置。")

            client = BitgetFuturesTradeClient(self.project_root)
            add_check("api_credentials", True, "Bitget API 凭据已加载")

            positions_payload = client.get_all_positions(product_type=self.config.product_type, margin_coin=self.config.margin_coin)
            add_check("private_positions", True, {"code": positions_payload.get("code"), "items": len(positions_payload.get("data") or [])})

            if configure_account:
                open_positions = self._exchange_open_positions(client)
                add_check("open_positions_before_account_setup", not open_positions, {"open_count": len(open_positions), "positions": open_positions})
                if open_positions:
                    return PreflightResult(
                        ok=False,
                        checks=checks,
                        error="当前产品线仍有持仓，Bitget 不允许切换逐仓/全仓；请先处理持仓后再运行 --preflight。",
                    )
                margin_mode_response = client.set_margin_mode(
                    symbol=symbol,
                    product_type=self.config.product_type,
                    margin_coin=self.config.margin_coin,
                    margin_mode="isolated",
                )
                add_check("set_margin_mode", True, margin_mode_response)
                long_leverage_response = client.set_leverage(
                    symbol=symbol,
                    product_type=self.config.product_type,
                    margin_coin=self.config.margin_coin,
                    leverage="10",
                    hold_side="long",
                )
                add_check("set_long_leverage", True, long_leverage_response)
                short_leverage_response = client.set_leverage(
                    symbol=symbol,
                    product_type=self.config.product_type,
                    margin_coin=self.config.margin_coin,
                    leverage="10",
                    hold_side="short",
                )
                add_check("set_short_leverage", True, short_leverage_response)
            else:
                add_check(
                    "account_setup",
                    True,
                    "运行前请用 --preflight 完成逐仓和 10 倍杠杆设置；真实信号触发时不会临时切换账户/合约设置。",
                )

            ticker = client.get_ticker(symbol=symbol, product_type=self.config.product_type)
            mark_price = ticker.get("markPrice")
            last_price = ticker.get("lastPr")
            add_check("ticker", bool(mark_price or last_price), {"markPrice": mark_price, "lastPr": last_price})
            entry_price = _ticker_price_for_entry_source(ticker, self.config.entry_price_source)
            if entry_price <= 0:
                return PreflightResult(ok=False, checks=checks, error="ticker 价格无效，无法计算 5U/10x 下单数量。")
            size = self._order_size_from_margin(entry_price)
            add_check(
                "computed_order_size",
                size > 0,
                {
                    "entry_price": _decimal_to_string(entry_price),
                    "margin_amount": self.config.margin_amount,
                    "leverage": self.config.leverage,
                    "notional": _decimal_to_string(margin_amount * leverage),
                    "size": _decimal_to_string(size),
                },
            )

            contracts = client.get_contracts(product_type=self.config.product_type)
            contract = next((item for item in contracts if str(item.get("symbol") or "").upper() == symbol.upper()), None)
            add_check("contract", contract is not None, self._contract_preflight_detail(contract))
            if contract is None:
                return PreflightResult(ok=False, checks=checks, error=f"未找到 Bitget 合约: {symbol}")

            precision_ok, precision_detail = self._preflight_precision(size=size, contract=contract)
            add_check("precision", precision_ok, precision_detail)
            if not precision_ok:
                return PreflightResult(ok=False, checks=checks, error="价格或数量精度配置可能不符合合约规格")

            futures_available = self._futures_available(client)
            add_check("futures_balance_query", True, {"available": _decimal_to_string(futures_available)})
            spot_available = self._spot_available(client)
            add_check("spot_balance_query", True, {"available": _decimal_to_string(spot_available)})
            required_available = self._required_futures_available()
            shortfall = max(required_available - futures_available, Decimal("0"))
            add_check(
                "futures_available",
                futures_available >= required_available or (self.config.auto_transfer_enabled and spot_available >= shortfall),
                {
                    "available": _decimal_to_string(futures_available),
                    "required_margin": _decimal_to_string(margin_amount),
                    "required_with_buffer": _decimal_to_string(required_available),
                    "auto_transfer_multiplier": self.config.auto_transfer_multiplier,
                    "auto_transfer_buffer": self.config.auto_transfer_buffer,
                    "shortfall": _decimal_to_string(shortfall),
                    "spot_available": _decimal_to_string(spot_available),
                    "auto_transfer_from_spot": self.config.auto_transfer_enabled,
                },
            )
            if futures_available < required_available and not self.config.auto_transfer_enabled:
                return PreflightResult(ok=False, checks=checks, error="合约账户 USDT 不足目标预留保证金，且自动现货划转未启用。")
            if shortfall > 0 and spot_available < shortfall:
                return PreflightResult(ok=False, checks=checks, error="合约账户 USDT 不足，现货账户余额也不足以补足目标预留保证金。")

            return PreflightResult(ok=all(bool(item.get("ok")) for item in checks), checks=checks)
        except Exception as exc:
            add_check("exception", False, str(exc))
            return PreflightResult(ok=False, checks=checks, error=str(exc))

    def _contract_preflight_detail(self, contract: dict[str, Any] | None) -> dict[str, Any]:
        if not contract:
            return {}
        keys = [
            "symbol",
            "symbolStatus",
            "minTradeNum",
            "sizeMultiplier",
            "priceEndStep",
            "volumePlace",
            "pricePlace",
        ]
        return {key: contract.get(key) for key in keys if key in contract}

    def _preflight_live_risk(self) -> tuple[bool, dict[str, Any]]:
        if not self.config.risk_exits_enabled:
            return False, {"risk_exits_enabled": False, "error": "真实实盘必须启用持仓风控出场。"}
        detail: dict[str, Any] = {
            "risk_exits_enabled": self.config.risk_exits_enabled,
            "risk_check_interval_seconds": self.config.risk_check_interval_seconds,
            "risk_error_email_cooldown_seconds": self.config.risk_error_email_cooldown_seconds,
            "exchange_disaster_sl_enabled": self.config.exchange_disaster_sl_enabled,
            "risk_close_managed_size_only": self.config.risk_close_managed_size_only,
            "risk_price_source": self.config.risk_price_source,
        }
        errors: list[str] = []
        if self.config.risk_check_interval_seconds > 10:
            errors.append("LIVE_TRADING_RISK_CHECK_INTERVAL_SECONDS 建议不超过 10 秒。")
        if self.config.risk_error_email_cooldown_seconds < 60:
            errors.append("LIVE_TRADING_RISK_ERROR_EMAIL_COOLDOWN_SECONDS 建议至少 60 秒，避免异常时刷屏。")
        if not self.config.exchange_disaster_sl_enabled:
            errors.append("必须启用 LIVE_TRADING_EXCHANGE_DISASTER_SL_ENABLED，给程序断线/宕机留交易所端灾难止损。")
        if not self.config.risk_close_managed_size_only:
            errors.append("必须启用 LIVE_TRADING_RISK_CLOSE_MANAGED_SIZE_ONLY，避免手动同向加仓后被一键全平。")
        if self.config.risk_price_source not in {"mark_price", "market", "last", "index_price"}:
            errors.append(f"LIVE_TRADING_RISK_PRICE_SOURCE 无效: {self.config.risk_price_source}")

        numeric_fields = {
            "risk_startup_max_favorable_points": self.config.risk_startup_max_favorable_points,
            "risk_startup_current_points": self.config.risk_startup_current_points,
            "risk_disaster_stop_points": self.config.risk_disaster_stop_points,
            "risk_breakeven_trigger_points": self.config.risk_breakeven_trigger_points,
            "risk_breakeven_stop_points": self.config.risk_breakeven_stop_points,
            "risk_trailing_trigger_1_points": self.config.risk_trailing_trigger_1_points,
            "risk_trailing_protect_1_ratio": self.config.risk_trailing_protect_1_ratio,
            "risk_trailing_trigger_2_points": self.config.risk_trailing_trigger_2_points,
            "risk_trailing_protect_2_ratio": self.config.risk_trailing_protect_2_ratio,
            "risk_trailing_trigger_3_points": self.config.risk_trailing_trigger_3_points,
            "risk_trailing_protect_3_ratio": self.config.risk_trailing_protect_3_ratio,
        }
        parsed: dict[str, str] = {}
        for key, value in numeric_fields.items():
            try:
                parsed[key] = _decimal_to_string(_to_decimal(value))
            except Exception:
                errors.append(f"{key} 不是有效数字: {value}")
        detail["params"] = parsed

        if _to_decimal(self.config.risk_disaster_stop_points) >= 0:
            errors.append("LIVE_TRADING_RISK_DISASTER_STOP_POINTS 必须是负数。")
        if _to_decimal(self.config.risk_startup_current_points) >= 0:
            errors.append("LIVE_TRADING_RISK_STARTUP_CURRENT_POINTS 必须是负数。")
        if _to_decimal(self.config.risk_breakeven_trigger_points) <= 0:
            errors.append("LIVE_TRADING_RISK_BREAKEVEN_TRIGGER_POINTS 必须大于 0。")
        if _to_decimal(self.config.risk_breakeven_stop_points) < 0:
            errors.append("LIVE_TRADING_RISK_BREAKEVEN_STOP_POINTS 不能小于 0。")
        for key in (
            "risk_trailing_protect_1_ratio",
            "risk_trailing_protect_2_ratio",
            "risk_trailing_protect_3_ratio",
        ):
            value = _to_decimal(getattr(self.config, key))
            if value <= 0 or value >= 1:
                errors.append(f"{key} 必须在 0 到 1 之间。")
        detail["errors"] = errors
        return not errors, detail

    def _preflight_precision(self, *, size: Decimal, contract: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        detail = {
            "configured_size": self.config.size,
            "configured_price_decimals": self.config.price_decimals,
            "configured_size_decimals": self.config.size_decimals,
            "contract": self._contract_preflight_detail(contract),
        }
        min_trade = contract.get("minTradeNum")
        if min_trade not in (None, ""):
            min_trade_value = _to_decimal(min_trade)
            detail["minTradeNum"] = str(min_trade_value)
            if size < min_trade_value:
                detail["error"] = "下单数量小于 minTradeNum"
                return False, detail

        volume_place = contract.get("volumePlace")
        if volume_place not in (None, ""):
            try:
                contract_size_decimals = int(volume_place)
                detail["contract_size_decimals"] = contract_size_decimals
                if self.config.size_decimals > contract_size_decimals:
                    detail["warning"] = "配置的数量小数位多于合约 volumePlace"
            except (TypeError, ValueError):
                pass

        price_place = contract.get("pricePlace")
        if price_place not in (None, ""):
            try:
                contract_price_decimals = int(price_place)
                detail["contract_price_decimals"] = contract_price_decimals
                if self.config.price_decimals > contract_price_decimals:
                    detail["warning"] = "配置的价格小数位多于合约 pricePlace"
            except (TypeError, ValueError):
                pass

        return True, detail

    def evaluate_snapshot(self, snapshot: dict[str, Any]) -> TradeDecision:
        candles = snapshot.get("candles") or []
        symbol = str(snapshot.get("symbol") or "").upper()
        if not candles:
            return TradeDecision(action="skip", symbol=symbol, side=None, bar_time=None, reason="snapshot 中没有 K 线")

        target_index = -2 if self.config.use_closed_bar and len(candles) >= 2 else -1
        target_candle = candles[target_index]
        bar_time = int(target_candle["time"])
        marker_texts = self._marker_texts_at(snapshot, bar_time)
        indicator_values, indicator_colors = self._indicator_context_at(snapshot, bar_time)
        last_close = float(target_candle.get("close") or snapshot.get("last_close") or 0)
        bar_open = _optional_float(target_candle.get("open"))
        bar_high = _optional_float(target_candle.get("high"))
        bar_low = _optional_float(target_candle.get("low"))
        bar_close = _optional_float(target_candle.get("close"))
        side, reason = self._side_from_strategy(marker_texts, indicator_values, indicator_colors, bar_high=bar_high, bar_low=bar_low)
        htf_lock_key: str | None = None
        htf_context: dict[str, Any] = {}
        if side is not None:
            htf_ok, htf_reason, htf_context = self._higher_timeframe_hull_allows_side(side, snapshot.get("higher_timeframe"), symbol=symbol)
            if not htf_ok:
                side = None
                reason = htf_reason
            else:
                htf_lock_key = str(htf_context.get("lock_key") or "") or None
                reason = f"{reason}；{htf_reason}"
        atr_value = self._atr_at(snapshot, bar_time)
        bar_time_label = self._bar_time_label(snapshot, bar_time)
        if side is None:
            return TradeDecision(
                action="skip",
                symbol=symbol,
                side=None,
                bar_time=bar_time,
                marker_texts=marker_texts,
                indicator_values=indicator_values,
                indicator_colors=indicator_colors,
                reason=reason,
                last_close=last_close,
                bar_open=bar_open,
                bar_high=bar_high,
                bar_low=bar_low,
                bar_close=bar_close,
                bar_time_label=bar_time_label,
                atr_value=atr_value,
            )

        client_oid = self._client_oid(symbol, side, bar_time)
        return TradeDecision(
            action="place_order",
            symbol=symbol,
            side=side,
            bar_time=bar_time,
            marker_texts=marker_texts,
            indicator_values=indicator_values,
            indicator_colors=indicator_colors,
            reason=reason,
            last_close=last_close,
            bar_open=bar_open,
            bar_high=bar_high,
            bar_low=bar_low,
            bar_close=bar_close,
            bar_time_label=bar_time_label,
            atr_value=atr_value,
            client_oid=client_oid,
            htf_lock_key=htf_lock_key,
            htf_context=htf_context,
        )

    def execute_decision(self, decision: TradeDecision) -> TradeExecutionResult:
        if decision.action != "place_order" or decision.side is None:
            result = TradeExecutionResult(decision=decision, dry_run=self.config.dry_run, enabled=self.config.enabled)
            self._log_result(result)
            return result
        if self.config.mode == "off":
            result = TradeExecutionResult(
                decision=decision,
                dry_run=True,
                enabled=False,
                response={"modeOff": True, "message": "LIVE_TRADING_MODE=off：跳过执行、订单日志和邮件。"},
            )
            self._log_result(result)
            return result
        if self._already_executed(decision.client_oid or ""):
            result = TradeExecutionResult(
                decision=decision,
                dry_run=True,
                enabled=self.config.enabled,
                already_executed=True,
            )
            self._log_result(result)
            return result
        locked_entry = self._htf_entry_lock(decision)
        if locked_entry is not None:
            result = TradeExecutionResult(
                decision=decision,
                dry_run=True,
                enabled=self.config.enabled,
                response={
                    "htfEntryLocked": True,
                    "message": "同一个高周期过滤阶段内，同方向已开过仓；即使此前已平仓，本阶段也不再重复开同向仓位。",
                    "lock": locked_entry,
                    "htfContext": decision.htf_context,
                },
            )
            self._record_execution(result)
            self._log_result(result)
            self._send_email(result)
            return result
        self.sync_local_positions_with_exchange(symbol=decision.symbol)
        local_position = self._local_same_side_position(decision)
        if local_position is not None and not self._is_real_trading_mode():
            result = TradeExecutionResult(
                decision=decision,
                dry_run=True,
                enabled=self.config.enabled,
                response={
                    "sameSidePosition": True,
                    "source": "local",
                    "message": "本地仓位账本已有同方向仓位，跳过开仓。",
                    "position": local_position,
                },
            )
            self._record_execution(result)
            self._log_result(result)
            self._send_email(result)
            return result
        if self.config.log_only or self.config.dry_run or not self.config.enabled:
            same_side_position = self._same_side_position_if_available(decision)
            if same_side_position is not None:
                result = TradeExecutionResult(
                    decision=decision,
                    dry_run=True,
                    enabled=self.config.enabled,
                    response={
                        "sameSidePosition": True,
                        "message": "已存在同方向仓位，观察/邮件模式跳过开仓提醒。",
                        "position": same_side_position,
                    },
                )
                self._record_execution(result)
                self._log_result(result)
                self._send_email(result)
                return result
        if self.config.log_only:
            result = TradeExecutionResult(
                decision=decision,
                dry_run=True,
                enabled=False,
                response={"logOnly": True, "message": "观察模式：仅打印日志并发送邮件，不构造真实下单请求。"},
            )
            self._record_execution(result)
            self._log_result(result)
            self._send_email(result)
            return result
        if self.config.dry_run or not self.config.enabled:
            try:
                request = self._order_request(decision)
            except Exception as exc:
                request = {"error": f"构造开仓请求失败: {exc}"}
            result = TradeExecutionResult(
                decision=decision,
                dry_run=True,
                enabled=self.config.enabled,
                request=request,
                response={"dryRun": True, "message": "未启用真实交易或仍处于 dry-run。"},
            )
            self._record_execution(result)
            self._log_result(result)
            self._send_email(result)
            return result

        preflight = self.run_preflight(symbol=decision.symbol)
        if not preflight.ok:
            try:
                request = self._order_request(decision)
            except Exception as exc:
                request = {"error": f"构造开仓请求失败: {exc}"}
            result = TradeExecutionResult(
                decision=decision,
                dry_run=False,
                enabled=self.config.enabled,
                request=request,
                response={"preflight": asdict(preflight)},
                error=f"Bitget 实盘预检查失败: {preflight.error or 'unknown'}",
            )
            self._log_result(result)
            self._send_email(result)
            return result

        request: dict[str, Any] | None = None
        order_response: dict[str, Any] | None = None
        reverse_close_response: dict[str, Any] | None = None
        fund_response: dict[str, Any] | None = None
        exchange_stop_response: dict[str, Any] | None = None
        entry_price: Decimal | None = None
        try:
            client = BitgetFuturesTradeClient(self.project_root)
            time_allowed, time_reason = self._entry_time_allowed()
            if not time_allowed:
                result = TradeExecutionResult(
                    decision=decision,
                    dry_run=True,
                    enabled=self.config.enabled,
                    request=request,
                    response={
                        "entryTimeBlocked": True,
                        "message": time_reason,
                        "entryTimeStart": self.config.entry_time_start,
                        "entryTimeEnd": self.config.entry_time_end,
                        "timezone": str(DISPLAY_TIMEZONE),
                    },
                )
                self._log_result(result)
                self._send_email(result)
                return result
            reverse_position = self._opposite_side_position(client, decision)
            if reverse_position is not None:
                reverse_close_response = self._close_opposite_position(client, decision, reverse_position)
                self.sync_local_positions_with_exchange(symbol=decision.symbol, force=True)
            same_side_position = self._same_side_position(client, decision)
            if same_side_position is not None:
                result = TradeExecutionResult(
                    decision=decision,
                    dry_run=True,
                    enabled=self.config.enabled,
                    request=request,
                    response={
                        "sameSidePosition": True,
                        "message": "已存在同方向仓位，跳过开仓；如刚平掉反向仓位，本次只完成平仓不反手。",
                        "position": same_side_position,
                        "reverseClose": reverse_close_response,
                    },
                )
                self._record_execution(result)
                self._log_result(result)
                self._send_email(result)
                return result
            late_reverse_close_response = self._close_opposite_position_if_needed(client, decision)
            if late_reverse_close_response is not None:
                reverse_close_response = _append_reverse_close_response(reverse_close_response, late_reverse_close_response)
                self.sync_local_positions_with_exchange(symbol=decision.symbol, force=True)
            entry_price = self._entry_price(client, decision)
            fund_response = self._ensure_futures_margin_available(client)
            request = self._order_request(decision, entry_price=entry_price)
            order_response = client.place_order(request)
            if self.config.risk_exits_enabled and self.config.exchange_disaster_sl_enabled:
                try:
                    self._wait_until_same_side_position_open(client, decision)
                    exchange_stop_response = self._place_exchange_disaster_stop(client, decision, entry_price)
                except Exception as stop_exc:
                    emergency_close_response = self._close_opposite_position_if_needed(client, TradeDecision(
                        action="place_order",
                        symbol=decision.symbol,
                        side="sell" if decision.side == "buy" else "buy",
                        bar_time=decision.bar_time,
                    ))
                    raise RuntimeError(
                        "交易所服务器端灾难止损设置失败，已尝试立即平掉刚开的仓；"
                        f"stop_error={stop_exc}; emergency_close={emergency_close_response}"
                    ) from stop_exc
            result = TradeExecutionResult(
                decision=decision,
                dry_run=False,
                enabled=True,
                request=request,
                response={
                    "entryPrice": _decimal_to_string(entry_price),
                    "order": order_response,
                    "reverseClose": reverse_close_response,
                    "funding": fund_response,
                    "exchangeDisasterStop": exchange_stop_response,
                    "accountSetup": "skipped_at_signal_time",
                },
            )
        except Exception as exc:
            result = TradeExecutionResult(
                decision=decision,
                dry_run=False,
                enabled=True,
                request=request,
                response={
                    "entryPrice": _decimal_to_string(entry_price) if entry_price is not None else None,
                    "order": order_response,
                    "reverseClose": reverse_close_response,
                    "funding": fund_response,
                    "exchangeDisasterStop": exchange_stop_response,
                    "accountSetup": "skipped_at_signal_time",
                },
                error=str(exc),
            )

        self._record_execution(result)
        self._log_result(result)
        self._send_email(result)
        return result

    def check_runtime_state(
        self,
        *,
        tickers: dict[str, dict[str, Any]] | None = None,
        sync_positions: bool = True,
    ) -> list[TradeExecutionResult]:
        if sync_positions:
            self.sync_local_positions_with_exchange()
        return self.check_live_risk_exits(tickers=tickers, use_exchange_positions=sync_positions)

    def check_live_risk_exits(
        self,
        *,
        tickers: dict[str, dict[str, Any]] | None = None,
        use_exchange_positions: bool = True,
    ) -> list[TradeExecutionResult]:
        if not self.config.risk_exits_enabled:
            return []
        if not self._is_real_trading_mode():
            return []

        state = self._read_state()
        positions = [item for item in state.get("local_positions") or [] if isinstance(item, dict)]
        client: BitgetFuturesTradeClient | None = None
        if use_exchange_positions:
            client = BitgetFuturesTradeClient(self.project_root)
            exchange_positions = self._exchange_open_positions(client)
        else:
            exchange_positions = self._local_risk_managed_positions(positions, tickers=tickers)
        if not exchange_positions:
            return []
        if client is None:
            client = BitgetFuturesTradeClient(self.project_root)
        now_ms = int(time.time() * 1000)
        changed = False
        results: list[TradeExecutionResult] = []

        for exchange_position in exchange_positions:
            local_position = exchange_position.pop("_local_position", None) or self._matching_open_local_position(positions, exchange_position)
            if local_position is None:
                continue
            if local_position.get("risk_managed") is False:
                continue
            try:
                ticker = self._ticker_for_live_risk(client, exchange_position["symbol"], tickers=tickers)
                current_price = _ticker_price_for_entry_source(ticker, self.config.risk_price_source)
                risk_state = self._update_live_risk_state(local_position, exchange_position, current_price, now_ms)
                changed = True
                exit_reason = self._live_risk_exit_reason(local_position, risk_state, now_ms)
                if exit_reason is None:
                    self._sync_exchange_protective_stop(client, local_position, exchange_position, risk_state)
                    continue
                result = self._close_position_for_live_risk(client, exchange_position, local_position, risk_state, exit_reason)
                results.append(result)
                local_position["status"] = "closed"
                local_position["closed_at"] = now_ms
                local_position["close_reason"] = exit_reason["reason"]
                local_position["risk_exit"] = exit_reason
                local_position["risk_state_at_close"] = risk_state
                self._cancel_known_exchange_stop_if_needed(client, local_position)
                response = result.response if isinstance(result.response, dict) else {}
                if response.get("closeMode") == "managed_size_order":
                    exclusions = state.get("risk_excluded_position_keys")
                    if not isinstance(exclusions, dict):
                        exclusions = {}
                    key = self._position_key(exchange_position["symbol"], exchange_position["side"])
                    exclusions[key] = {
                        "ts": now_ms,
                        "reason": "本策略只平 managed_size，剩余同向仓位视为手动/外部仓位，排除自动风控直到该方向仓位清空。",
                        "closeMode": response.get("closeMode"),
                    }
                    state["risk_excluded_position_keys"] = exclusions
                changed = True
            except Exception as exc:
                result = self._record_live_risk_error(exchange_position, local_position, exc, now_ms)
                if result is not None:
                    results.append(result)
                changed = True

        if changed:
            state["local_positions"] = positions[-500:]
            state["last_live_risk_check"] = {
                "ts": now_ms,
                "open_count": len(exchange_positions),
                "closed_count": len([item for item in results if not item.error]),
            }
            self._write_state(state)
        return results

    def _record_live_risk_error(
        self,
        exchange_position: dict[str, Any],
        local_position: dict[str, Any],
        exc: Exception,
        now_ms: int,
    ) -> TradeExecutionResult | None:
        error_key = f"risk_error_last_email_at:{self._position_key(exchange_position['symbol'], exchange_position['side'])}"
        last_email_at = _optional_int(local_position.get(error_key)) or 0
        cooldown_ms = int(max(self.config.risk_error_email_cooldown_seconds, 0) * 1000)
        local_position["risk_last_error"] = str(exc)
        local_position["risk_last_error_at"] = now_ms
        if cooldown_ms > 0 and now_ms - last_email_at < cooldown_ms:
            self.logger.error("实盘持仓风控检查失败，邮件冷却中: %s", exc)
            return None

        local_position[error_key] = now_ms
        decision = self._risk_close_decision(exchange_position, local_position, reason=f"实盘持仓风控检查失败: {exc}")
        result = TradeExecutionResult(
            decision=decision,
            dry_run=False,
            enabled=True,
            response={
                "position": exchange_position,
                "localPosition": local_position,
                "emailCooldownSeconds": self.config.risk_error_email_cooldown_seconds,
            },
            error=str(exc),
        )
        self._record_execution(result)
        self._log_result(result)
        self._send_email(result)
        return result

    def _matching_open_local_position(
        self,
        positions: list[dict[str, Any]],
        exchange_position: dict[str, Any],
    ) -> dict[str, Any] | None:
        expected_key = self._position_key(exchange_position["symbol"], exchange_position["side"])
        for position in reversed(positions):
            if str(position.get("status") or "open").lower() != "open":
                continue
            key = self._position_key(str(position.get("symbol") or "").upper(), str(position.get("side") or ""))
            if key == expected_key:
                return position
        return None

    def _local_risk_managed_positions(
        self,
        positions: list[dict[str, Any]],
        *,
        tickers: dict[str, dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        scoped_symbols = {symbol.upper() for symbol in (tickers or {})}
        result: list[dict[str, Any]] = []
        for position in positions:
            if str(position.get("status") or "open").lower() != "open":
                continue
            if position.get("risk_managed") is False:
                continue
            symbol = str(position.get("symbol") or "").upper()
            side = str(position.get("side") or "").lower()
            if not symbol or side not in {"buy", "sell"}:
                continue
            if scoped_symbols and symbol not in scoped_symbols:
                continue
            result.append(
                {
                    "symbol": symbol,
                    "side": side,
                    "holdSide": str(position.get("holdSide") or ("long" if side == "buy" else "short")),
                    "size": str(position.get("exchange_size") or position.get("size") or position.get("managed_size") or ""),
                    "raw": position.get("exchange_position") if isinstance(position.get("exchange_position"), dict) else {},
                    "_local_position": position,
                }
            )
        return result

    def _ticker_for_live_risk(
        self,
        client: BitgetFuturesTradeClient,
        symbol: str,
        *,
        tickers: dict[str, dict[str, Any]] | None,
    ) -> dict[str, Any]:
        ticker = (tickers or {}).get(symbol.upper())
        if ticker and self._is_fresh_ws_ticker(ticker):
            return ticker
        return client.get_ticker(symbol=symbol, product_type=self.config.product_type)

    def _is_fresh_ws_ticker(self, ticker: dict[str, Any]) -> bool:
        if ticker.get("_source") != "bitget_ws_ticker":
            return False
        received_at = _optional_int(ticker.get("_received_at_ms")) or 0
        if received_at <= 0:
            return False
        max_age_ms = int(max(self.config.risk_websocket_ticker_stale_seconds, 0.5) * 1000)
        return int(time.time() * 1000) - received_at <= max_age_ms

    def _update_live_risk_state(
        self,
        local_position: dict[str, Any],
        exchange_position: dict[str, Any],
        current_price: Decimal,
        now_ms: int,
    ) -> dict[str, Any]:
        entry_price = self._live_risk_entry_price(local_position, exchange_position)
        side = str(exchange_position.get("side") or local_position.get("side") or "").lower()
        current_points = current_price - entry_price if side == "buy" else entry_price - current_price
        previous_max_favorable = _optional_decimal(local_position.get("risk_max_favorable_points")) or Decimal("0")
        previous_max_adverse = _optional_decimal(local_position.get("risk_max_adverse_points")) or Decimal("0")
        max_favorable = max(previous_max_favorable, current_points)
        max_adverse = min(previous_max_adverse, current_points)
        protected_stop = self._live_risk_protected_stop(max_favorable, local_position.get("risk_protected_stop_points"))

        local_position["entry_price"] = _decimal_to_string(entry_price)
        local_position["risk_current_price"] = _decimal_to_string(current_price)
        local_position["risk_current_points"] = _decimal_to_string(current_points)
        local_position["risk_max_favorable_points"] = _decimal_to_string(max_favorable)
        local_position["risk_max_adverse_points"] = _decimal_to_string(max_adverse)
        local_position["risk_protected_stop_points"] = _decimal_to_string(protected_stop) if protected_stop is not None else None
        local_position["risk_last_checked_at"] = now_ms

        return {
            "entry_price": _decimal_to_string(entry_price),
            "current_price": _decimal_to_string(current_price),
            "current_points": _decimal_to_string(current_points),
            "max_favorable_points": _decimal_to_string(max_favorable),
            "max_adverse_points": _decimal_to_string(max_adverse),
            "protected_stop_points": _decimal_to_string(protected_stop) if protected_stop is not None else None,
            "startup_checked": bool(local_position.get("risk_startup_checked")),
        }

    def _live_risk_entry_price(
        self,
        local_position: dict[str, Any],
        exchange_position: dict[str, Any],
    ) -> Decimal:
        for value in (local_position.get("entry_price"), local_position.get("entryPrice")):
            if value not in (None, ""):
                return _to_decimal(value)
        raw = exchange_position.get("raw") if isinstance(exchange_position.get("raw"), dict) else {}
        for key in ("openPriceAvg", "averageOpenPrice", "avgOpenPrice", "openPrice", "breakEvenPrice"):
            value = raw.get(key)
            if value not in (None, ""):
                return _to_decimal(value)
        raise RuntimeError("缺少仓位入场价，无法执行实盘持仓风控。")

    def _live_risk_protected_stop(self, max_favorable: Decimal, previous_value: Any) -> Decimal | None:
        protected = _optional_decimal(previous_value)
        breakeven_trigger = _to_decimal(self.config.risk_breakeven_trigger_points)
        if max_favorable >= breakeven_trigger:
            protected = max(protected or Decimal("-Infinity"), _to_decimal(self.config.risk_breakeven_stop_points))

        trailing_rules = (
            (self.config.risk_trailing_trigger_1_points, self.config.risk_trailing_protect_1_ratio),
            (self.config.risk_trailing_trigger_2_points, self.config.risk_trailing_protect_2_ratio),
            (self.config.risk_trailing_trigger_3_points, self.config.risk_trailing_protect_3_ratio),
        )
        for trigger_text, ratio_text in trailing_rules:
            trigger = _to_decimal(trigger_text)
            ratio = _to_decimal(ratio_text)
            if max_favorable >= trigger:
                candidate = max_favorable * ratio
                protected = max(protected or Decimal("-Infinity"), candidate)
        return protected

    def _live_risk_exit_reason(
        self,
        local_position: dict[str, Any],
        risk_state: dict[str, Any],
        now_ms: int,
    ) -> dict[str, Any] | None:
        current_points = _to_decimal(risk_state["current_points"])
        max_favorable = _to_decimal(risk_state["max_favorable_points"])
        max_adverse = _to_decimal(risk_state["max_adverse_points"])

        disaster_stop = _to_decimal(self.config.risk_disaster_stop_points)
        if max_adverse <= disaster_stop or current_points <= disaster_stop:
            return {
                "reason": "live_disaster_stop",
                "message": f"灾难硬止损触发：最大浮亏 {risk_state['max_adverse_points']} 点，当前 {risk_state['current_points']} 点。",
                "rule": {"disaster_stop_points": self.config.risk_disaster_stop_points},
                "risk": risk_state,
            }

        protected_stop_text = risk_state.get("protected_stop_points")
        if protected_stop_text not in (None, ""):
            protected_stop = _to_decimal(protected_stop_text)
            if current_points <= protected_stop:
                return {
                    "reason": "live_protected_stop",
                    "message": f"保护止损触发：保护线 {protected_stop_text} 点，当前 {risk_state['current_points']} 点。",
                    "rule": {
                        "breakeven_trigger_points": self.config.risk_breakeven_trigger_points,
                        "breakeven_stop_points": self.config.risk_breakeven_stop_points,
                        "trailing_1": [self.config.risk_trailing_trigger_1_points, self.config.risk_trailing_protect_1_ratio],
                        "trailing_2": [self.config.risk_trailing_trigger_2_points, self.config.risk_trailing_protect_2_ratio],
                        "trailing_3": [self.config.risk_trailing_trigger_3_points, self.config.risk_trailing_protect_3_ratio],
                    },
                    "risk": risk_state,
                }

        if not bool(local_position.get("risk_startup_checked")):
            closed_bars = _closed_5m_bars_since_entry(local_position, now_ms)
            required_bars = max(int(self.config.risk_startup_check_bars_5m), 1)
            if closed_bars is not None and closed_bars >= required_bars:
                local_position["risk_startup_checked"] = True
                if (
                    max_favorable < _to_decimal(self.config.risk_startup_max_favorable_points)
                    and current_points < _to_decimal(self.config.risk_startup_current_points)
                ):
                    return {
                        "reason": "live_startup_failure_stop",
                        "message": (
                            f"启动失败止损触发：{self.config.risk_startup_check_bars_5m} 根 5m 后，"
                            f"最大浮盈 {risk_state['max_favorable_points']} 点 < {self.config.risk_startup_max_favorable_points} 点，"
                            f"当前 {risk_state['current_points']} 点 < {self.config.risk_startup_current_points} 点。"
                        ),
                        "rule": {
                            "startup_check_bars_5m": self.config.risk_startup_check_bars_5m,
                            "closed_5m_bars_since_entry": closed_bars,
                            "startup_max_favorable_points": self.config.risk_startup_max_favorable_points,
                            "startup_current_points": self.config.risk_startup_current_points,
                        },
                        "risk": risk_state,
                    }
        return None

    def _close_position_for_live_risk(
        self,
        client: BitgetFuturesTradeClient,
        exchange_position: dict[str, Any],
        local_position: dict[str, Any],
        risk_state: dict[str, Any],
        exit_reason: dict[str, Any],
    ) -> TradeExecutionResult:
        payload, close_mode = self._risk_close_payload(exchange_position, local_position)
        response = client.place_order(payload) if close_mode == "managed_size_order" else client.close_position_order(payload)
        failures = ((response.get("data") or {}).get("failureList") or []) if isinstance(response, dict) else []
        if failures:
            raise RuntimeError(f"实盘风控平仓失败: {json.dumps(failures, ensure_ascii=False)}")
        close_confirmed = (
            self._wait_until_exchange_position_reduced(client, exchange_position, _to_decimal(payload["size"]))
            if close_mode == "managed_size_order"
            else self._wait_until_exchange_position_closed(client, exchange_position)
        )

        decision = self._risk_close_decision(exchange_position, local_position, reason=exit_reason.get("message") or exit_reason["reason"])
        result = TradeExecutionResult(
            decision=decision,
            dry_run=False,
            enabled=True,
            request=payload,
            response={
                "riskExit": exit_reason,
                "riskState": risk_state,
                "close": response,
                "closeMode": close_mode,
                "closeConfirmed": close_confirmed,
                "position": exchange_position,
                "localPosition": local_position,
            },
        )
        self._record_execution(result)
        self._log_result(result)
        self._send_email(result)
        self.logger.warning("实盘风控已提交平仓: %s", json.dumps(asdict(result), ensure_ascii=False))
        return result

    def _sync_exchange_protective_stop(
        self,
        client: BitgetFuturesTradeClient,
        local_position: dict[str, Any],
        exchange_position: dict[str, Any],
        risk_state: dict[str, Any],
    ) -> None:
        if not self.config.exchange_disaster_sl_enabled:
            return
        protected_stop_text = risk_state.get("protected_stop_points")
        if protected_stop_text in (None, ""):
            return
        order_id = str(local_position.get("exchange_stop_order_id") or "").strip()
        client_oid = str(local_position.get("exchange_stop_client_oid") or "").strip()
        if not order_id and not client_oid:
            return

        entry_price = _to_decimal(risk_state["entry_price"])
        protected_points = _to_decimal(protected_stop_text)
        side = str(exchange_position.get("side") or local_position.get("side") or "").lower()
        trigger_price = entry_price + protected_points if side == "buy" else entry_price - protected_points
        trigger_price = _quantize_decimal(trigger_price, self.config.price_decimals)
        previous_trigger = _optional_decimal(local_position.get("exchange_stop_trigger_price"))
        if previous_trigger is not None:
            if side == "buy" and trigger_price <= previous_trigger:
                return
            if side == "sell" and trigger_price >= previous_trigger:
                return

        size = _optional_decimal(local_position.get("managed_size")) or _optional_decimal(local_position.get("size"))
        if size is None or size <= 0:
            return
        payload = {
            "marginCoin": self.config.margin_coin,
            "productType": self.config.product_type,
            "symbol": exchange_position["symbol"],
            "triggerPrice": _decimal_to_string(trigger_price),
            "triggerType": "mark_price",
            "executePrice": "0",
            "size": _decimal_to_string(size),
        }
        if order_id:
            payload["orderId"] = order_id
        if client_oid:
            payload["clientOid"] = client_oid
        response = client.modify_tpsl_order(payload)
        ref = _tpsl_order_ref(response)
        local_position["exchange_stop_order_id"] = ref.get("orderId") or order_id or None
        local_position["exchange_stop_client_oid"] = ref.get("clientOid") or client_oid or None
        local_position["exchange_stop_trigger_price"] = _decimal_to_string(trigger_price)
        local_position["exchange_stop_kind"] = "protective"
        local_position["exchange_stop_updated_at"] = int(time.time() * 1000)
        local_position["exchange_stop_update_response"] = response
        self.logger.warning(
            "已上移交易所服务器端保护止损: %s",
            json.dumps({"request": payload, "response": response}, ensure_ascii=False),
        )

    def _cancel_known_exchange_stop_if_needed(
        self,
        client: BitgetFuturesTradeClient,
        local_position: dict[str, Any],
    ) -> None:
        order_id = str(local_position.get("exchange_stop_order_id") or "").strip()
        client_oid = str(local_position.get("exchange_stop_client_oid") or "").strip()
        if not order_id and not client_oid:
            return
        payload: dict[str, Any] = {
            "symbol": str(local_position.get("symbol") or "").upper(),
            "productType": self.config.product_type,
            "marginCoin": self.config.margin_coin,
            "planType": "loss_plan",
            "orderIdList": [
                {
                    "orderId": order_id,
                    "clientOid": client_oid,
                }
            ],
        }
        try:
            response = client.cancel_plan_order(payload)
            local_position["exchange_stop_cancel_response"] = response
            local_position["exchange_stop_cancelled_at"] = int(time.time() * 1000)
            self.logger.info("已取消已知交易所止损计划单: %s", json.dumps({"request": payload, "response": response}, ensure_ascii=False))
        except Exception as exc:
            local_position["exchange_stop_cancel_error"] = str(exc)
            local_position["exchange_stop_cancel_error_at"] = int(time.time() * 1000)
            self.logger.warning("取消交易所止损计划单失败，可能已由交易所自动取消: %s", exc)

    def _risk_close_payload(
        self,
        exchange_position: dict[str, Any],
        local_position: dict[str, Any],
    ) -> tuple[dict[str, Any], str]:
        symbol = exchange_position["symbol"]
        side = str(exchange_position.get("side") or local_position.get("side") or "").lower()
        exchange_size = _optional_decimal(exchange_position.get("size")) or Decimal("0")
        local_size = _optional_decimal(local_position.get("managed_size")) or _optional_decimal(local_position.get("size"))
        if (
            self.config.risk_close_managed_size_only
            and self.config.position_mode == "hedge_mode"
            and local_size is not None
            and local_size > 0
            and exchange_size > local_size
        ):
            return (
                {
                    "symbol": symbol,
                    "productType": self.config.product_type,
                    "marginMode": "isolated",
                    "marginCoin": self.config.margin_coin,
                    "size": _decimal_to_string(local_size),
                    "side": side,
                    "tradeSide": "close",
                    "orderType": "market",
                    "clientOid": f"tq-risk-close-{symbol.lower()}-{side}-{int(time.time())}"[:64],
                },
                "managed_size_order",
            )

        payload = {
            "symbol": symbol,
            "productType": self.config.product_type,
        }
        if self.config.position_mode == "hedge_mode":
            hold_side = str(exchange_position.get("holdSide") or ("long" if side == "buy" else "short"))
            if hold_side not in {"long", "short"}:
                raise RuntimeError(f"缺少有效 holdSide，拒绝调用 close-positions 以避免误平仓: {exchange_position}")
            payload["holdSide"] = hold_side
        return payload, "flash_close_position"

    def _wait_until_exchange_position_closed(
        self,
        client: BitgetFuturesTradeClient,
        position: dict[str, Any],
    ) -> bool:
        symbol = str(position.get("symbol") or "").upper()
        side = str(position.get("side") or "").lower()
        for attempt in range(1, 5):
            remaining = [
                item
                for item in self._exchange_open_positions(client, symbol=symbol)
                if item.get("side") == side
            ]
            if not remaining:
                return True
            if attempt < 4:
                time.sleep(1)
        raise RuntimeError(f"风控平仓后仍检测到 {symbol} {side} 持仓，拒绝标记本地仓位已关闭。")

    def _wait_until_exchange_position_reduced(
        self,
        client: BitgetFuturesTradeClient,
        position: dict[str, Any],
        close_size: Decimal,
    ) -> bool:
        symbol = str(position.get("symbol") or "").upper()
        side = str(position.get("side") or "").lower()
        before_size = _optional_decimal(position.get("size")) or Decimal("0")
        target_size = max(before_size - close_size, Decimal("0"))
        for attempt in range(1, 5):
            remaining = [
                item
                for item in self._exchange_open_positions(client, symbol=symbol)
                if item.get("side") == side
            ]
            current_size = _optional_decimal(remaining[0].get("size")) if remaining else Decimal("0")
            if (current_size or Decimal("0")) <= target_size:
                return True
            if attempt < 4:
                time.sleep(1)
        raise RuntimeError(
            f"风控按策略 size 平仓后仓位未减少到目标值: symbol={symbol} side={side} "
            f"before={before_size} close_size={close_size} target={target_size}"
        )

    def _risk_close_decision(
        self,
        exchange_position: dict[str, Any],
        local_position: dict[str, Any] | None = None,
        *,
        reason: str,
    ) -> TradeDecision:
        local_position = local_position or {}
        current_price = _optional_float(local_position.get("risk_current_price"))
        return TradeDecision(
            action="risk_close",
            symbol=str(exchange_position.get("symbol") or local_position.get("symbol") or "").upper(),
            side=str(exchange_position.get("side") or local_position.get("side") or "").lower() or None,
            bar_time=None,
            marker_texts=[],
            indicator_values={},
            indicator_colors={},
            reason=reason,
            last_close=current_price,
            bar_close=current_price,
            bar_time_label=datetime.now(DISPLAY_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S"),
        )

    def _opposite_side_position(
        self,
        client: BitgetFuturesTradeClient,
        decision: TradeDecision,
    ) -> dict[str, Any] | None:
        if decision.side not in {"buy", "sell"}:
            return None
        opposite_side = "sell" if decision.side == "buy" else "buy"
        for position in self._exchange_open_positions(client, symbol=decision.symbol):
            if position.get("side") == opposite_side:
                return position
        return None

    def _close_opposite_position(
        self,
        client: BitgetFuturesTradeClient,
        decision: TradeDecision,
        position: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "symbol": decision.symbol,
            "productType": self.config.product_type,
        }
        if self.config.position_mode == "hedge_mode":
            payload["holdSide"] = str(position.get("holdSide") or ("short" if decision.side == "buy" else "long"))
        response = client.close_position_order(payload)
        failures = ((response.get("data") or {}).get("failureList") or []) if isinstance(response, dict) else []
        if failures:
            raise RuntimeError(f"反向仓位平仓失败: {json.dumps(failures, ensure_ascii=False)}")
        result = {"request": payload, "response": response, "position": position}
        self.logger.info("开仓前已提交反向仓位平仓: %s", json.dumps(result, ensure_ascii=False))
        self._wait_until_opposite_position_closed(client, decision)
        return result

    def _close_opposite_position_if_needed(
        self,
        client: BitgetFuturesTradeClient,
        decision: TradeDecision,
    ) -> dict[str, Any] | None:
        reverse_position = self._opposite_side_position(client, decision)
        if reverse_position is None:
            return None
        return self._close_opposite_position(client, decision, reverse_position)

    def _wait_until_opposite_position_closed(
        self,
        client: BitgetFuturesTradeClient,
        decision: TradeDecision,
    ) -> None:
        for attempt in range(1, 4):
            remaining = self._opposite_side_position(client, decision)
            if remaining is None:
                return
            if attempt < 3:
                time.sleep(1)
        raise RuntimeError(f"反向仓位平仓后仍检测到持仓，拒绝继续开仓: {remaining}")

    def _entry_price(self, client: BitgetFuturesTradeClient, decision: TradeDecision) -> Decimal:
        if self.config.entry_price_source == "bar_close" and decision.bar_close is not None:
            return _to_decimal(decision.bar_close)
        ticker = client.get_ticker(symbol=decision.symbol, product_type=self.config.product_type)
        field_by_source = {
            "mark_price": "markPrice",
            "market": "lastPr",
            "last": "lastPr",
            "index_price": "indexPrice",
        }
        field = field_by_source.get(self.config.entry_price_source, "markPrice")
        raw_value = ticker.get(field)
        if raw_value in (None, ""):
            raise RuntimeError(f"ticker 中缺少 {field}，拒绝开仓以避免无法记录开仓价格。")
        return _to_decimal(raw_value)

    def _entry_time_allowed(self, now: datetime | None = None) -> tuple[bool, str]:
        if not self.config.entry_time_filter_enabled:
            return True, "开仓时间过滤未启用。"
        current = now or datetime.now(DISPLAY_TIMEZONE)
        current_minutes = current.hour * 60 + current.minute
        start_minutes = _parse_time_of_day_minutes(self.config.entry_time_start)
        end_minutes = _parse_time_of_day_minutes(self.config.entry_time_end)

        if start_minutes == end_minutes:
            allowed = True
        elif start_minutes < end_minutes:
            allowed = start_minutes <= current_minutes < end_minutes
        else:
            allowed = current_minutes >= start_minutes or current_minutes < end_minutes

        current_label = current.strftime("%Y-%m-%d %H:%M:%S %Z")
        window_label = f"{self.config.entry_time_start}-{self.config.entry_time_end}"
        if allowed:
            return True, f"当前北京时间 {current_label} 在允许开仓时段 {window_label} 内。"
        return False, f"当前北京时间 {current_label} 不在允许开仓时段 {window_label} 内，禁止新开仓。"

    def _dry_run_entry_price(self, decision: TradeDecision) -> Decimal:
        if self.config.entry_price_source == "bar_close" and decision.bar_close is not None:
            return _to_decimal(decision.bar_close)
        if decision.bar_close is None:
            raise RuntimeError("缺少 bar_close，无法预估 dry-run 开仓价格。")
        return _to_decimal(decision.bar_close)

    def _order_size_from_margin(self, entry_price: Decimal) -> Decimal:
        margin_amount = _to_decimal(self.config.margin_amount)
        leverage = _to_decimal(self.config.leverage)
        if margin_amount <= 0:
            raise RuntimeError("LIVE_TRADING_MARGIN_AMOUNT 必须大于 0。")
        if leverage != Decimal("10"):
            raise RuntimeError("当前实盘固定要求 LIVE_TRADING_LEVERAGE=10。")
        if entry_price <= 0:
            raise RuntimeError("开仓价格必须大于 0，无法计算下单数量。")
        raw_size = margin_amount * leverage / entry_price
        size = _quantize_decimal(raw_size, self.config.size_decimals)
        if size <= 0:
            raise RuntimeError(f"按 5U/10x 计算出的下单数量过小: raw={raw_size}")
        return size

    def _required_futures_available(self) -> Decimal:
        margin_amount = _to_decimal(self.config.margin_amount)
        multiplier = _to_decimal(self.config.auto_transfer_multiplier)
        buffer_amount = _to_decimal(self.config.auto_transfer_buffer)
        if multiplier < Decimal("1"):
            raise RuntimeError("LIVE_TRADING_AUTO_TRANSFER_MULTIPLIER 不能小于 1。")
        return margin_amount * multiplier + max(buffer_amount, Decimal("0"))

    def _futures_available(self, client: BitgetFuturesTradeClient) -> Decimal:
        payload = client.get_futures_accounts(product_type=self.config.product_type)
        accounts = payload.get("data") or []
        for account in accounts:
            if not isinstance(account, dict):
                continue
            margin_coin = str(account.get("marginCoin") or "").upper()
            if margin_coin and margin_coin != self.config.margin_coin:
                continue
            for key in ("isolatedMaxAvailable", "available", "availableBalance", "fixedMaxAvailable", "crossedMaxAvailable"):
                value = account.get(key)
                if value not in (None, ""):
                    return _to_decimal(value)
        return Decimal("0")

    def _spot_available(self, client: BitgetFuturesTradeClient) -> Decimal:
        payload = client.get_spot_assets(coin=self.config.margin_coin)
        assets = payload.get("data") or []
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            coin = str(asset.get("coinName") or asset.get("coin") or "").upper()
            if coin and coin != self.config.margin_coin:
                continue
            value = asset.get("available")
            if value not in (None, ""):
                return _to_decimal(value)
        return Decimal("0")

    def _ensure_futures_margin_available(self, client: BitgetFuturesTradeClient) -> dict[str, Any]:
        margin_amount = _to_decimal(self.config.margin_amount)
        required_available = self._required_futures_available()
        futures_available = self._futures_available(client)
        if futures_available >= required_available:
            return {
                "transferred": False,
                "futures_available": _decimal_to_string(futures_available),
                "required_margin": _decimal_to_string(margin_amount),
                "required_with_buffer": _decimal_to_string(required_available),
                "auto_transfer_multiplier": self.config.auto_transfer_multiplier,
                "auto_transfer_buffer": self.config.auto_transfer_buffer,
            }
        if not self.config.auto_transfer_enabled:
            raise RuntimeError("合约账户 USDT 不足目标预留保证金，且 LIVE_TRADING_AUTO_TRANSFER_FROM_SPOT=false。")

        transfer_amount = max(required_available - futures_available, Decimal("0"))
        spot_available = self._spot_available(client)
        if transfer_amount <= 0:
            transfer_amount = margin_amount - futures_available
        if spot_available < transfer_amount:
            raise RuntimeError(
                "合约账户 USDT 不足，且现货账户余额不足以自动划转："
                f"need={_decimal_to_string(transfer_amount)} spot={_decimal_to_string(spot_available)}"
            )
        response = client.transfer_between_accounts(
            from_type="spot",
            to_type="usdt_futures",
            amount=_decimal_to_string(transfer_amount),
            coin=self.config.margin_coin,
            client_oid=f"tq-transfer-{int(time.time() * 1000)}",
        )
        refreshed = self._futures_available(client)
        if refreshed < required_available:
            raise RuntimeError(
                "现货划转后合约账户仍不足目标预留保证金："
                f"available={_decimal_to_string(refreshed)} required={_decimal_to_string(required_available)}"
            )
        return {
            "transferred": True,
            "from": "spot",
            "to": "usdt_futures",
            "coin": self.config.margin_coin,
            "amount": _decimal_to_string(transfer_amount),
            "before_futures_available": _decimal_to_string(futures_available),
            "after_futures_available": _decimal_to_string(refreshed),
            "required_margin": _decimal_to_string(margin_amount),
            "required_with_buffer": _decimal_to_string(required_available),
            "auto_transfer_multiplier": self.config.auto_transfer_multiplier,
            "auto_transfer_buffer": self.config.auto_transfer_buffer,
            "spot_available_before": _decimal_to_string(spot_available),
            "response": response,
        }

    def _same_side_position(
        self,
        client: BitgetFuturesTradeClient,
        decision: TradeDecision,
    ) -> dict[str, Any] | None:
        payload = client.get_all_positions(product_type=self.config.product_type, margin_coin=self.config.margin_coin)
        positions = payload.get("data") or []
        expected_hold_side = {"buy": "long", "sell": "short"}.get(decision.side or "")
        if not expected_hold_side:
            return None

        for position in positions:
            if not isinstance(position, dict):
                continue
            symbol = str(position.get("symbol") or "").upper()
            if symbol != decision.symbol:
                continue
            hold_side = str(position.get("holdSide") or position.get("posSide") or "").lower()
            if hold_side and hold_side != expected_hold_side:
                continue
            if self.config.position_mode == "one_way_mode" and not hold_side:
                side_from_net = self._one_way_position_side(position)
                if side_from_net != expected_hold_side:
                    continue
            if self._position_size(position) > 0:
                return {
                    "symbol": symbol,
                    "holdSide": hold_side or expected_hold_side,
                    "total": str(position.get("total", "") or ""),
                    "available": str(position.get("available", "") or ""),
                    "locked": str(position.get("locked", "") or ""),
                    "marginSize": str(position.get("marginSize", "") or ""),
                    "unrealizedPL": str(position.get("unrealizedPL", "") or ""),
                }
        return None

    def _same_side_position_if_available(self, decision: TradeDecision) -> dict[str, Any] | None:
        try:
            client = BitgetFuturesTradeClient(self.project_root)
            return self._same_side_position(client, decision)
        except Exception as exc:
            self.logger.warning("观察/邮件模式同向仓位检查失败，继续发送信号邮件: %s", exc)
            return None

    def sync_local_positions_with_exchange(self, *, symbol: str | None = None, force: bool = False) -> dict[str, Any]:
        if not self.config.local_position_enabled or not self.config.position_sync_enabled:
            return {"skipped": True, "reason": "本地仓位或同步开关未启用"}
        if self.config.position_sync_real_only and not force and not self._is_real_trading_mode():
            return {"skipped": True, "reason": "非真实交易模式，保留 only 邮件/观察模式的本地虚拟仓位"}

        try:
            client = BitgetFuturesTradeClient(self.project_root)
            exchange_positions = self._exchange_open_positions(client, symbol=symbol)
        except Exception as exc:
            now = time.monotonic()
            if now - self._last_position_sync_warning_at >= 60:
                self.logger.warning("Bitget 持仓同步失败，本地仓位暂不覆盖: %s", exc)
                self._last_position_sync_warning_at = now
            return {"skipped": True, "reason": str(exc)}

        state = self._read_state()
        positions = [item for item in state.get("local_positions") or [] if isinstance(item, dict)]
        scoped_symbol = symbol.upper() if symbol else None
        risk_exclusions = state.get("risk_excluded_position_keys")
        if not isinstance(risk_exclusions, dict):
            risk_exclusions = {}
        exchange_by_key = {
            self._position_key(position["symbol"], position["side"]): position
            for position in exchange_positions
        }
        now_ms = int(time.time() * 1000)
        changed = False
        closed = 0
        added = 0
        updated = 0
        for key in list(risk_exclusions):
            if key not in exchange_by_key:
                risk_exclusions.pop(key, None)
                changed = True

        for position in positions:
            position_symbol = str(position.get("symbol") or "").upper()
            if scoped_symbol and position_symbol != scoped_symbol:
                continue
            if str(position.get("status") or "open").lower() != "open":
                continue
            key = self._position_key(position_symbol, str(position.get("side") or ""))
            exchange_position = exchange_by_key.get(key)
            if exchange_position is None:
                self._cancel_known_exchange_stop_if_needed(client, position)
                position["status"] = "closed"
                position["closed_at"] = now_ms
                position["close_reason"] = "exchange_sync_no_position"
                position["sync_source"] = "bitget"
                changed = True
                closed += 1
                continue
            position["source"] = "exchange"
            position["holdSide"] = exchange_position["holdSide"]
            if not position.get("managed_size") and position.get("size"):
                position["managed_size"] = position.get("size")
            position["size"] = exchange_position["size"]
            position["exchange_size"] = exchange_position["size"]
            position["available"] = exchange_position.get("available")
            position["unrealizedPL"] = exchange_position.get("unrealizedPL")
            position["marginSize"] = exchange_position.get("marginSize")
            if not position.get("entry_price") and exchange_position.get("entry_price"):
                position["entry_price"] = exchange_position.get("entry_price")
            if not position.get("created_at") and exchange_position.get("opened_at"):
                position["created_at"] = exchange_position.get("opened_at")
            position["exchange_position"] = exchange_position.get("raw")
            position["synced_at"] = now_ms
            changed = True
            updated += 1

        existing_open_keys = {
            self._position_key(str(item.get("symbol") or "").upper(), str(item.get("side") or ""))
            for item in positions
            if str(item.get("status") or "open").lower() == "open"
        }
        for exchange_position in exchange_positions:
            key = self._position_key(exchange_position["symbol"], exchange_position["side"])
            if key in existing_open_keys:
                continue
            positions.append(
                {
                    "status": "open",
                    "source": "exchange",
                    "risk_managed": False,
                    "symbol": exchange_position["symbol"],
                    "side": exchange_position["side"],
                    "holdSide": exchange_position["holdSide"],
                    "size": exchange_position["size"],
                    "exchange_size": exchange_position["size"],
                    "managed_size": exchange_position["size"],
                    "available": exchange_position.get("available"),
                    "unrealizedPL": exchange_position.get("unrealizedPL"),
                    "marginSize": exchange_position.get("marginSize"),
                    "entry_price": exchange_position.get("entry_price"),
                    "risk_max_favorable_points": "0",
                    "risk_max_adverse_points": "0",
                    "risk_protected_stop_points": None,
                    "risk_startup_checked": False,
                    "exchange_position": exchange_position.get("raw"),
                    "created_at": exchange_position.get("opened_at") or now_ms,
                    "synced_at": now_ms,
                    "note": (
                        "由 Bitget 实际持仓同步写入；未知来源仓位默认不自动风控，避免误平手动仓位。"
                        if key not in risk_exclusions
                        else "该方向存在本策略部分平仓后剩余的手动/外部仓位，已排除自动风控直到该方向仓位清空。"
                    ),
                }
            )
            existing_open_keys.add(key)
            changed = True
            added += 1

        if changed:
            state["local_positions"] = positions[-500:]
            state["risk_excluded_position_keys"] = risk_exclusions
            state["last_position_sync"] = {
                "ts": now_ms,
                "symbol": scoped_symbol or "*",
                "source": "bitget",
                "open_count": len(exchange_positions),
                "added": added,
                "updated": updated,
                "closed": closed,
            }
            self._write_state(state)
            self.logger.info("Bitget 持仓已同步到本地账本: %s", json.dumps(state["last_position_sync"], ensure_ascii=False))
        return {"skipped": False, "open_count": len(exchange_positions), "added": added, "updated": updated, "closed": closed}

    def _exchange_open_positions(self, client: BitgetFuturesTradeClient, *, symbol: str | None = None) -> list[dict[str, Any]]:
        payload = client.get_all_positions(product_type=self.config.product_type, margin_coin=self.config.margin_coin)
        positions = payload.get("data") or []
        scoped_symbol = symbol.upper() if symbol else None
        normalized: list[dict[str, Any]] = []
        for position in positions:
            if not isinstance(position, dict):
                continue
            position_symbol = str(position.get("symbol") or "").upper()
            if not position_symbol or (scoped_symbol and position_symbol != scoped_symbol):
                continue
            size = self._position_size(position)
            if size <= 0:
                continue
            hold_side = str(position.get("holdSide") or position.get("posSide") or "").lower()
            if hold_side not in {"long", "short"}:
                hold_side = self._one_way_position_side(position) or ""
            side = {"long": "buy", "short": "sell"}.get(hold_side)
            if side is None:
                continue
            normalized.append(
                {
                    "symbol": position_symbol,
                    "side": side,
                    "holdSide": hold_side,
                    "size": str(size),
                    "total": str(position.get("total", "") or ""),
                    "available": str(position.get("available", "") or ""),
                    "locked": str(position.get("locked", "") or ""),
                    "marginSize": str(position.get("marginSize", "") or ""),
                    "unrealizedPL": str(position.get("unrealizedPL", "") or ""),
                    "entry_price": _exchange_position_entry_price_text(position),
                    "opened_at": _exchange_position_opened_at_ms(position),
                    "raw": position,
                }
            )
        return normalized

    def _is_real_trading_mode(self) -> bool:
        return self.config.enabled and not self.config.dry_run and not self.config.log_only

    @staticmethod
    def _position_key(symbol: str, side: str) -> str:
        return f"{str(symbol or '').upper()}:{str(side or '').lower()}"

    def _local_same_side_position(self, decision: TradeDecision) -> dict[str, Any] | None:
        if not self.config.local_position_enabled or decision.side not in {"buy", "sell"}:
            return None
        state = self._read_state()
        positions = [item for item in state.get("local_positions") or [] if isinstance(item, dict)]
        for position in reversed(positions):
            if str(position.get("status") or "open").lower() != "open":
                continue
            if str(position.get("symbol") or "").upper() != decision.symbol.upper():
                continue
            if str(position.get("side") or "").lower() != decision.side:
                continue
            return {
                "source": "local",
                "symbol": position.get("symbol"),
                "side": position.get("side"),
                "holdSide": position.get("holdSide"),
                "clientOid": position.get("clientOid"),
                "bar_time": position.get("bar_time"),
                "bar_time_label": position.get("bar_time_label"),
                "created_at": position.get("created_at"),
                "note": position.get("note"),
            }
        return None

    @staticmethod
    def _position_size(position: dict[str, Any]) -> float:
        for key in ("total", "available", "locked", "holdVol", "pos", "positionSize"):
            try:
                value = abs(float(position.get(key) or 0))
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
        return 0.0

    @staticmethod
    def _one_way_position_side(position: dict[str, Any]) -> str | None:
        for key in ("total", "available", "locked", "holdVol", "pos", "positionSize"):
            try:
                value = float(position.get(key) or 0)
            except (TypeError, ValueError):
                continue
            if value > 0:
                return "long"
            if value < 0:
                return "short"
        return None

    def _marker_texts_at(self, snapshot: dict[str, Any], bar_time: int) -> list[str]:
        texts: list[str] = []
        for indicator in snapshot.get("indicators") or []:
            for series in indicator.get("series") or []:
                options = series.get("options") or {}
                for marker in (options.get("candleMarkers") or options.get("markers") or []):
                    if int(marker.get("time") or 0) == bar_time:
                        text = str(marker.get("text") or "").strip()
                        if text:
                            texts.append(text)
        return texts

    def _bar_time_label(self, snapshot: dict[str, Any], bar_time: int) -> str:
        label = str((snapshot.get("time_labels") or {}).get(str(bar_time)) or "").strip()
        if label:
            return label
        try:
            return datetime.fromtimestamp(bar_time, tz=DISPLAY_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return str(bar_time)

    def _atr_at(self, snapshot: dict[str, Any], bar_time: int) -> float | None:
        candles = snapshot.get("candles") or []
        period = max(int(self.config.atr_period), 1)
        target_index: int | None = None
        for index, candle in enumerate(candles):
            if int(candle.get("time") or 0) == bar_time:
                target_index = index
                break
        if target_index is None or target_index <= 0 or target_index + 1 < period:
            return None

        true_ranges: list[Decimal] = []
        start_index = target_index - period + 1
        for index in range(start_index, target_index + 1):
            candle = candles[index]
            previous = candles[index - 1]
            try:
                high = _to_decimal(candle.get("high"))
                low = _to_decimal(candle.get("low"))
                prev_close = _to_decimal(previous.get("close"))
            except (InvalidOperation, TypeError, ValueError):
                return None
            true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
        if not true_ranges:
            return None
        return float(sum(true_ranges) / Decimal(len(true_ranges)))

    def _indicator_context_at(self, snapshot: dict[str, Any], bar_time: int) -> tuple[dict[str, float], dict[str, str]]:
        values: dict[str, float] = {}
        colors: dict[str, str] = {}
        for indicator in snapshot.get("indicators") or []:
            indicator_id = str(indicator.get("id") or "indicator")
            for series in indicator.get("series") or []:
                series_id = str(series.get("id") or "series")
                for point in series.get("data") or []:
                    if int(point.get("time") or 0) != bar_time:
                        continue
                    value = point.get("value")
                    if isinstance(value, (int, float)):
                        values[f"{indicator_id}.{series_id}"] = float(value)
                    color = str(point.get("color") or "").strip()
                    if color:
                        colors[f"{indicator_id}.{series_id}"] = color
                    break
        return values, colors

    def _side_from_strategy(
        self,
        marker_texts: list[str],
        indicator_values: dict[str, float],
        indicator_colors: dict[str, str],
        *,
        bar_high: float | None = None,
        bar_low: float | None = None,
    ) -> tuple[str | None, str]:
        if self.config.strategy == "stc_extreme_contrarian":
            return self._stc_extreme_contrarian_side(marker_texts, indicator_values, indicator_colors, bar_high=bar_high, bar_low=bar_low)
        side = self._side_from_marker_texts(marker_texts)
        if side is None:
            return None, f"目标 K 线没有满足 {self.config.signal_mode} 模式的交易信号"
        hull_ok, hull_reason = self._hull_position_allows_side(side, indicator_values, bar_high=bar_high, bar_low=bar_low)
        if not hull_ok:
            return None, hull_reason
        return side, f"检测到 {','.join(marker_texts)} 信号"

    def _stc_extreme_contrarian_side(
        self,
        marker_texts: list[str],
        indicator_values: dict[str, float],
        indicator_colors: dict[str, str],
        *,
        bar_high: float | None = None,
        bar_low: float | None = None,
    ) -> tuple[str | None, str]:
        texts = set(marker_texts)
        stc_value = indicator_values.get("stc.stc")
        stc_color = indicator_colors.get("stc.stc", "")
        stc_is_red = _is_red_color(stc_color)
        stc_is_green = _is_green_color(stc_color)
        has_any_buy = "Buy" in texts or "买" in texts
        has_any_sell = "Sell" in texts or "卖" in texts

        if has_any_sell and stc_value is not None and stc_value > 75 and stc_is_red:
            hull_ok, hull_reason = self._hull_position_allows_side("sell", indicator_values, bar_high=bar_high, bar_low=bar_low)
            if not hull_ok:
                return None, hull_reason
            return "sell", f"空单观察信号：Sell 或 卖 出现，且 STC={stc_value:.2f}>75 并为红色；{hull_reason}"
        if has_any_buy and stc_value is not None and stc_value < 25 and stc_is_green:
            hull_ok, hull_reason = self._hull_position_allows_side("buy", indicator_values, bar_high=bar_high, bar_low=bar_low)
            if not hull_ok:
                return None, hull_reason
            return "buy", f"多单观察信号：Buy 或 买 出现，且 STC={stc_value:.2f}<25 并为绿色；{hull_reason}"

        return (
            None,
            "未满足观察策略：空单需 Sell/卖 任一信号且 STC>75 红色；多单需 Buy/买 任一信号且 STC<25 绿色。"
            f" 当前 signals={','.join(marker_texts) or '-'}, STC={stc_value}, color={stc_color or '-'}",
        )

    def _hull_position_allows_side(
        self,
        side: str,
        indicator_values: dict[str, float],
        *,
        bar_high: float | None,
        bar_low: float | None,
    ) -> tuple[bool, str]:
        if bar_high is None or bar_low is None:
            return False, "缺少开仓 K 线 high/low，无法判断 Hull 与 K 线位置，禁止开仓。"

        high = float(bar_high)
        low = float(bar_low)
        if side == "buy":
            hull_values = self._hull_band_values(indicator_values, "buy")
            if not hull_values:
                return False, "缺少红带 Hull 指标值，无法判断多单位置，禁止开仓。"
            if all(value < low for value in hull_values):
                return True, f"红带在 K 线下方，允许多单：hull={_format_float_list(hull_values)}, low={_format_price(low)}"
            return (
                False,
                f"红带未完全位于开仓 K 线下方，禁止多单：要求红带上下边界都 < low；"
                f"hull={_format_float_list(hull_values)}, high={_format_price(high)}, low={_format_price(low)}",
            )
        if side == "sell":
            hull_values = self._hull_band_values(indicator_values, "sell")
            if not hull_values:
                return False, "缺少绿带 Hull 指标值，无法判断空单位置，禁止开仓。"
            if all(value > high for value in hull_values):
                return True, f"绿带在 K 线上方，允许空单：hull={_format_float_list(hull_values)}, high={_format_price(high)}"
            return (
                False,
                f"绿带未完全位于开仓 K 线上方，禁止空单：要求绿带上下边界都 > high；"
                f"hull={_format_float_list(hull_values)}, high={_format_price(high)}, low={_format_price(low)}",
            )
        return False, f"未知开仓方向，无法判断 Hull 位置: {side}"

    @staticmethod
    def _hull_band_values(indicator_values: dict[str, float], side: str) -> list[float]:
        keys = (
            ("merged_dkx_hull_ut.mhull_up", "merged_dkx_hull_ut.shull_up")
            if side == "buy"
            else ("merged_dkx_hull_ut.mhull_down", "merged_dkx_hull_ut.shull_down")
        )
        values: list[float] = []
        for key in keys:
            value = indicator_values.get(key)
            if value is not None:
                values.append(float(value))
        return values

    def _higher_timeframe_hull_allows_side(
        self,
        side: str,
        htf_snapshot: Any,
        *,
        symbol: str,
    ) -> tuple[bool, str, dict[str, Any]]:
        configured_label = _duration_label(self.config.htf_hull_duration_seconds)
        if not self.config.htf_hull_filter_enabled:
            return True, f"{configured_label} Hull 趋势过滤未启用。", {}
        if not isinstance(htf_snapshot, dict):
            return False, f"缺少高周期 Hull 快照，无法确认 {configured_label} 趋势，禁止开仓。", {}

        trend, detail = self._higher_timeframe_hull_trend(htf_snapshot)
        duration = htf_snapshot.get("duration_seconds") or self.config.htf_hull_duration_seconds
        duration_label = _duration_label(int(duration))
        label = detail.get("bar_time_label") or detail.get("bar_time") or "-"
        lock_context = {
            "symbol": symbol.upper(),
            "side": side,
            "duration_seconds": int(duration),
            "bar_time": detail.get("bar_time"),
            "bar_time_label": detail.get("bar_time_label"),
            "trend_start_time": detail.get("trend_start_time"),
            "trend_start_time_label": detail.get("trend_start_time_label"),
            "trend": trend,
        }
        if detail.get("trend_start_time") is not None:
            lock_context["lock_key"] = self._htf_entry_lock_key(symbol, side, int(duration), int(detail["trend_start_time"]))
        if trend == "buy":
            if side == "sell":
                return False, f"{duration_label} Hull 为红色多趋势，禁止 5m 反向开空；{duration_label}={label}", lock_context
            return True, f"{duration_label} Hull 为红色多趋势，允许顺势开多；{duration_label}={label}", lock_context
        if trend == "sell":
            if side == "buy":
                return False, f"{duration_label} Hull 为绿色空趋势，禁止 5m 反向开多；{duration_label}={label}", lock_context
            return True, f"{duration_label} Hull 为绿色空趋势，允许顺势开空；{duration_label}={label}", lock_context
        return False, f"高周期 Hull 趋势不明确，禁止开仓：{detail.get('reason') or detail}", lock_context

    def _higher_timeframe_hull_trend(self, htf_snapshot: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
        candles = htf_snapshot.get("candles") or []
        if not candles:
            return None, {"reason": "高周期快照没有 K 线"}
        target_index = -2 if self.config.use_closed_bar and len(candles) >= 2 else -1
        actual_index = len(candles) + target_index if target_index < 0 else target_index
        target_candle = candles[target_index]
        bar_time = int(target_candle.get("time") or 0)
        indicator_values, indicator_colors = self._indicator_context_at(htf_snapshot, bar_time)
        red_band = self._hull_band_values(indicator_values, "buy")
        green_band = self._hull_band_values(indicator_values, "sell")
        stc_color = indicator_colors.get("stc.stc", "")
        stc_trend = self._stc_trend_from_color(stc_color)
        detail = {
            "bar_time": bar_time,
            "bar_time_label": self._bar_time_label(htf_snapshot, bar_time),
            "red_band": red_band,
            "green_band": green_band,
            "stc_color": stc_color,
            "stc_trend": stc_trend,
        }
        if red_band and not green_band:
            self._attach_hull_trend_start(htf_snapshot, candles, actual_index, "buy", detail)
            if stc_trend != "buy":
                duration_label = _duration_label(int(htf_snapshot.get("duration_seconds") or self.config.htf_hull_duration_seconds))
                detail["reason"] = f"{duration_label} Hull 为红色上升趋势，但 {duration_label} STC 不是上升色"
                return None, detail
            return "buy", detail
        if green_band and not red_band:
            self._attach_hull_trend_start(htf_snapshot, candles, actual_index, "sell", detail)
            if stc_trend != "sell":
                duration_label = _duration_label(int(htf_snapshot.get("duration_seconds") or self.config.htf_hull_duration_seconds))
                detail["reason"] = f"{duration_label} Hull 为绿色下降趋势，但 {duration_label} STC 不是下降色"
                return None, detail
            return "sell", detail
        detail["reason"] = "红带/绿带状态为空或同时存在"
        return None, detail

    def _attach_hull_trend_start(
        self,
        htf_snapshot: dict[str, Any],
        candles: list[dict[str, Any]],
        target_index: int,
        trend: str,
        detail: dict[str, Any],
    ) -> None:
        start_index = max(min(target_index, len(candles) - 1), 0)
        for index in range(start_index - 1, -1, -1):
            candle = candles[index]
            candle_time = int(candle.get("time") or 0)
            if self._hull_trend_at(htf_snapshot, candle_time) != trend:
                break
            start_index = index
        start_time = int(candles[start_index].get("time") or 0)
        detail["trend_start_time"] = start_time
        detail["trend_start_time_label"] = self._bar_time_label(htf_snapshot, start_time)

    def _hull_trend_at(self, snapshot: dict[str, Any], bar_time: int) -> str | None:
        indicator_values, _indicator_colors = self._indicator_context_at(snapshot, bar_time)
        red_band = self._hull_band_values(indicator_values, "buy")
        green_band = self._hull_band_values(indicator_values, "sell")
        if red_band and not green_band:
            return "buy"
        if green_band and not red_band:
            return "sell"
        return None

    @staticmethod
    def _stc_trend_from_color(color: str) -> str | None:
        if _is_green_color(color):
            return "buy"
        if _is_red_color(color):
            return "sell"
        return None

    def _side_from_marker_texts(self, marker_texts: list[str]) -> str | None:
        texts = set(marker_texts)
        has_buy = bool(texts & SIGNAL_TEXT_BY_SIDE["buy"])
        has_sell = bool(texts & SIGNAL_TEXT_BY_SIDE["sell"])
        mode = self.config.signal_mode
        if mode == "ut":
            has_buy = "Buy" in texts
            has_sell = "Sell" in texts
        elif mode == "dkx":
            has_buy = "买" in texts
            has_sell = "卖" in texts
        elif mode == "confirmed":
            has_buy = "Buy" in texts and "买" in texts
            has_sell = "Sell" in texts and "卖" in texts
        if has_buy == has_sell:
            return None
        return "buy" if has_buy else "sell"

    def _order_request(self, decision: TradeDecision, entry_price: Decimal | None = None) -> dict[str, Any]:
        price = entry_price or self._dry_run_entry_price(decision)
        size = self._order_size_from_margin(price)
        request = {
            "symbol": decision.symbol,
            "productType": self.config.product_type,
            "marginMode": "isolated",
            "marginCoin": self.config.margin_coin,
            "size": _decimal_to_string(size),
            "side": decision.side,
            "orderType": "market",
            "clientOid": decision.client_oid,
        }
        if self.config.position_mode == "hedge_mode":
            request["tradeSide"] = "open"
        return request

    def _place_exchange_disaster_stop(
        self,
        client: BitgetFuturesTradeClient,
        decision: TradeDecision,
        entry_price: Decimal,
    ) -> dict[str, Any]:
        if decision.side not in {"buy", "sell"}:
            raise RuntimeError(f"无法为未知方向设置交易所灾难止损: {decision.side}")
        disaster_points = abs(_to_decimal(self.config.risk_disaster_stop_points))
        trigger_price = entry_price - disaster_points if decision.side == "buy" else entry_price + disaster_points
        trigger_price = _quantize_decimal(trigger_price, self.config.price_decimals)
        if trigger_price <= 0:
            raise RuntimeError(f"交易所灾难止损价格无效: {trigger_price}")
        hold_side = "long" if decision.side == "buy" else "short"
        stop_size = self._order_size_from_margin(entry_price)
        payload = {
            "marginCoin": self.config.margin_coin,
            "productType": self.config.product_type,
            "symbol": decision.symbol,
            "planType": "loss_plan",
            "triggerPrice": _decimal_to_string(trigger_price),
            "triggerType": "mark_price",
            "holdSide": hold_side if self.config.position_mode == "hedge_mode" else decision.side,
            "size": _decimal_to_string(stop_size),
            "clientOid": f"tq-sl-{decision.symbol.lower()}-{decision.side}-{decision.bar_time or int(time.time())}"[:64],
        }
        response = client.place_tpsl_order(payload)
        self.logger.warning("已设置交易所服务器端灾难止损: %s", json.dumps({"request": payload, "response": response}, ensure_ascii=False))
        return {
            "request": payload,
            "response": response,
            "orderRef": _tpsl_order_ref(response),
            "triggerPrice": payload["triggerPrice"],
        }

    def _client_oid(self, symbol: str, side: str, bar_time: int | None) -> str:
        return f"tq-live-{symbol.lower()}-{side}-{bar_time or int(time.time())}"[:64]

    @staticmethod
    def _htf_entry_lock_key(symbol: str, side: str, duration_seconds: int, htf_bar_time: int) -> str:
        return f"{str(symbol or '').upper()}:{str(side or '').lower()}:htf:{int(duration_seconds)}:{int(htf_bar_time)}"

    def _wait_until_same_side_position_open(
        self,
        client: BitgetFuturesTradeClient,
        decision: TradeDecision,
    ) -> None:
        for attempt in range(1, 5):
            if self._same_side_position(client, decision) is not None:
                return
            if attempt < 4:
                time.sleep(1)
        raise RuntimeError(f"开仓下单后仍未查询到同向持仓，拒绝继续设置服务器端止损: {decision.symbol} {decision.side}")

    def _already_executed(self, client_oid: str) -> bool:
        state = self._read_state()
        return client_oid in set(state.get("client_oids") or [])

    def _htf_entry_lock(self, decision: TradeDecision) -> dict[str, Any] | None:
        if not decision.htf_lock_key:
            return None
        state = self._read_state()
        locks = state.get("htf_entry_locks")
        if not isinstance(locks, dict):
            return None
        entry = locks.get(decision.htf_lock_key)
        return entry if isinstance(entry, dict) else None

    def _record_htf_entry_lock_if_needed(self, result: TradeExecutionResult) -> None:
        decision = result.decision
        if decision.action != "place_order" or decision.side not in {"buy", "sell"}:
            return
        if result.error or result.already_executed or not decision.htf_lock_key:
            return
        response = result.response if isinstance(result.response, dict) else {}
        if not response.get("order"):
            return
        state = self._read_state()
        locks = state.get("htf_entry_locks")
        if not isinstance(locks, dict):
            locks = {}
        locks[decision.htf_lock_key] = {
            "key": decision.htf_lock_key,
            "symbol": decision.symbol,
            "side": decision.side,
            "clientOid": decision.client_oid,
            "created_at": int(time.time() * 1000),
            "bar_time": decision.bar_time,
            "bar_time_label": decision.bar_time_label,
            "htf_context": decision.htf_context,
            "note": "同一个高周期过滤阶段内，同方向只允许开一次仓；平仓后本锁仍保留到高周期 key 变化。",
        }
        state["htf_entry_locks"] = dict(list(locks.items())[-1000:])
        self._write_state(state)

    def _record_execution(self, result: TradeExecutionResult) -> None:
        self.config.order_log_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "ts": int(time.time() * 1000),
            "decision": asdict(result.decision),
            "dry_run": result.dry_run,
            "enabled": result.enabled,
            "request": result.request,
            "response": result.response,
            "error": result.error,
            "already_executed": result.already_executed,
        }
        with self.config.order_log_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(payload, ensure_ascii=False) + "\n")

        if self._should_update_execution_state(result):
            state = self._read_state()
            client_oids = list(dict.fromkeys([*(state.get("client_oids") or []), result.decision.client_oid]))[-500:]
            state["client_oids"] = client_oids
            self._write_state(state)
        self._record_local_position_if_needed(result)
        self._record_htf_entry_lock_if_needed(result)

    @staticmethod
    def _should_update_execution_state(result: TradeExecutionResult) -> bool:
        if not result.decision.client_oid:
            return False
        if result.error:
            return False
        if result.dry_run or not result.enabled:
            return False
        response = result.response if isinstance(result.response, dict) else {}
        return bool(response.get("order"))

    def _record_local_position_if_needed(self, result: TradeExecutionResult) -> None:
        if not self.config.local_position_enabled:
            return
        decision = result.decision
        if decision.action != "place_order" or decision.side not in {"buy", "sell"} or not decision.client_oid:
            return
        if result.error or result.already_executed:
            return
        response = result.response if isinstance(result.response, dict) else {}
        if response.get("sameSidePosition") or response.get("entryTimeBlocked") or response.get("preflight"):
            return
        is_real_position = bool(response.get("order"))
        is_observation_position = bool(response.get("logOnly") or response.get("dryRun"))
        if is_observation_position and not self.config.local_position_record_observation:
            return
        if not is_real_position and not is_observation_position:
            return

        state = self._read_state()
        positions = [item for item in state.get("local_positions") or [] if isinstance(item, dict)]
        existing = {str(item.get("clientOid") or "") for item in positions}
        if decision.client_oid in existing:
            return
        source = "live" if is_real_position and not result.dry_run and result.enabled else ("log_only" if response.get("logOnly") else "dry_run")
        exchange_stop = response.get("exchangeDisasterStop") if isinstance(response.get("exchangeDisasterStop"), dict) else {}
        exchange_stop_ref = exchange_stop.get("orderRef") if isinstance(exchange_stop.get("orderRef"), dict) else {}
        positions.append(
            {
                "status": "open",
                "source": source,
                "risk_managed": is_real_position,
                "symbol": decision.symbol,
                "side": decision.side,
                "holdSide": "long" if decision.side == "buy" else "short",
                "clientOid": decision.client_oid,
                "size": (result.request or {}).get("size") if isinstance(result.request, dict) else None,
                "managed_size": (result.request or {}).get("size") if isinstance(result.request, dict) else None,
                "entry_price": response.get("entryPrice"),
                "risk_max_favorable_points": "0",
                "risk_max_adverse_points": "0",
                "risk_protected_stop_points": None,
                "risk_startup_checked": False,
                "exchange_stop_order_id": exchange_stop_ref.get("orderId"),
                "exchange_stop_client_oid": exchange_stop_ref.get("clientOid"),
                "exchange_stop_trigger_price": exchange_stop.get("triggerPrice"),
                "exchange_stop_kind": "disaster",
                "bar_time": decision.bar_time,
                "bar_time_label": decision.bar_time_label,
                "created_at": int(time.time() * 1000),
                "note": "本地仓位账本记录；如已手动平仓，请手动在 state 文件中将 status 改为 closed。",
            }
        )
        state["local_positions"] = positions[-500:]
        self._write_state(state)

    def _read_state(self) -> dict[str, Any]:
        if not self.config.state_path.exists():
            return {}
        try:
            return json.loads(self.config.state_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _write_state(self, state: dict[str, Any]) -> None:
        self.config.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.config.state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    def _log_result(self, result: TradeExecutionResult) -> None:
        decision = result.decision
        if result.error:
            self.logger.error("实盘执行失败: %s", json.dumps(asdict(result), ensure_ascii=False))
        elif decision.action == "place_order":
            self.logger.info("实盘执行: %s", json.dumps(asdict(result), ensure_ascii=False))
        else:
            self.logger.info("实盘跳过: %s", json.dumps(asdict(result), ensure_ascii=False))

    def _send_email(self, result: TradeExecutionResult) -> None:
        if not self.config.email_enabled or not self.config.email_to:
            return
        decision = result.decision
        response = result.response or {}
        if result.error:
            status = "失败"
        elif decision.action == "risk_close":
            status = "已风控平仓"
        elif response.get("htfEntryLocked"):
            status = "高周期锁跳过"
        else:
            status = "已持仓跳过" if response.get("sameSidePosition") else ("DRY-RUN" if result.dry_run else "已下单")
        action_label = self._email_action_label(result)
        side_label = {"buy": "多单", "sell": "空单"}.get(decision.side or "", decision.side or "-")
        price_label = f"{decision.bar_close:.2f}" if decision.bar_close is not None else "-"
        htf_context_label = self._email_htf_context_label(result)
        subject = f"[TQ Live] {status} {action_label} {decision.symbol} {price_label} {decision.bar_time_label or decision.bar_time}"
        html = (
            "<h3>TQ Live Trading</h3>"
            f"<p><b>Status:</b> {status}</p>"
            f"<p><b>Symbol:</b> {decision.symbol}</p>"
            f"<p><b>Action:</b> {action_label}</p>"
            f"<p><b>Side:</b> {side_label}</p>"
            f"<p><b>Time:</b> {decision.bar_time_label or decision.bar_time}</p>"
            f"<p><b>Close:</b> {price_label}</p>"
            f"<p><b>OHLC:</b> O={_format_price(decision.bar_open)} H={_format_price(decision.bar_high)} L={_format_price(decision.bar_low)} C={_format_price(decision.bar_close)}</p>"
            f"<p><b>Signals:</b> {', '.join(decision.marker_texts) or '-'}</p>"
            f"<p><b>Reason:</b> {decision.reason}</p>"
            f"<p><b>HTF Lock:</b> {htf_context_label}</p>"
            f"<p><b>Error:</b> {result.error or '-'}</p>"
            f"<pre>{json.dumps(decision.indicator_values, ensure_ascii=False, indent=2)}</pre>"
            f"<pre>{json.dumps(decision.indicator_colors, ensure_ascii=False, indent=2)}</pre>"
            f"<pre>{json.dumps({'request': result.request, 'response': result.response}, ensure_ascii=False, indent=2)}</pre>"
        )
        try:
            send_resend_email(to=self.config.email_to, subject=subject, html=html, project_root=self.project_root)
        except Exception as exc:
            self.logger.warning("邮件发送失败: %s", exc)

    def _email_action_label(self, result: TradeExecutionResult) -> str:
        side_label = {"buy": "开多", "sell": "开空"}.get(result.decision.side or "", result.decision.side or "-")
        response = result.response or {}
        if result.decision.action == "risk_close":
            return {"buy": "风控平多", "sell": "风控平空"}.get(result.decision.side or "", "风控平仓")
        if response.get("htfEntryLocked"):
            duration_label = _duration_label(self.config.htf_hull_duration_seconds)
            return {
                "buy": f"{duration_label} Hull同色周期内已开过多单，跳过",
                "sell": f"{duration_label} Hull同色周期内已开过空单，跳过",
            }.get(result.decision.side or "", "高周期锁跳过")
        if response.get("sameSidePosition"):
            return {"buy": "已有多单，跳过", "sell": "已有空单，跳过"}.get(result.decision.side or "", "已有仓位，跳过")
        if self.config.log_only or response.get("logOnly"):
            return {"buy": "观察多单", "sell": "观察空单"}.get(result.decision.side or "", f"观察 {side_label}")
        if result.dry_run or response.get("dryRun"):
            return f"模拟{side_label}"
        return f"真实{side_label}"

    def _email_htf_context_label(self, result: TradeExecutionResult) -> str:
        context = result.decision.htf_context
        response = result.response if isinstance(result.response, dict) else {}
        if response.get("htfContext"):
            context = response.get("htfContext") or context
        if not context:
            return "-"
        duration = _duration_label(int(context.get("duration_seconds") or self.config.htf_hull_duration_seconds))
        trend_label = {"buy": "红色多趋势", "sell": "绿色空趋势"}.get(str(context.get("trend") or ""), str(context.get("trend") or "-"))
        trend_start = context.get("trend_start_time_label") or context.get("trend_start_time") or "-"
        bar_label = context.get("bar_time_label") or context.get("bar_time") or "-"
        return f"{duration} {trend_label}，颜色周期起点={trend_start}，当前高周期K={bar_label}"

def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip()
    if raw == "":
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _env_live_trading_mode() -> str:
    raw = os.getenv("LIVE_TRADING_MODE", "").strip().lower().replace("-", "_")
    if not raw:
        return ""
    aliases = {
        "none": "off",
        "disabled": "off",
        "disable": "off",
        "observe": "email",
        "observation": "email",
        "mail": "email",
        "email_only": "email",
        "log": "email",
        "log_only": "email",
        "dryrun": "dry_run",
        "paper": "dry_run",
        "real": "live",
        "trade": "live",
        "trading": "live",
    }
    mode = aliases.get(raw, raw)
    if mode not in {"off", "email", "dry_run", "live"}:
        raise ValueError("LIVE_TRADING_MODE 只支持 off / email / dry_run / live")
    return mode


def _mode_flags(mode: str) -> tuple[bool, bool, bool]:
    if mode == "live":
        return True, False, False
    if mode == "dry_run":
        return False, True, False
    if mode in {"email", "off"}:
        return False, True, True
    raise ValueError(f"未知 LIVE_TRADING_MODE: {mode}")


def _mode_from_flags(*, enabled: bool, dry_run: bool, log_only: bool) -> str:
    if enabled and not dry_run and not log_only:
        return "live"
    if log_only:
        return "email"
    if dry_run or not enabled:
        return "dry_run"
    return "email"


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _parse_time_of_day_minutes(value: str) -> int:
    text = str(value or "").strip()
    if text == "24:00":
        return 24 * 60
    try:
        hour_text, minute_text = text.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
    except (TypeError, ValueError):
        raise ValueError(f"时间格式无效: {value}，请使用 HH:MM，例如 20:00 或 24:00。")
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"时间范围无效: {value}，请使用 00:00 到 24:00。")
    return hour * 60 + minute


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def _exchange_position_entry_price_text(position: dict[str, Any]) -> str | None:
    for key in (
        "openPriceAvg",
        "averageOpenPrice",
        "avgOpenPrice",
        "openPrice",
        "breakEvenPrice",
        "holdAvgPrice",
        "avgPrice",
    ):
        value = position.get(key)
        if value not in (None, ""):
            try:
                return _decimal_to_string(_to_decimal(value))
            except (InvalidOperation, TypeError, ValueError):
                continue
    return None


def _exchange_position_opened_at_ms(position: dict[str, Any]) -> int | None:
    for key in ("cTime", "ctime", "openTime", "createdTime", "created_at"):
        value = _optional_int(position.get(key))
        if value is not None and value > 0:
            return value if value > 10_000_000_000 else value * 1000
    return None


def _position_opened_at_ms(position: dict[str, Any]) -> int | None:
    value = position.get("created_at")
    if value not in (None, ""):
        try:
            return int(value)
        except (TypeError, ValueError):
            pass
    bar_time = position.get("bar_time")
    if bar_time not in (None, ""):
        try:
            return int(bar_time) * 1000
        except (TypeError, ValueError):
            pass
    return None


def _closed_5m_bars_since_entry(position: dict[str, Any], now_ms: int) -> int | None:
    bar_time = position.get("bar_time")
    if bar_time not in (None, ""):
        try:
            entry_bar = int(bar_time)
        except (TypeError, ValueError):
            entry_bar = 0
        if entry_bar > 0:
            current_seconds = now_ms // 1000
            current_closed_bar = (current_seconds // 300) * 300 - 300
            return max((current_closed_bar - entry_bar) // 300 + 1, 0)
    opened_at = _position_opened_at_ms(position)
    if opened_at is None:
        return None
    return max((now_ms - opened_at) // (300 * 1000), 0)


def _ticker_price_for_entry_source(ticker: dict[str, Any], entry_price_source: str) -> Decimal:
    field_by_source = {
        "mark_price": "markPrice",
        "market": "lastPr",
        "last": "lastPr",
        "index_price": "indexPrice",
    }
    field = field_by_source.get(entry_price_source, "markPrice")
    raw_value = ticker.get(field)
    if raw_value in (None, ""):
        raw_value = ticker.get("markPrice") or ticker.get("lastPr")
    return _to_decimal(raw_value)


def _format_price(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def _format_float_list(values: list[float]) -> str:
    return "[" + ", ".join(_format_price(value) for value in values) + "]"


def _duration_label(duration_seconds: int) -> str:
    seconds = max(int(duration_seconds), 1)
    if seconds % 86400 == 0:
        days = seconds // 86400
        return f"{days}D" if days != 1 else "1D"
    if seconds % 3600 == 0:
        hours = seconds // 3600
        return f"{hours}h"
    if seconds % 60 == 0:
        minutes = seconds // 60
        return f"{minutes}m"
    return f"{seconds}s"


def _append_reverse_close_response(existing: dict[str, Any] | None, new_response: dict[str, Any]) -> dict[str, Any]:
    if existing is None:
        return new_response
    if "steps" in existing:
        steps = list(existing.get("steps") or [])
    else:
        steps = [existing]
    steps.append(new_response)
    return {"steps": steps}


def _tpsl_order_ref(response: dict[str, Any]) -> dict[str, str]:
    data = response.get("data") if isinstance(response, dict) else None
    if isinstance(data, list):
        first = next((item for item in data if isinstance(item, dict)), {})
    elif isinstance(data, dict):
        first = data
    else:
        first = {}
    return {
        "orderId": str(first.get("orderId") or "").strip(),
        "clientOid": str(first.get("stopLossClientOid") or first.get("clientOid") or "").strip(),
    }


def _to_decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"无法转换为 Decimal: {value}") from exc


def _quantize_decimal(value: Decimal, decimals: int) -> Decimal:
    safe_decimals = max(int(decimals), 0)
    quantizer = Decimal("1") if safe_decimals == 0 else Decimal("1").scaleb(-safe_decimals)
    return value.quantize(quantizer, rounding=ROUND_DOWN)


def _decimal_to_string(value: Decimal) -> str:
    normalized = value.normalize()
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _is_red_color(color: str) -> bool:
    normalized = color.replace(" ", "").lower()
    return "red" in normalized or "#f23645" in normalized or "239,83,80" in normalized or "242,54,69" in normalized


def _is_green_color(color: str) -> bool:
    normalized = color.replace(" ", "").lower()
    return "green" in normalized or "#089981" in normalized or "#4caf50" in normalized or "38,166,154" in normalized


def _build_logger(log_path: Path) -> logging.Logger:
    logger = logging.getLogger("tq_app.live_trading")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return logger
