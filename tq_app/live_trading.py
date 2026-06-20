from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
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
            "<p><b>TP/SL:</b> disabled</p>"
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
        if side is not None:
            htf_ok, htf_reason = self._higher_timeframe_hull_allows_side(side, snapshot.get("higher_timeframe"))
            if not htf_ok:
                side = None
                reason = htf_reason
            else:
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
            self._assert_no_opposite_position(client, decision)
            entry_price = self._entry_price(client, decision)
            fund_response = self._ensure_futures_margin_available(client)
            request = self._order_request(decision, entry_price=entry_price)
            order_response = client.place_order(request)
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
                    "accountSetup": "skipped_at_signal_time",
                },
                error=str(exc),
            )

        self._record_execution(result)
        self._log_result(result)
        self._send_email(result)
        return result

    def check_runtime_state(self) -> None:
        self.sync_local_positions_with_exchange()

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

    def _assert_no_opposite_position(
        self,
        client: BitgetFuturesTradeClient,
        decision: TradeDecision,
    ) -> None:
        remaining = self._opposite_side_position(client, decision)
        if remaining is not None:
            raise RuntimeError(f"检测到反向仓位仍存在，拒绝开新仓以避免真实双向持仓: {remaining}")

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
        exchange_by_key = {
            self._position_key(position["symbol"], position["side"]): position
            for position in exchange_positions
        }
        now_ms = int(time.time() * 1000)
        changed = False
        closed = 0
        added = 0
        updated = 0

        for position in positions:
            position_symbol = str(position.get("symbol") or "").upper()
            if scoped_symbol and position_symbol != scoped_symbol:
                continue
            if str(position.get("status") or "open").lower() != "open":
                continue
            key = self._position_key(position_symbol, str(position.get("side") or ""))
            exchange_position = exchange_by_key.get(key)
            if exchange_position is None:
                position["status"] = "closed"
                position["closed_at"] = now_ms
                position["close_reason"] = "exchange_sync_no_position"
                position["sync_source"] = "bitget"
                changed = True
                closed += 1
                continue
            position["source"] = "exchange"
            position["holdSide"] = exchange_position["holdSide"]
            position["size"] = exchange_position["size"]
            position["available"] = exchange_position.get("available")
            position["unrealizedPL"] = exchange_position.get("unrealizedPL")
            position["marginSize"] = exchange_position.get("marginSize")
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
                    "symbol": exchange_position["symbol"],
                    "side": exchange_position["side"],
                    "holdSide": exchange_position["holdSide"],
                    "size": exchange_position["size"],
                    "available": exchange_position.get("available"),
                    "unrealizedPL": exchange_position.get("unrealizedPL"),
                    "marginSize": exchange_position.get("marginSize"),
                    "exchange_position": exchange_position.get("raw"),
                    "created_at": now_ms,
                    "synced_at": now_ms,
                    "note": "由 Bitget 实际持仓同步写入；真实交易模式下以交易所持仓为准。",
                }
            )
            existing_open_keys.add(key)
            changed = True
            added += 1

        if changed:
            state["local_positions"] = positions[-500:]
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

    def _higher_timeframe_hull_allows_side(self, side: str, htf_snapshot: Any) -> tuple[bool, str]:
        if not self.config.htf_hull_filter_enabled:
            return True, "1h Hull 趋势过滤未启用。"
        if not isinstance(htf_snapshot, dict):
            return False, "缺少高周期 Hull 快照，无法确认 1h 趋势，禁止开仓。"

        trend, detail = self._higher_timeframe_hull_trend(htf_snapshot)
        duration = htf_snapshot.get("duration_seconds") or self.config.htf_hull_duration_seconds
        label = detail.get("bar_time_label") or detail.get("bar_time") or "-"
        if trend == "buy":
            if side == "sell":
                return False, f"{duration}s Hull 为红色多趋势，禁止 5m 反向开空；1h={label}"
            return True, f"{duration}s Hull 为红色多趋势，允许顺势开多；1h={label}"
        if trend == "sell":
            if side == "buy":
                return False, f"{duration}s Hull 为绿色空趋势，禁止 5m 反向开多；1h={label}"
            return True, f"{duration}s Hull 为绿色空趋势，允许顺势开空；1h={label}"
        return False, f"高周期 Hull 趋势不明确，禁止开仓：{detail.get('reason') or detail}"

    def _higher_timeframe_hull_trend(self, htf_snapshot: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
        candles = htf_snapshot.get("candles") or []
        if not candles:
            return None, {"reason": "高周期快照没有 K 线"}
        target_index = -2 if self.config.use_closed_bar and len(candles) >= 2 else -1
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
            if stc_trend != "buy":
                detail["reason"] = "1h Hull 为红色上升趋势，但 1h STC 不是上升色"
                return None, detail
            return "buy", detail
        if green_band and not red_band:
            if stc_trend != "sell":
                detail["reason"] = "1h Hull 为绿色下降趋势，但 1h STC 不是下降色"
                return None, detail
            return "sell", detail
        detail["reason"] = "红带/绿带状态为空或同时存在"
        return None, detail

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

    def _client_oid(self, symbol: str, side: str, bar_time: int | None) -> str:
        return f"tq-live-{symbol.lower()}-{side}-{bar_time or int(time.time())}"[:64]

    def _already_executed(self, client_oid: str) -> bool:
        state = self._read_state()
        return client_oid in set(state.get("client_oids") or [])

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
        positions.append(
            {
                "status": "open",
                "source": source,
                "symbol": decision.symbol,
                "side": decision.side,
                "holdSide": "long" if decision.side == "buy" else "short",
                "clientOid": decision.client_oid,
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
        status = "失败" if result.error else ("已持仓跳过" if response.get("sameSidePosition") else ("DRY-RUN" if result.dry_run else "已下单"))
        action_label = self._email_action_label(result)
        side_label = {"buy": "多单", "sell": "空单"}.get(decision.side or "", decision.side or "-")
        price_label = f"{decision.bar_close:.2f}" if decision.bar_close is not None else "-"
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
        if response.get("sameSidePosition"):
            return {"buy": "已有多单，跳过", "sell": "已有空单，跳过"}.get(result.decision.side or "", "已有仓位，跳过")
        if self.config.log_only or response.get("logOnly"):
            return {"buy": "观察多单", "sell": "观察空单"}.get(result.decision.side or "", f"观察 {side_label}")
        if result.dry_run or response.get("dryRun"):
            return f"模拟{side_label}"
        return f"真实{side_label}"

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
