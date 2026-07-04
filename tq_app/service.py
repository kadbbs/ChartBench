from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import threading
import time
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from tq_app.contracts import (
    format_contract_label,
    load_binance_contract_catalog,
    load_bitget_contract_catalog,
)
from tq_app.data_sources import DataSource, create_data_source, get_available_data_sources
from tq_app.data_sources.binance import BINANCE_INTERVAL_MAP, load_binance_account_summary
from tq_app.data_sources.bitget import BITGET_GRANULARITY_MAP, load_bitget_account_summary
from tq_app.indicators import build_indicator_registry
from tq_app.models import IndicatorMeta, IndicatorResult

TV_UP = "#089981"
TV_DOWN = "#f23645"
DEFAULT_DURATION_OPTIONS = [60, 180, 300, 900, 1800, 3600, 86400]
DEFAULT_BAR_MODES = [
    {"id": "time", "label": "时间 K 线"},
    {"id": "tick", "label": "Tick 图"},
    {"id": "range", "label": "Range Bar"},
    {"id": "renko", "label": "Renko"},
]
DEFAULT_RANGE_TICKS = 10
DEFAULT_BRICK_LENGTH = 10000
DISPLAY_TIMEZONE = ZoneInfo("Asia/Shanghai")
BITGET_PROVIDER = "bitget"
BINANCE_PROVIDER = "binance"


