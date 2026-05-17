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
    size: str = ""
    leverage: str = ""
    signal_mode: str = "any"
    strategy: str = "stc_extreme_contrarian"
    use_closed_bar: bool = True
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
            order_type=os.getenv("LIVE_TRADING_ORDER_TYPE", "market").strip().lower(),
            force=os.getenv("LIVE_TRADING_FORCE", "gtc").strip().lower(),
            size=os.getenv("LIVE_TRADING_ORDER_SIZE", "").strip(),
            leverage=os.getenv("LIVE_TRADING_LEVERAGE", "").strip(),
            signal_mode=os.getenv("LIVE_TRADING_SIGNAL_MODE", "any").strip().lower(),
            strategy=os.getenv("LIVE_TRADING_STRATEGY", "stc_extreme_contrarian").strip().lower(),
            use_closed_bar=_env_bool("LIVE_TRADING_USE_CLOSED_BAR", True),
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

    def place_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/v2/mix/order/place-order", body=payload)

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
            f"<p><b>Started At:</b> {datetime.now(DISPLAY_TIMEZONE).strftime('%Y-%m-%d %H:%M:%S')}</p>"
        )
        try:
            send_resend_email(to=self.config.email_to, subject=subject, html=html, project_root=self.project_root)
        except Exception as exc:
            self.logger.warning("启动邮件发送失败: %s", exc)

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

        request = self._order_request(decision)
        if self.config.dry_run or not self.config.enabled:
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
            response = client.place_order(request)
            result = TradeExecutionResult(
                decision=decision,
                dry_run=False,
                enabled=True,
                request=request,
                response=response,
            )
        except Exception as exc:
            result = TradeExecutionResult(
                decision=decision,
                dry_run=False,
                enabled=True,
                request=request,
                error=str(exc),
            )

        self._record_execution(result)
        self._log_result(result)
        self._send_email(result)
        return result

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

    def _order_request(self, decision: TradeDecision) -> dict[str, Any]:
        request = {
            "symbol": decision.symbol,
            "productType": self.config.product_type,
            "marginMode": self.config.margin_mode,
            "marginCoin": self.config.margin_coin,
            "size": self.config.size,
            "side": decision.side,
            "orderType": self.config.order_type,
            "clientOid": decision.client_oid,
        }
        if self.config.order_type == "limit":
            raise ValueError("当前实盘模块只自动生成 market 订单；limit 订单需要显式补价格逻辑。")
        if self.config.position_mode == "hedge_mode":
            request["tradeSide"] = "open"
        if self.config.force and self.config.order_type == "limit":
            request["force"] = self.config.force
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
            self.config.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.config.state_path.write_text(json.dumps({"client_oids": client_oids}, ensure_ascii=False, indent=2), encoding="utf-8")

    def _read_state(self) -> dict[str, Any]:
        if not self.config.state_path.exists():
            return {}
        try:
            return json.loads(self.config.state_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

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


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip()
    if raw == "":
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_price(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


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
