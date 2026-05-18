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
    enabled: bool = False
    dry_run: bool = True
    log_only: bool = True
    product_type: str = "USDT-FUTURES"
    margin_coin: str = "USDT"
    margin_mode: str = "crossed"
    position_mode: str = "one_way_mode"
    order_type: str = "market"
    force: str = "gtc"
    maker_price_levels: int = 3
    maker_retry_attempts: int = 3
    maker_retry_delay_seconds: float = 0.3
    maker_fallback_to_market: bool = False
    size: str = ""
    leverage: str = ""
    signal_mode: str = "any"
    strategy: str = "stc_extreme_contrarian"
    use_closed_bar: bool = True
    tpsl_enabled: bool = True
    entry_price_source: str = "mark_price"
    tpsl_trigger_type: str = "mark_price"
    atr_period: int = 14
    stop_atr_multiplier: str = "2"
    tp1_r_multiple: str = "1"
    tp1_size_ratio: str = "0.5"
    tp2_r_multiple: str = "1.5"
    price_decimals: int = 2
    size_decimals: int = 6
    tpsl_retry_attempts: int = 3
    tpsl_retry_delay_seconds: float = 1.0
    close_on_tpsl_failure: bool = False
    tpsl_monitor_enabled: bool = True
    tpsl_monitor_interval_seconds: float = 30.0
    email_enabled: bool = True
    email_to: str = ""
    log_path: Path = DEFAULT_LOG_PATH
    order_log_path: Path = DEFAULT_ORDER_LOG_PATH
    state_path: Path = DEFAULT_STATE_PATH

    @classmethod
    def from_env(cls, project_root: Path) -> "LiveTradingConfig":
        load_dotenv(project_root / ".env")
        return cls(
            enabled=_env_bool("LIVE_TRADING_ENABLED", False),
            dry_run=_env_bool("LIVE_TRADING_DRY_RUN", True),
            log_only=_env_bool("LIVE_TRADING_LOG_ONLY", True),
            product_type=os.getenv("LIVE_TRADING_PRODUCT_TYPE", os.getenv("BITGET_DEFAULT_PRODUCT_TYPE", "USDT-FUTURES")).strip().upper(),
            margin_coin=os.getenv("LIVE_TRADING_MARGIN_COIN", "USDT").strip().upper(),
            margin_mode=os.getenv("LIVE_TRADING_MARGIN_MODE", "crossed").strip().lower(),
            position_mode=os.getenv("LIVE_TRADING_POSITION_MODE", "one_way_mode").strip().lower(),
            order_type=os.getenv("LIVE_TRADING_ENTRY_ORDER_TYPE", os.getenv("LIVE_TRADING_ORDER_TYPE", "market")).strip().lower(),
            force=os.getenv("LIVE_TRADING_FORCE", "gtc").strip().lower(),
            maker_price_levels=_env_int("LIVE_TRADING_MAKER_PRICE_LEVELS", 3),
            maker_retry_attempts=_env_int("LIVE_TRADING_MAKER_RETRY_ATTEMPTS", 3),
            maker_retry_delay_seconds=_env_float("LIVE_TRADING_MAKER_RETRY_DELAY_SECONDS", 0.3),
            maker_fallback_to_market=_env_bool("LIVE_TRADING_MAKER_FALLBACK_TO_MARKET", False),
            size=os.getenv("LIVE_TRADING_ORDER_SIZE", "").strip(),
            leverage=os.getenv("LIVE_TRADING_LEVERAGE", "").strip(),
            signal_mode=os.getenv("LIVE_TRADING_SIGNAL_MODE", "any").strip().lower(),
            strategy=os.getenv("LIVE_TRADING_STRATEGY", "stc_extreme_contrarian").strip().lower(),
            use_closed_bar=_env_bool("LIVE_TRADING_USE_CLOSED_BAR", True),
            tpsl_enabled=_env_bool("LIVE_TRADING_TPSL_ENABLED", True),
            entry_price_source=os.getenv("LIVE_TRADING_ENTRY_PRICE_SOURCE", "mark_price").strip().lower(),
            tpsl_trigger_type=os.getenv("LIVE_TRADING_TPSL_TRIGGER_TYPE", "mark_price").strip().lower(),
            atr_period=_env_int("LIVE_TRADING_ATR_PERIOD", 14),
            stop_atr_multiplier=os.getenv("LIVE_TRADING_STOP_ATR_MULTIPLIER", "2").strip(),
            tp1_r_multiple=os.getenv("LIVE_TRADING_TP1_R_MULTIPLE", "1").strip(),
            tp1_size_ratio=os.getenv("LIVE_TRADING_TP1_SIZE_RATIO", "0.5").strip(),
            tp2_r_multiple=os.getenv("LIVE_TRADING_TP2_R_MULTIPLE", "1.5").strip(),
            price_decimals=_env_int("LIVE_TRADING_PRICE_DECIMALS", 2),
            size_decimals=_env_int("LIVE_TRADING_SIZE_DECIMALS", 6),
            tpsl_retry_attempts=_env_int("LIVE_TRADING_TPSL_RETRY_ATTEMPTS", 3),
            tpsl_retry_delay_seconds=_env_float("LIVE_TRADING_TPSL_RETRY_DELAY_SECONDS", 1.0),
            close_on_tpsl_failure=_env_bool("LIVE_TRADING_CLOSE_ON_TPSL_FAILURE", False),
            tpsl_monitor_enabled=_env_bool("LIVE_TRADING_TPSL_MONITOR_ENABLED", True),
            tpsl_monitor_interval_seconds=_env_float("LIVE_TRADING_TPSL_MONITOR_INTERVAL_SECONDS", 30.0),
            email_enabled=_env_bool("LIVE_TRADING_EMAIL_ENABLED", True),
            email_to=os.getenv("LIVE_TRADING_EMAIL_TO", "").strip(),
            log_path=project_root / os.getenv("LIVE_TRADING_LOG_PATH", str(DEFAULT_LOG_PATH)).strip(),
            order_log_path=project_root / os.getenv("LIVE_TRADING_ORDER_LOG_PATH", str(DEFAULT_ORDER_LOG_PATH)).strip(),
            state_path=project_root / os.getenv("LIVE_TRADING_STATE_PATH", str(DEFAULT_STATE_PATH)).strip(),
        )


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

    def get_pending_plan_order(
        self,
        *,
        product_type: str,
        client_oid: str | None = None,
        order_id: str | None = None,
        symbol: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            "/api/v2/mix/order/orders-plan-pending",
            params={
                "productType": product_type,
                "planType": "profit_loss",
                "clientOid": client_oid,
                "orderId": order_id,
                "symbol": symbol,
            },
        )

    def get_history_plan_order(
        self,
        *,
        product_type: str,
        client_oid: str | None = None,
        order_id: str | None = None,
        symbol: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            "/api/v2/mix/order/orders-plan-history",
            params={
                "productType": product_type,
                "planType": "profit_loss",
                "clientOid": client_oid,
                "orderId": order_id,
                "symbol": symbol,
            },
        )

    def place_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/v2/mix/order/place-order", body=payload)

    def place_tpsl_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/v2/mix/order/place-tpsl-order", body=payload)

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

    def get_merge_depth(self, *, symbol: str, product_type: str, limit: str = "5") -> dict[str, Any]:
        query = urlencode({"symbol": symbol, "productType": product_type, "limit": limit})
        with urlopen(f"{self.api_base}/api/v2/mix/market/merge-depth?{query}", timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
        code = str(payload.get("code", ""))
        if code and code != "00000":
            raise RuntimeError(f"Bitget depth 返回错误 {code}: {payload.get('msg') or payload}")
        data = payload.get("data") or {}
        if not isinstance(data, dict):
            raise RuntimeError(f"Bitget depth 返回格式异常: {payload}")
        return data

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
        with urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
        code = str(payload.get("code", ""))
        if code and code != "00000":
            raise RuntimeError(f"Bitget API {path} 返回错误 {code}: {payload.get('msg') or payload}")
        return payload


class LiveTradingEngine:
    def __init__(self, project_root: Path, config: LiveTradingConfig | None = None) -> None:
        self.project_root = project_root
        self.config = config or LiveTradingConfig.from_env(project_root)
        self.logger = _build_logger(self.config.log_path)

    def send_startup_email(self, *, symbol: str, duration_seconds: int, continuous: bool) -> None:
        if not self.config.email_enabled or not self.config.email_to:
            return
        mode = "常驻实盘" if continuous else "单次实盘"
        status = "观察模式" if self.config.log_only else ("DRY-RUN" if self.config.dry_run or not self.config.enabled else "真实交易")
        subject = f"[TQ Live] {mode}已启动 {symbol.upper()} {duration_seconds}s {status}"
        html = (
            "<h3>TQ Live Trading Started</h3>"
            f"<p><b>Mode:</b> {mode}</p>"
            f"<p><b>Status:</b> {status}</p>"
            f"<p><b>Symbol:</b> {symbol.upper()}</p>"
            f"<p><b>Duration:</b> {duration_seconds}s</p>"
            f"<p><b>Enabled:</b> {self.config.enabled}</p>"
            f"<p><b>Dry Run:</b> {self.config.dry_run}</p>"
            f"<p><b>Log Only:</b> {self.config.log_only}</p>"
            f"<p><b>Strategy:</b> {self.config.strategy}</p>"
            f"<p><b>Use Closed Bar:</b> {self.config.use_closed_bar}</p>"
            f"<p><b>TP/SL Enabled:</b> {self.config.tpsl_enabled}</p>"
            f"<p><b>Started At:</b> {datetime.now(DISPLAY_TIMEZONE).strftime('%Y-%m-%d %H:%M:%S')}</p>"
        )
        try:
            send_resend_email(to=self.config.email_to, subject=subject, html=html, project_root=self.project_root)
        except Exception as exc:
            self.logger.warning("启动邮件发送失败: %s", exc)

    def run_preflight(self, *, symbol: str) -> PreflightResult:
        checks: list[dict[str, Any]] = []

        def add_check(name: str, ok: bool, detail: Any = None) -> None:
            checks.append({"name": name, "ok": ok, "detail": detail})

        try:
            if not self.config.size:
                add_check("order_size", False, "LIVE_TRADING_ORDER_SIZE 为空")
                return PreflightResult(ok=False, checks=checks, error="LIVE_TRADING_ORDER_SIZE 为空")
            size = _to_decimal(self.config.size)
            add_check("order_size", size > 0, self.config.size)
            if size <= 0:
                return PreflightResult(ok=False, checks=checks, error="LIVE_TRADING_ORDER_SIZE 必须大于 0")

            client = BitgetFuturesTradeClient(self.project_root)
            add_check("api_credentials", True, "Bitget API 凭据已加载")

            positions_payload = client.get_all_positions(product_type=self.config.product_type, margin_coin=self.config.margin_coin)
            add_check("private_positions", True, {"code": positions_payload.get("code"), "items": len(positions_payload.get("data") or [])})

            ticker = client.get_ticker(symbol=symbol, product_type=self.config.product_type)
            mark_price = ticker.get("markPrice")
            last_price = ticker.get("lastPr")
            add_check("ticker", bool(mark_price or last_price), {"markPrice": mark_price, "lastPr": last_price})

            contracts = client.get_contracts(product_type=self.config.product_type)
            contract = next((item for item in contracts if str(item.get("symbol") or "").upper() == symbol.upper()), None)
            add_check("contract", contract is not None, self._contract_preflight_detail(contract))
            if contract is None:
                return PreflightResult(ok=False, checks=checks, error=f"未找到 Bitget 合约: {symbol}")

            precision_ok, precision_detail = self._preflight_precision(size=size, contract=contract)
            add_check("precision", precision_ok, precision_detail)
            if not precision_ok:
                return PreflightResult(ok=False, checks=checks, error="价格或数量精度配置可能不符合合约规格")

            if self.config.tpsl_enabled:
                tpsl_ok = self.config.atr_period > 0 and _to_decimal(self.config.stop_atr_multiplier) > 0
                add_check(
                    "tpsl_config",
                    tpsl_ok,
                    {
                        "atr_period": self.config.atr_period,
                        "stop_atr_multiplier": self.config.stop_atr_multiplier,
                        "tp1_r_multiple": self.config.tp1_r_multiple,
                        "tp1_size_ratio": self.config.tp1_size_ratio,
                        "tp2_r_multiple": self.config.tp2_r_multiple,
                    },
                )
                if not tpsl_ok:
                    return PreflightResult(ok=False, checks=checks, error="止盈止损参数无效")

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
        side, reason = self._side_from_strategy(marker_texts, indicator_values, indicator_colors)
        last_close = float(target_candle.get("close") or snapshot.get("last_close") or 0)
        bar_open = _optional_float(target_candle.get("open"))
        bar_high = _optional_float(target_candle.get("high"))
        bar_low = _optional_float(target_candle.get("low"))
        bar_close = _optional_float(target_candle.get("close"))
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
        if self._already_executed(decision.client_oid or ""):
            result = TradeExecutionResult(
                decision=decision,
                dry_run=True,
                enabled=self.config.enabled,
                already_executed=True,
            )
            self._log_result(result)
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
        if not self.config.size:
            result = TradeExecutionResult(
                decision=decision,
                dry_run=self.config.dry_run,
                enabled=self.config.enabled,
                error="缺少 LIVE_TRADING_ORDER_SIZE，拒绝下单。",
            )
            self._log_result(result)
            self._send_email(result)
            return result

        if self.config.tpsl_enabled and decision.atr_value is None:
            result = TradeExecutionResult(
                decision=decision,
                dry_run=self.config.dry_run,
                enabled=self.config.enabled,
                error="缺少 ATR，拒绝开仓以避免裸仓。",
            )
            self._log_result(result)
            self._send_email(result)
            return result

        if self.config.dry_run or not self.config.enabled:
            try:
                dry_run_maker_price = self._dry_run_maker_price(decision)
                request = self._order_request(decision, maker_price=dry_run_maker_price)
            except Exception as exc:
                dry_run_maker_price = None
                request = {"error": f"构造开仓请求失败: {exc}"}
            if self.config.tpsl_enabled:
                try:
                    request["plannedTpsl"] = self._build_tpsl_requests(
                        decision=decision,
                        entry_price=dry_run_maker_price or self._dry_run_entry_price(decision),
                    )
                except Exception as exc:
                    request["plannedTpslError"] = str(exc)
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
                request = self._order_request(decision, maker_price=self._dry_run_maker_price(decision))
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
        tpsl_requests: list[dict[str, Any]] = []
        protection_responses: list[dict[str, Any]] = []
        entry_price: Decimal | None = None
        try:
            client = BitgetFuturesTradeClient(self.project_root)
            same_side_position = self._same_side_position(client, decision)
            if same_side_position is not None:
                result = TradeExecutionResult(
                    decision=decision,
                    dry_run=True,
                    enabled=self.config.enabled,
                    request=request,
                    response={
                        "sameSidePosition": True,
                        "message": "已存在同方向仓位，跳过开仓。",
                        "position": same_side_position,
                    },
                )
                self._record_execution(result)
                self._log_result(result)
                self._send_email(result)
                return result
            if self.config.leverage:
                client.set_leverage(
                    symbol=decision.symbol,
                    product_type=self.config.product_type,
                    margin_coin=self.config.margin_coin,
                    leverage=self.config.leverage,
                )
            if self._use_maker_entry():
                maker_result = self._place_maker_entry_with_retry(client, decision)
                request = maker_result["request"]
                order_response = maker_result["response"]
                entry_price = maker_result["entry_price"]
                if maker_result.get("fallback_to_market"):
                    tpsl_requests = self._build_tpsl_requests(decision=decision, entry_price=entry_price) if self.config.tpsl_enabled else []
                    protection_responses = self._place_tpsl_orders_with_retry(client, decision, request, order_response, tpsl_requests)
                    result = TradeExecutionResult(
                        decision=decision,
                        dry_run=False,
                        enabled=True,
                        request=request,
                        response={
                            "entryPrice": _decimal_to_string(entry_price),
                            "order": order_response,
                            "makerAttempts": maker_result["attempts"],
                            "fallbackToMarket": True,
                            "tpslRequests": tpsl_requests,
                            "tpslResponses": protection_responses,
                        },
                    )
                    self._record_execution(result)
                    self._log_result(result)
                    self._send_email(result)
                    return result
                result = TradeExecutionResult(
                    decision=decision,
                    dry_run=False,
                    enabled=True,
                    request=request,
                    response={
                        "entryPrice": _decimal_to_string(entry_price),
                        "order": order_response,
                        "makerEntryPending": True,
                        "makerAttempts": maker_result["attempts"],
                        "message": "Maker post-only 开仓单已提交，等待成交后再挂止盈止损。",
                    },
                )
                self._record_execution(result)
                self._log_result(result)
                self._send_email(result)
                return result
            request = self._order_request(decision)
            entry_price = self._entry_price(client, decision)
            tpsl_requests = self._build_tpsl_requests(decision=decision, entry_price=entry_price) if self.config.tpsl_enabled else []
            order_response = client.place_order(request)
            protection_responses = self._place_tpsl_orders_with_retry(client, decision, request, order_response, tpsl_requests)
            result = TradeExecutionResult(
                decision=decision,
                dry_run=False,
                enabled=True,
                request=request,
                response={
                    "entryPrice": _decimal_to_string(entry_price),
                    "order": order_response,
                    "tpslRequests": tpsl_requests,
                    "tpslResponses": protection_responses,
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
                    "tpslRequests": tpsl_requests,
                    "tpslResponses": protection_responses,
                },
                error=str(exc),
            )

        self._record_execution(result)
        self._log_result(result)
        self._send_email(result)
        return result

    def check_tracked_tpsl_orders(self) -> None:
        if not self.config.tpsl_monitor_enabled:
            return
        self._check_tracked_entry_orders()
        state = self._read_state()
        tracked_orders = [item for item in state.get("tracked_tpsl") or [] if isinstance(item, dict)]
        pending = [item for item in tracked_orders if not item.get("notified")]
        if not pending:
            return

        try:
            client = BitgetFuturesTradeClient(self.project_root)
        except Exception as exc:
            self.logger.warning("保护单监控初始化失败: %s", exc)
            return

        changed = False
        for item in pending:
            try:
                status_payload = self._tpsl_status(client, item)
            except Exception as exc:
                self.logger.warning("保护单状态查询失败: item=%s error=%s", json.dumps(item, ensure_ascii=False), exc)
                continue

            status = status_payload.get("status")
            if status not in {"executed", "fail_execute", "cancelled"}:
                item["last_status"] = status or "live"
                continue

            item["notified"] = True
            item["notified_at"] = int(time.time() * 1000)
            item["last_status"] = status
            item["status_payload"] = status_payload
            changed = True
            self._send_tpsl_trigger_email(item, status_payload)

        if changed:
            state["tracked_tpsl"] = tracked_orders[-500:]
            self._write_state(state)

    def _check_tracked_entry_orders(self) -> None:
        state = self._read_state()
        tracked_entries = [item for item in state.get("tracked_entries") or [] if isinstance(item, dict)]
        pending = [item for item in tracked_entries if not item.get("protection_placed") and not item.get("notified")]
        if not pending:
            return
        try:
            client = BitgetFuturesTradeClient(self.project_root)
        except Exception as exc:
            self.logger.warning("maker 开仓监控初始化失败: %s", exc)
            return

        changed = False
        for item in pending:
            try:
                detail = client.get_order_detail(
                    symbol=str(item.get("symbol") or ""),
                    product_type=self.config.product_type,
                    client_oid=str(item.get("clientOid") or "") or None,
                    order_id=str(item.get("orderId") or "") or None,
                )
                data = detail.get("data") if isinstance(detail.get("data"), dict) else {}
                status = str(data.get("status") or data.get("state") or "").lower()
                item["last_status"] = status or "unknown"
                item["order_detail"] = data
                if status in {"filled", "full_fill", "full-filled"}:
                    decision = self._decision_from_tracked_entry(item)
                    fill_price = _to_decimal(data.get("priceAvg") or data.get("fillPrice") or item.get("price"))
                    tpsl_requests = self._build_tpsl_requests(decision=decision, entry_price=fill_price) if self.config.tpsl_enabled else []
                    tpsl_responses = self._place_tpsl_orders_with_retry(client, decision, {"trackedMakerEntry": item}, detail, tpsl_requests)
                    fake_result = TradeExecutionResult(
                        decision=decision,
                        dry_run=False,
                        enabled=True,
                        request={"trackedMakerEntry": item},
                        response={"entryPrice": _decimal_to_string(fill_price), "tpslRequests": tpsl_requests, "tpslResponses": tpsl_responses},
                    )
                    self._add_tracked_tpsl_orders(state, fake_result)
                    item["protection_placed"] = True
                    item["notified"] = True
                    item["notified_at"] = int(time.time() * 1000)
                    changed = True
                    self._send_maker_entry_filled_email(item, fake_result.response or {})
                elif status in {"cancelled", "canceled"}:
                    item["notified"] = True
                    item["notified_at"] = int(time.time() * 1000)
                    changed = True
                    self._send_maker_entry_cancelled_email(item)
            except Exception as exc:
                self.logger.warning("maker 开仓状态处理失败: item=%s error=%s", json.dumps(item, ensure_ascii=False), exc)

        if changed:
            state["tracked_entries"] = tracked_entries[-500:]
            self._write_state(state)

    def _tpsl_status(self, client: BitgetFuturesTradeClient, item: dict[str, Any]) -> dict[str, Any]:
        client_oid = str(item.get("clientOid") or "").strip() or None
        order_id = str(item.get("orderId") or "").strip() or None
        symbol = str(item.get("symbol") or "").strip() or None

        history = client.get_history_plan_order(
            product_type=self.config.product_type,
            client_oid=client_oid,
            order_id=order_id,
            symbol=symbol,
        )
        history_items = self._extract_plan_orders(history)
        if history_items:
            matched = history_items[0]
            return {
                "status": str(matched.get("planStatus") or matched.get("status") or "").lower(),
                "source": "history",
                "order": matched,
                "raw": history,
            }

        pending = client.get_pending_plan_order(
            product_type=self.config.product_type,
            client_oid=client_oid,
            order_id=order_id,
            symbol=symbol,
        )
        pending_items = self._extract_plan_orders(pending)
        if pending_items:
            matched = pending_items[0]
            return {
                "status": str(matched.get("planStatus") or matched.get("status") or "live").lower(),
                "source": "pending",
                "order": matched,
                "raw": pending,
            }

        return {"status": "unknown", "source": "none", "raw": {"history": history, "pending": pending}}

    @staticmethod
    def _extract_plan_orders(payload: dict[str, Any]) -> list[dict[str, Any]]:
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, dict):
            orders = data.get("entrustedList") or data.get("orderList") or []
            return [item for item in orders if isinstance(item, dict)]
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        return []

    def _place_tpsl_orders_with_retry(
        self,
        client: BitgetFuturesTradeClient,
        decision: TradeDecision,
        order_request: dict[str, Any],
        order_response: dict[str, Any] | None,
        tpsl_requests: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        responses: list[dict[str, Any]] = []
        attempts = max(int(self.config.tpsl_retry_attempts), 1)
        delay = max(float(self.config.tpsl_retry_delay_seconds), 0.0)

        for tpsl_request in tpsl_requests:
            last_error: Exception | None = None
            for attempt in range(1, attempts + 1):
                try:
                    response = client.place_tpsl_order(tpsl_request)
                    responses.append({"request": tpsl_request, "response": response, "attempt": attempt})
                    break
                except Exception as exc:
                    last_error = exc
                    self.logger.warning(
                        "保护单提交失败，准备重试: attempt=%s/%s request=%s error=%s",
                        attempt,
                        attempts,
                        json.dumps(tpsl_request, ensure_ascii=False),
                        exc,
                    )
                    if attempt < attempts and delay > 0:
                        time.sleep(delay)
            else:
                error_text = str(last_error) if last_error is not None else "未知保护单提交失败"
                self._send_tpsl_emergency_email(
                    decision=decision,
                    order_request=order_request,
                    order_response=order_response,
                    tpsl_request=tpsl_request,
                    placed_tpsl_responses=responses,
                    error=error_text,
                    close_response=None,
                )
                if self.config.close_on_tpsl_failure:
                    close_response = self._close_position_after_tpsl_failure(client, decision)
                    self._send_tpsl_emergency_email(
                        decision=decision,
                        order_request=order_request,
                        order_response=order_response,
                        tpsl_request=tpsl_request,
                        placed_tpsl_responses=responses,
                        error=error_text,
                        close_response=close_response,
                    )
                raise RuntimeError(f"保护单提交失败，已重试 {attempts} 次: {error_text}")

        return responses

    def _place_maker_entry_with_retry(
        self,
        client: BitgetFuturesTradeClient,
        decision: TradeDecision,
    ) -> dict[str, Any]:
        attempts = max(int(self.config.maker_retry_attempts), 1)
        delay = max(float(self.config.maker_retry_delay_seconds), 0.0)
        attempt_records: list[dict[str, Any]] = []
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            maker_price: Decimal | None = None
            request: dict[str, Any] | None = None
            try:
                maker_price = self._maker_entry_price(client, decision)
                request = self._order_request(decision, maker_price=maker_price)
                response = client.place_order(request)
                attempt_records.append(
                    {
                        "attempt": attempt,
                        "entryPrice": _decimal_to_string(maker_price),
                        "request": request,
                        "response": response,
                        "ok": True,
                    }
                )
                return {
                    "entry_price": maker_price,
                    "request": request,
                    "response": response,
                    "attempts": attempt_records,
                }
            except Exception as exc:
                last_error = exc
                attempt_records.append(
                    {
                        "attempt": attempt,
                        "entryPrice": _decimal_to_string(maker_price) if maker_price is not None else None,
                        "request": request,
                        "error": str(exc),
                        "ok": False,
                    }
                )
                self.logger.warning(
                    "maker post-only 开仓失败，准备重试: attempt=%s/%s error=%s",
                    attempt,
                    attempts,
                    exc,
                )
                if attempt < attempts and delay > 0:
                    time.sleep(delay)

        if self.config.maker_fallback_to_market:
            market_request = self._market_order_request(decision)
            fallback_entry_price = self._entry_price(client, decision)
            response = client.place_order(market_request)
            attempt_records.append(
                {
                    "attempt": "fallback_market",
                    "entryPrice": _decimal_to_string(fallback_entry_price),
                    "request": market_request,
                    "response": response,
                    "ok": True,
                }
            )
            self._send_maker_fallback_email(decision, attempt_records)
            return {
                "entry_price": fallback_entry_price,
                "request": market_request,
                "response": response,
                "attempts": attempt_records,
                "fallback_to_market": True,
            }

        self._send_maker_entry_failed_email(decision, attempt_records, str(last_error) if last_error is not None else "未知 maker 开仓失败")
        raise RuntimeError(f"maker post-only 开仓失败，已重试 {attempts} 次: {last_error}")

    def _close_position_after_tpsl_failure(self, client: BitgetFuturesTradeClient, decision: TradeDecision) -> dict[str, Any]:
        payload = {
            "symbol": decision.symbol,
            "productType": self.config.product_type,
        }
        if self.config.position_mode == "hedge_mode":
            payload["holdSide"] = "long" if decision.side == "buy" else "short"
        try:
            response = client.close_position_order(payload)
            self.logger.error("保护单失败后已尝试自动平仓: %s", json.dumps({"request": payload, "response": response}, ensure_ascii=False))
            return {"request": payload, "response": response}
        except Exception as exc:
            self.logger.error("保护单失败后自动平仓也失败: %s", exc)
            return {"request": payload, "error": str(exc)}

    def _send_tpsl_emergency_email(
        self,
        *,
        decision: TradeDecision,
        order_request: dict[str, Any],
        order_response: dict[str, Any] | None,
        tpsl_request: dict[str, Any],
        placed_tpsl_responses: list[dict[str, Any]],
        error: str,
        close_response: dict[str, Any] | None,
    ) -> None:
        if not self.config.email_enabled or not self.config.email_to:
            return
        close_status = "未启用自动平仓" if close_response is None else "已尝试自动平仓"
        subject = f"[TQ Live][URGENT] 保护单失败 {decision.symbol} {decision.side or '-'} {decision.bar_time_label or decision.bar_time}"
        html = (
            "<h3>TQ Live Trading URGENT</h3>"
            "<p><b>Status:</b> 保护单提交失败，可能存在裸仓风险。</p>"
            f"<p><b>Symbol:</b> {decision.symbol}</p>"
            f"<p><b>Side:</b> {decision.side or '-'}</p>"
            f"<p><b>Time:</b> {decision.bar_time_label or decision.bar_time}</p>"
            f"<p><b>Error:</b> {error}</p>"
            f"<p><b>Close Action:</b> {close_status}</p>"
            f"<pre>{json.dumps({'orderRequest': order_request, 'orderResponse': order_response, 'failedTpslRequest': tpsl_request, 'placedTpslResponses': placed_tpsl_responses, 'closeResponse': close_response}, ensure_ascii=False, indent=2)}</pre>"
        )
        try:
            send_resend_email(to=self.config.email_to, subject=subject, html=html, project_root=self.project_root)
        except Exception as exc:
            self.logger.warning("保护单紧急邮件发送失败: %s", exc)

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
            raise RuntimeError(f"ticker 中缺少 {field}，拒绝开仓以避免无法计算保护单。")
        return _to_decimal(raw_value)

    def _use_maker_entry(self) -> bool:
        return self.config.order_type in {"maker", "post_only", "post-only"}

    def _maker_entry_price(self, client: BitgetFuturesTradeClient, decision: TradeDecision) -> Decimal:
        depth = client.get_merge_depth(symbol=decision.symbol, product_type=self.config.product_type, limit="5")
        bids = depth.get("bids") or []
        asks = depth.get("asks") or []
        if not bids or not asks:
            raise RuntimeError("盘口为空，无法生成 maker 开仓价格。")
        best_bid = _to_decimal(bids[0][0])
        best_ask = _to_decimal(asks[0][0])
        tick = self._price_tick(client, decision.symbol)
        levels = max(int(self.config.maker_price_levels), 0)
        if decision.side == "buy":
            price = best_bid - tick * levels
        elif decision.side == "sell":
            price = best_ask + tick * levels
        else:
            raise RuntimeError(f"未知开仓方向: {decision.side}")
        if price <= 0:
            raise RuntimeError("maker 开仓价格计算结果无效。")
        return _quantize_decimal(price, self.config.price_decimals)

    def _dry_run_maker_price(self, decision: TradeDecision) -> Decimal | None:
        if not self._use_maker_entry():
            return None
        if decision.bar_close is None:
            raise RuntimeError("缺少 bar_close，无法预估 maker 价格。")
        tick = Decimal("1") if self.config.price_decimals <= 0 else Decimal("1").scaleb(-self.config.price_decimals)
        levels = max(int(self.config.maker_price_levels), 0)
        base = _to_decimal(decision.bar_close)
        price = base - tick * levels if decision.side == "buy" else base + tick * levels
        return _quantize_decimal(price, self.config.price_decimals)

    def _price_tick(self, client: BitgetFuturesTradeClient, symbol: str) -> Decimal:
        contracts = client.get_contracts(product_type=self.config.product_type)
        contract = next((item for item in contracts if str(item.get("symbol") or "").upper() == symbol.upper()), None)
        if not contract:
            return Decimal("1") if self.config.price_decimals <= 0 else Decimal("1").scaleb(-self.config.price_decimals)
        price_place = _env_int_from_value(contract.get("pricePlace"), self.config.price_decimals)
        end_step = _to_decimal(contract.get("priceEndStep") or "1")
        base_tick = Decimal("1") if price_place <= 0 else Decimal("1").scaleb(-price_place)
        tick = base_tick * end_step
        return tick if tick > 0 else (Decimal("1") if self.config.price_decimals <= 0 else Decimal("1").scaleb(-self.config.price_decimals))

    def _dry_run_entry_price(self, decision: TradeDecision) -> Decimal:
        if self.config.entry_price_source == "bar_close" and decision.bar_close is not None:
            return _to_decimal(decision.bar_close)
        if decision.bar_close is None:
            raise RuntimeError("缺少 bar_close，无法预估 dry-run 保护单。")
        return _to_decimal(decision.bar_close)

    def _build_tpsl_requests(self, *, decision: TradeDecision, entry_price: Decimal) -> list[dict[str, Any]]:
        if decision.side not in {"buy", "sell"}:
            return []
        if decision.atr_value is None:
            raise RuntimeError("缺少 ATR，无法计算止盈止损。")
        size = _to_decimal(self.config.size)
        if size <= 0:
            raise RuntimeError("LIVE_TRADING_ORDER_SIZE 必须大于 0。")

        atr = _to_decimal(decision.atr_value)
        stop_multiplier = _to_decimal(self.config.stop_atr_multiplier)
        risk = atr * stop_multiplier
        if risk <= 0:
            raise RuntimeError("ATR 或止损倍数无效，无法计算 R。")

        tp1_ratio = min(max(_to_decimal(self.config.tp1_size_ratio), Decimal("0")), Decimal("1"))
        tp1_size = _quantize_decimal(size * tp1_ratio, self.config.size_decimals)
        tp2_size = _quantize_decimal(size - tp1_size, self.config.size_decimals)
        if tp1_size <= 0 or tp2_size <= 0:
            raise RuntimeError("止盈分仓数量无效，请调整 LIVE_TRADING_TP1_SIZE_RATIO 或下单数量。")

        direction = Decimal("1") if decision.side == "buy" else Decimal("-1")
        stop_price = entry_price - direction * risk
        tp1_price = entry_price + direction * risk * _to_decimal(self.config.tp1_r_multiple)
        tp2_price = entry_price + direction * risk * _to_decimal(self.config.tp2_r_multiple)
        if stop_price <= 0 or tp1_price <= 0 or tp2_price <= 0:
            raise RuntimeError("止盈止损价格计算结果无效，请检查 ATR 和 R 参数。")
        hold_side = self._tpsl_hold_side(decision.side)

        return [
            self._tpsl_request(decision, "loss_plan", stop_price, size, self._child_client_oid(decision.client_oid, "sl")),
            self._tpsl_request(decision, "profit_plan", tp1_price, tp1_size, self._child_client_oid(decision.client_oid, "tp1"), hold_side=hold_side),
            self._tpsl_request(decision, "profit_plan", tp2_price, tp2_size, self._child_client_oid(decision.client_oid, "tp2"), hold_side=hold_side),
        ]

    def _tpsl_request(
        self,
        decision: TradeDecision,
        plan_type: str,
        trigger_price: Decimal,
        size: Decimal,
        client_oid: str,
        hold_side: str | None = None,
    ) -> dict[str, Any]:
        return {
            "marginCoin": self.config.margin_coin,
            "productType": self.config.product_type,
            "symbol": decision.symbol,
            "planType": plan_type,
            "triggerPrice": _decimal_to_string(_quantize_decimal(trigger_price, self.config.price_decimals)),
            "triggerType": self.config.tpsl_trigger_type,
            "executePrice": "0",
            "holdSide": hold_side or self._tpsl_hold_side(decision.side or ""),
            "size": _decimal_to_string(size),
            "clientOid": client_oid[:64],
        }

    @staticmethod
    def _child_client_oid(parent_oid: str | None, suffix: str) -> str:
        base = parent_oid or f"tq-live-{int(time.time())}"
        suffix_text = f"-{suffix}"
        return f"{base[:64 - len(suffix_text)]}{suffix_text}"

    def _tpsl_hold_side(self, side: str) -> str:
        if self.config.position_mode == "hedge_mode":
            return "long" if side == "buy" else "short"
        return side

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
    ) -> tuple[str | None, str]:
        if self.config.strategy == "stc_extreme_contrarian":
            return self._stc_extreme_contrarian_side(marker_texts, indicator_values, indicator_colors)
        side = self._side_from_marker_texts(marker_texts)
        if side is None:
            return None, f"目标 K 线没有满足 {self.config.signal_mode} 模式的交易信号"
        return side, f"检测到 {','.join(marker_texts)} 信号"

    def _stc_extreme_contrarian_side(
        self,
        marker_texts: list[str],
        indicator_values: dict[str, float],
        indicator_colors: dict[str, str],
    ) -> tuple[str | None, str]:
        texts = set(marker_texts)
        stc_value = indicator_values.get("stc.stc")
        stc_color = indicator_colors.get("stc.stc", "")
        stc_is_red = _is_red_color(stc_color)
        stc_is_green = _is_green_color(stc_color)
        has_any_buy = "Buy" in texts or "买" in texts
        has_any_sell = "Sell" in texts or "卖" in texts

        if has_any_sell and stc_value is not None and stc_value > 75 and stc_is_red:
            return "sell", f"空单观察信号：Sell 或 卖 出现，且 STC={stc_value:.2f}>75 并为红色"
        if has_any_buy and stc_value is not None and stc_value < 25 and stc_is_green:
            return "buy", f"多单观察信号：Buy 或 买 出现，且 STC={stc_value:.2f}<25 并为绿色"

        return (
            None,
            "未满足观察策略：空单需 Sell/卖 任一信号且 STC>75 红色；多单需 Buy/买 任一信号且 STC<25 绿色。"
            f" 当前 signals={','.join(marker_texts) or '-'}, STC={stc_value}, color={stc_color or '-'}",
        )

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

    def _order_request(self, decision: TradeDecision, maker_price: Decimal | None = None) -> dict[str, Any]:
        order_type = "limit" if self._use_maker_entry() else self.config.order_type
        request = {
            "symbol": decision.symbol,
            "productType": self.config.product_type,
            "marginMode": self.config.margin_mode,
            "marginCoin": self.config.margin_coin,
            "size": self.config.size,
            "side": decision.side,
            "orderType": order_type,
            "clientOid": decision.client_oid,
        }
        if self._use_maker_entry():
            if maker_price is None:
                raise ValueError("maker 开仓需要先计算 post-only limit 价格。")
            request["price"] = _decimal_to_string(_quantize_decimal(maker_price, self.config.price_decimals))
            request["force"] = "post_only"
        elif self.config.order_type == "limit":
            raise ValueError("当前实盘模块只自动生成 market 订单；limit 订单需要显式补价格逻辑。")
        if self.config.position_mode == "hedge_mode":
            request["tradeSide"] = "open"
        if self.config.force and order_type == "limit" and not self._use_maker_entry():
            request["force"] = self.config.force
        return request

    def _market_order_request(self, decision: TradeDecision) -> dict[str, Any]:
        request = {
            "symbol": decision.symbol,
            "productType": self.config.product_type,
            "marginMode": self.config.margin_mode,
            "marginCoin": self.config.margin_coin,
            "size": self.config.size,
            "side": decision.side,
            "orderType": "market",
            "clientOid": self._child_client_oid(decision.client_oid, "mkt"),
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

        if result.decision.client_oid:
            state = self._read_state()
            client_oids = list(dict.fromkeys([*(state.get("client_oids") or []), result.decision.client_oid]))[-500:]
            state["client_oids"] = client_oids
            self._add_tracked_entry_order(state, result)
            self._add_tracked_tpsl_orders(state, result)
            self._write_state(state)

    def _add_tracked_entry_order(self, state: dict[str, Any], result: TradeExecutionResult) -> None:
        response = result.response or {}
        if not isinstance(response, dict) or not response.get("makerEntryPending"):
            return
        order_response = response.get("order") if isinstance(response.get("order"), dict) else {}
        data = order_response.get("data") if isinstance(order_response.get("data"), dict) else {}
        request = result.request or {}
        client_oid = str(data.get("clientOid") or request.get("clientOid") or result.decision.client_oid or "").strip()
        order_id = str(data.get("orderId") or "").strip()
        tracked = [item for item in state.get("tracked_entries") or [] if isinstance(item, dict)]
        existing_keys = {str(item.get("clientOid") or item.get("orderId") or "") for item in tracked}
        key = client_oid or order_id
        if not key or key in existing_keys:
            return
        tracked.append(
            {
                "clientOid": client_oid,
                "orderId": order_id,
                "symbol": result.decision.symbol,
                "side": result.decision.side,
                "size": str(request.get("size") or self.config.size),
                "price": str(request.get("price") or ""),
                "bar_time": result.decision.bar_time,
                "bar_time_label": result.decision.bar_time_label,
                "atr_value": result.decision.atr_value,
                "created_at": int(time.time() * 1000),
                "protection_placed": False,
                "notified": False,
                "last_status": "live",
            }
        )
        state["tracked_entries"] = tracked[-500:]

    def _add_tracked_tpsl_orders(self, state: dict[str, Any], result: TradeExecutionResult) -> None:
        response = result.response or {}
        tpsl_responses = response.get("tpslResponses") if isinstance(response, dict) else None
        if not isinstance(tpsl_responses, list):
            return

        existing_keys = {
            str(item.get("clientOid") or item.get("orderId") or "")
            for item in (state.get("tracked_tpsl") or [])
            if isinstance(item, dict)
        }
        tracked = [item for item in state.get("tracked_tpsl") or [] if isinstance(item, dict)]
        for item in tpsl_responses:
            if not isinstance(item, dict):
                continue
            request = item.get("request") if isinstance(item.get("request"), dict) else {}
            response = item.get("response") if isinstance(item.get("response"), dict) else {}
            data = response.get("data") if isinstance(response.get("data"), dict) else {}
            client_oid = str(data.get("clientOid") or request.get("clientOid") or "").strip()
            order_id = str(data.get("orderId") or "").strip()
            key = client_oid or order_id
            if not key or key in existing_keys:
                continue
            tracked.append(
                {
                    "clientOid": client_oid,
                    "orderId": order_id,
                    "symbol": result.decision.symbol,
                    "side": result.decision.side,
                    "label": self._tpsl_label(client_oid),
                    "planType": str(request.get("planType") or ""),
                    "triggerPrice": str(request.get("triggerPrice") or ""),
                    "size": str(request.get("size") or ""),
                    "bar_time": result.decision.bar_time,
                    "bar_time_label": result.decision.bar_time_label,
                    "created_at": int(time.time() * 1000),
                    "notified": False,
                    "last_status": "live",
                }
            )
            existing_keys.add(key)
        state["tracked_tpsl"] = tracked[-500:]

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
        side_label = {"buy": "多单观察", "sell": "空单观察"}.get(decision.side or "", decision.side or "-")
        price_label = f"{decision.bar_close:.2f}" if decision.bar_close is not None else "-"
        subject = f"[TQ Live] {status} {side_label} {decision.symbol} {price_label} {decision.bar_time_label or decision.bar_time}"
        html = (
            "<h3>TQ Live Trading</h3>"
            f"<p><b>Status:</b> {status}</p>"
            f"<p><b>Symbol:</b> {decision.symbol}</p>"
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

    def _send_tpsl_trigger_email(self, item: dict[str, Any], status_payload: dict[str, Any]) -> None:
        if not self.config.email_enabled or not self.config.email_to:
            return
        status = str(status_payload.get("status") or "").lower()
        status_label = {
            "executed": "已触发成交",
            "fail_execute": "触发失败",
            "cancelled": "已取消",
        }.get(status, status or "未知")
        label = str(item.get("label") or "保护单")
        symbol = str(item.get("symbol") or "")
        side = str(item.get("side") or "")
        subject = f"[TQ Live] {label} {status_label} {symbol} {side}"
        html = (
            "<h3>TQ Live TP/SL Update</h3>"
            f"<p><b>Status:</b> {status_label}</p>"
            f"<p><b>Label:</b> {label}</p>"
            f"<p><b>Symbol:</b> {symbol}</p>"
            f"<p><b>Side:</b> {side}</p>"
            f"<p><b>Trigger Price:</b> {item.get('triggerPrice') or '-'}</p>"
            f"<p><b>Size:</b> {item.get('size') or '-'}</p>"
            f"<p><b>Signal Time:</b> {item.get('bar_time_label') or item.get('bar_time') or '-'}</p>"
            f"<pre>{json.dumps({'tracked': item, 'status': status_payload}, ensure_ascii=False, indent=2)}</pre>"
        )
        try:
            send_resend_email(to=self.config.email_to, subject=subject, html=html, project_root=self.project_root)
        except Exception as exc:
            self.logger.warning("保护单触发邮件发送失败: %s", exc)

    def _send_maker_entry_filled_email(self, item: dict[str, Any], response: dict[str, Any]) -> None:
        if not self.config.email_enabled or not self.config.email_to:
            return
        symbol = str(item.get("symbol") or "")
        side = str(item.get("side") or "")
        subject = f"[TQ Live] Maker开仓已成交并挂保护单 {symbol} {side}"
        html = (
            "<h3>TQ Live Maker Entry Filled</h3>"
            f"<p><b>Symbol:</b> {symbol}</p>"
            f"<p><b>Side:</b> {side}</p>"
            f"<p><b>Entry Price:</b> {response.get('entryPrice') or item.get('price') or '-'}</p>"
            f"<p><b>Signal Time:</b> {item.get('bar_time_label') or item.get('bar_time') or '-'}</p>"
            f"<pre>{json.dumps({'entry': item, 'protection': response}, ensure_ascii=False, indent=2)}</pre>"
        )
        try:
            send_resend_email(to=self.config.email_to, subject=subject, html=html, project_root=self.project_root)
        except Exception as exc:
            self.logger.warning("maker 成交邮件发送失败: %s", exc)

    def _send_maker_entry_cancelled_email(self, item: dict[str, Any]) -> None:
        if not self.config.email_enabled or not self.config.email_to:
            return
        subject = f"[TQ Live] Maker开仓已取消 {item.get('symbol') or ''} {item.get('side') or ''}"
        html = f"<h3>TQ Live Maker Entry Cancelled</h3><pre>{json.dumps(item, ensure_ascii=False, indent=2)}</pre>"
        try:
            send_resend_email(to=self.config.email_to, subject=subject, html=html, project_root=self.project_root)
        except Exception as exc:
            self.logger.warning("maker 取消邮件发送失败: %s", exc)

    def _send_maker_entry_failed_email(self, decision: TradeDecision, attempts: list[dict[str, Any]], error: str) -> None:
        if not self.config.email_enabled or not self.config.email_to:
            return
        subject = f"[TQ Live] Maker开仓失败 {decision.symbol} {decision.side or '-'}"
        html = (
            "<h3>TQ Live Maker Entry Failed</h3>"
            f"<p><b>Symbol:</b> {decision.symbol}</p>"
            f"<p><b>Side:</b> {decision.side or '-'}</p>"
            f"<p><b>Error:</b> {error}</p>"
            f"<p><b>Signal Time:</b> {decision.bar_time_label or decision.bar_time or '-'}</p>"
            f"<pre>{json.dumps({'attempts': attempts}, ensure_ascii=False, indent=2)}</pre>"
        )
        try:
            send_resend_email(to=self.config.email_to, subject=subject, html=html, project_root=self.project_root)
        except Exception as exc:
            self.logger.warning("maker 失败邮件发送失败: %s", exc)

    def _send_maker_fallback_email(self, decision: TradeDecision, attempts: list[dict[str, Any]]) -> None:
        if not self.config.email_enabled or not self.config.email_to:
            return
        subject = f"[TQ Live] Maker失败已降级市价开仓 {decision.symbol} {decision.side or '-'}"
        html = (
            "<h3>TQ Live Maker Fallback</h3>"
            "<p><b>Status:</b> maker post-only 重试失败，已按配置降级为 market 开仓。</p>"
            f"<p><b>Symbol:</b> {decision.symbol}</p>"
            f"<p><b>Side:</b> {decision.side or '-'}</p>"
            f"<p><b>Signal Time:</b> {decision.bar_time_label or decision.bar_time or '-'}</p>"
            f"<pre>{json.dumps({'attempts': attempts}, ensure_ascii=False, indent=2)}</pre>"
        )
        try:
            send_resend_email(to=self.config.email_to, subject=subject, html=html, project_root=self.project_root)
        except Exception as exc:
            self.logger.warning("maker 降级邮件发送失败: %s", exc)

    def _decision_from_tracked_entry(self, item: dict[str, Any]) -> TradeDecision:
        return TradeDecision(
            action="place_order",
            symbol=str(item.get("symbol") or "").upper(),
            side=str(item.get("side") or "") or None,
            bar_time=int(item.get("bar_time") or 0) or None,
            atr_value=_optional_float(item.get("atr_value")),
            bar_time_label=str(item.get("bar_time_label") or ""),
            client_oid=str(item.get("clientOid") or ""),
        )

    @staticmethod
    def _tpsl_label(client_oid: str) -> str:
        lowered = client_oid.lower()
        if lowered.endswith("-sl"):
            return "止损"
        if lowered.endswith("-tp1"):
            return "止盈1"
        if lowered.endswith("-tp2"):
            return "止盈2"
        return "保护单"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip()
    if raw == "":
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_int_from_value(value: Any, default: int) -> int:
    try:
        if value in (None, ""):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


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


def _format_price(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


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