class MarketDataService:
    def __init__(
        self,
        provider: str,
        symbol: str,
        duration_seconds: int,
        data_length: int,
        refresh_ms: int,
        project_root: Path,
        brick_length: int = DEFAULT_BRICK_LENGTH,
        bar_mode: str = "time",
        range_ticks: int = DEFAULT_RANGE_TICKS,
    ) -> None:
        self.provider = self._resolve_provider(provider)
        self.symbol = symbol
        self.duration_seconds = duration_seconds
        self.data_length = data_length
        self.brick_length = brick_length
        self.refresh_ms = refresh_ms
        self.project_root = project_root
        self.bar_mode = bar_mode
        self.range_ticks = range_ticks
        self._source_lock = threading.Lock()
        self._data_sources: dict[tuple[str, str, int, str, int, int, int], DataSource] = {}
        self._contracts_by_provider: dict[str, list[dict[str, Any]]] = {}
        self.indicators = build_indicator_registry(project_root)

    def start(self) -> None:
        self._get_data_source(
            self.provider,
            self.symbol,
            self.duration_seconds,
            self.bar_mode,
            self.range_ticks,
            self.brick_length,
            self.data_length,
        )

    def stop(self) -> None:
        with self._source_lock:
            data_sources = list(self._data_sources.values())
            self._data_sources.clear()
        for data_source in data_sources:
            data_source.stop()

    def get_health(self) -> dict[str, Any]:
        self.start()
        now = time.time()
        stale_after_seconds = max(60, int(self.duration_seconds) * 3)
        with self._source_lock:
            sources = list(self._data_sources.items())

        source_states: list[dict[str, Any]] = []
        healthy = True
        for key, data_source in sources:
            provider, symbol, duration_seconds, bar_mode, range_ticks, brick_length, data_length = key
            status = data_source.status()
            last_message_at = status.get("last_message_at")
            last_update_at = status.get("last_update_at")
            freshness_at = last_message_at or last_update_at
            last_message_age = now - float(last_message_at) if last_message_at else None
            last_update_age = now - float(freshness_at) if freshness_at else None
            stream_state = str(status.get("stream_state") or "")
            source_healthy = (
                status.get("error") is None
                and int(status.get("version") or 0) > 0
                and stream_state in {"connected", "live", "history_ready"}
                and last_update_age is not None
                and last_update_age <= stale_after_seconds
            )
            if not source_healthy:
                healthy = False
            source_states.append(
                {
                    "provider": provider,
                    "symbol": symbol,
                    "duration_seconds": duration_seconds,
                    "bar_mode": bar_mode,
                    "range_ticks": range_ticks,
                    "brick_length": brick_length,
                    "data_length": data_length,
                    "healthy": source_healthy,
                    "last_message_age": last_message_age,
                    "last_update_age": last_update_age,
                    "status": status,
                }
            )

        return {
            "healthy": healthy,
            "stale_after_seconds": stale_after_seconds,
            "sources": source_states,
        }

    def get_config(self, provider: str | None = None) -> dict[str, Any]:
        effective_provider = self._resolve_provider(provider)
        contracts = self._load_contracts(effective_provider)
        default_symbol = self._default_symbol_for_provider(effective_provider)
        selected_symbol = default_symbol
        current_contract = next((item for item in contracts if item["symbol"] == self.symbol), None)
        if current_contract is not None:
            selected_symbol = self.symbol
        indicator_meta = [asdict(meta) for meta in self.indicators.list_meta()]
        duration_options = self._duration_options_for_provider(effective_provider)
        bar_modes = self._bar_modes_for_provider(effective_provider)
        return {
            "provider": effective_provider,
            "providers": get_available_data_sources(),
            "symbol": selected_symbol,
            "symbol_label": self._symbol_label(effective_provider, selected_symbol),
            "provider_hint": self._provider_hint(effective_provider),
            "provider_account": self._provider_account(effective_provider),
            "contract_detail": self._contract_detail(effective_provider, selected_symbol),
            "duration_seconds": self.duration_seconds,
            "duration_options": duration_options,
            "bar_mode": self.bar_mode,
            "bar_modes": bar_modes,
            "range_ticks": self.range_ticks,
            "data_length": self.data_length,
            "brick_length": self.brick_length,
            "refresh_ms": self._refresh_interval_ms(effective_provider),
            "contracts": contracts,
            "indicators": indicator_meta,
            "default_indicator_ids": self.indicators.default_ids(),
        }

    def get_snapshot(
        self,
        indicator_ids: list[str] | None = None,
        indicator_params: dict[str, dict[str, Any]] | None = None,
        symbol: str | None = None,
        duration_seconds: int | None = None,
        bar_mode: str | None = None,
        range_ticks: int | None = None,
        brick_length: int | None = None,
        data_length: int | None = None,
        provider: str | None = None,
    ) -> dict[str, Any]:
        effective_provider = self._resolve_provider(provider)
        effective_symbol = (symbol or self._default_symbol_for_provider(effective_provider)).strip()
        effective_duration = duration_seconds or self.duration_seconds
        effective_bar_mode = (bar_mode or self.bar_mode).strip() or "time"
        effective_range_ticks = range_ticks or self.range_ticks
        effective_brick_length = brick_length or self.brick_length
        effective_data_length = data_length or self.data_length
        selected = indicator_ids or self.indicators.default_ids()
        all_params = indicator_params or {}

        data_source = self._get_data_source(
            effective_provider,
            effective_symbol,
            effective_duration,
            effective_bar_mode,
            effective_range_ticks,
            effective_brick_length,
            effective_data_length,
        )
        bars, source_status = data_source.get_bars_with_status()
        normalized = self._with_chart_time(bars, effective_bar_mode)

        results: list[IndicatorResult] = []
        for indicator_id in selected:
            indicator = self.indicators.get(indicator_id)
            resolved_params = indicator.resolve_params(all_params.get(indicator_id))
            results.append(indicator.build(normalized, resolved_params))

        last_close = float(normalized.iloc[-1]["close"])
        prev_close = float(normalized.iloc[-2]["close"]) if len(normalized) > 1 else last_close

        return {
            "symbol": effective_symbol,
            "symbol_label": self._symbol_label(effective_provider, effective_symbol),
            "provider": effective_provider,
            "provider_hint": self._provider_hint(effective_provider),
            "provider_account": self._provider_account(effective_provider),
            "refresh_ms": self._refresh_interval_ms(effective_provider),
            "contract_detail": self._contract_detail(effective_provider, effective_symbol),
            "duration_seconds": effective_duration,
            "bar_mode": effective_bar_mode,
            "range_ticks": effective_range_ticks,
            "brick_length": effective_brick_length,
            "data_length": effective_data_length,
            "time_labels": self._serialize_time_labels(normalized),
            "candles": self._serialize_candles(normalized),
            "volume": self._serialize_volume(normalized),
            "indicators": [self._serialize_indicator(item) for item in results],
            "last_close": last_close,
            "last_color": TV_UP if last_close >= prev_close else TV_DOWN,
            "last_time": pd.Timestamp(normalized.iloc[-1]["datetime"]).tz_convert(DISPLAY_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S"),
            "stream": source_status,
        }

    def wait_for_update(
        self,
        symbol: str | None = None,
        duration_seconds: int | None = None,
        bar_mode: str | None = None,
        range_ticks: int | None = None,
        brick_length: int | None = None,
        data_length: int | None = None,
        provider: str | None = None,
        last_version: int | None = None,
        timeout: float = 15.0,
    ) -> int:
        effective_provider = self._resolve_provider(provider)
        data_source = self._get_data_source(
            effective_provider,
            (symbol or self._default_symbol_for_provider(effective_provider)).strip(),
            duration_seconds or self.duration_seconds,
            (bar_mode or self.bar_mode).strip() or "time",
            range_ticks or self.range_ticks,
            brick_length or self.brick_length,
            data_length or self.data_length,
        )
        return data_source.wait_for_update(last_version, timeout)

    def _load_contracts(self, provider: str) -> list[dict[str, Any]]:
        cached = self._contracts_by_provider.get(provider)
        if cached is not None:
            return cached

        if provider == BINANCE_PROVIDER:
            try:
                contracts = load_binance_contract_catalog(self.project_root)
            except Exception:
                contracts = []
        elif provider == BITGET_PROVIDER:
            try:
                contracts = load_bitget_contract_catalog(self.project_root)
            except Exception:
                contracts = []
        else:
            contracts = []

        if not any(item["symbol"] == self.symbol for item in contracts):
            contracts = [
                {
                    "symbol": self.symbol,
                    "name": self.symbol,
                    "label": format_contract_label(self.symbol),
                    "exchange_id": "",
                    "product_id": "",
                },
                *contracts,
            ]
        self._contracts_by_provider[provider] = contracts
        return contracts

    def _symbol_label(self, provider: str, symbol: str) -> str:
        contract_map = {item["symbol"]: item for item in self._load_contracts(provider)}
        contract = contract_map.get(symbol)
        if contract:
            return str(contract["label"])
        return format_contract_label(symbol)

    def _get_data_source(
        self,
        provider: str,
        symbol: str,
        duration_seconds: int,
        bar_mode: str,
        range_ticks: int,
        brick_length: int,
        data_length: int,
    ) -> DataSource:
        if not symbol:
            raise ValueError("合约不能为空。")
        if bar_mode not in {item["id"] for item in DEFAULT_BAR_MODES}:
            raise ValueError(f"未知图表类型: {bar_mode}")
        if duration_seconds <= 0:
            raise ValueError("周期必须大于 0 秒。")
        if range_ticks <= 0:
            raise ValueError("Range Tick 必须大于 0。")
        if brick_length <= 0:
            raise ValueError("Brick Length 必须大于 0。")
        if data_length <= 0:
            raise ValueError("Data Length 必须大于 0。")

        key = (provider, symbol, duration_seconds, bar_mode, range_ticks, brick_length, data_length)
        with self._source_lock:
            data_source = self._data_sources.get(key)
            if data_source is None:
                data_source = create_data_source(
                    provider=provider,
                    symbol=symbol,
                    duration_seconds=duration_seconds,
                    data_length=data_length,
                    brick_length=brick_length,
                    refresh_ms=self.refresh_ms,
                    bar_mode=bar_mode,
                    range_ticks=range_ticks,
                )
                data_source.start()
                self._data_sources[key] = data_source
            return data_source

    def _resolve_provider(self, provider: str | None) -> str:
        candidate = (provider or BINANCE_PROVIDER).strip().lower()
        available = set(get_available_data_sources())
        if candidate not in available:
            raise ValueError(f"未知数据源: {candidate}，当前支持: {', '.join(sorted(available))}")
        return candidate

    def _default_symbol_for_provider(self, provider: str) -> str:
        contracts = self._load_contracts(provider)
        if contracts:
            return str(contracts[0]["symbol"])
        return self.symbol

    def _contract_detail(self, provider: str, symbol: str) -> dict[str, Any]:
        contracts = self._load_contracts(provider)
        contract = next((item for item in contracts if item["symbol"] == symbol), None)
        if not contract:
            return {}
        return dict(contract)

    def _provider_account(self, provider: str) -> dict[str, Any]:
        if provider == BINANCE_PROVIDER:
            try:
                return load_binance_account_summary(self.project_root)
            except Exception:
                return {}
        if provider == BITGET_PROVIDER:
            try:
                return load_bitget_account_summary(self.project_root)
            except Exception:
                return {}
        return {}

    @staticmethod
    def _provider_hint(provider: str) -> str:
        if provider == BINANCE_PROVIDER:
            return "当前使用 Binance USD-M Futures 公共行情，K 线口径为官方 MARKET 成交价。浏览器只连接本机后端；后端通过 Binance REST 初始化历史 K 线，并通过 Binance WebSocket /market 更新当前 K 线。"
        if provider == BITGET_PROVIDER:
            return "当前使用 Bitget USDT-FUTURES 公共行情，K 线口径默认是官方 MARKET 成交价。浏览器只连接本机后端；后端通过 Bitget REST 初始化历史 K 线，并通过 Bitget WebSocket 更新当前 K 线。"
        return ""

    def _refresh_interval_ms(self, provider: str) -> int:
        return self.refresh_ms

    @staticmethod
    def _duration_options_for_provider(provider: str) -> list[int]:
        if provider == BINANCE_PROVIDER:
            return [seconds for seconds in DEFAULT_DURATION_OPTIONS if seconds in BINANCE_INTERVAL_MAP]
        if provider == BITGET_PROVIDER:
            return [seconds for seconds in DEFAULT_DURATION_OPTIONS if seconds in BITGET_GRANULARITY_MAP]
        return DEFAULT_DURATION_OPTIONS

    @staticmethod
    def _bar_modes_for_provider(provider: str) -> list[dict[str, Any]]:
        if provider in {BINANCE_PROVIDER, BITGET_PROVIDER}:
            return [item for item in DEFAULT_BAR_MODES if item["id"] == "time"]
        return DEFAULT_BAR_MODES

    @staticmethod
    def _with_chart_time(bars: pd.DataFrame, bar_mode: str) -> pd.DataFrame:
        normalized = bars.copy()
        if bar_mode == "time":
            adjusted_times = [int(pd.Timestamp(dt).timestamp()) for dt in normalized["datetime"].tolist()]
        else:
            base_times = [int(pd.Timestamp(dt).timestamp()) for dt in normalized["datetime"].tolist()]
            if base_times:
                start_time = int(base_times[0])
                adjusted_times = [start_time + index for index in range(len(base_times))]
            else:
                adjusted_times = []
        normalized["time"] = adjusted_times
        datetimes = pd.to_datetime(normalized["datetime"], utc=True, errors="coerce")
        normalized["display_time"] = datetimes.dt.tz_convert(DISPLAY_TIMEZONE).dt.strftime("%Y-%m-%d %H:%M:%S")
        return normalized

    @staticmethod
    def _serialize_candles(df: pd.DataFrame) -> list[dict[str, Any]]:
        times = pd.to_numeric(df["time"], errors="coerce").fillna(0).astype(int).tolist()
        opens = pd.to_numeric(df["open"], errors="coerce").tolist()
        highs = pd.to_numeric(df["high"], errors="coerce").tolist()
        lows = pd.to_numeric(df["low"], errors="coerce").tolist()
        closes = pd.to_numeric(df["close"], errors="coerce").tolist()
        return [
            {
                "time": time_value,
                "open": float(open_value),
                "high": float(high_value),
                "low": float(low_value),
                "close": float(close_value),
            }
            for time_value, open_value, high_value, low_value, close_value in zip(times, opens, highs, lows, closes)
        ]

    @staticmethod
    def _serialize_volume(df: pd.DataFrame) -> list[dict[str, Any]]:
        times = pd.to_numeric(df["time"], errors="coerce").fillna(0).astype(int).tolist()
        opens = pd.to_numeric(df["open"], errors="coerce").tolist()
        closes = pd.to_numeric(df["close"], errors="coerce").tolist()
        volumes = pd.to_numeric(df["volume"], errors="coerce").fillna(0).tolist()
        return [
            {
                "time": time_value,
                "value": float(volume_value),
                "color": TV_UP if close_value >= open_value else TV_DOWN,
            }
            for time_value, open_value, close_value, volume_value in zip(times, opens, closes, volumes)
        ]

    @staticmethod
    def _serialize_time_labels(df: pd.DataFrame) -> dict[str, str]:
        times = pd.to_numeric(df["time"], errors="coerce").fillna(0).astype(int).tolist()
        labels = df["display_time"].astype(str).tolist()
        return {str(time_value): label for time_value, label in zip(times, labels)}

    @staticmethod
    def _serialize_indicator(result: IndicatorResult) -> dict[str, Any]:
        return {
            "id": result.id,
            "name": result.name,
            "pane": result.pane,
            "series": [
                {
                    "id": series.id,
                    "name": series.name,
                    "pane": series.pane,
                    "series_type": series.series_type,
                    "data": series.data,
                    "options": series.options,
                }
                for series in result.series
            ],
        }
