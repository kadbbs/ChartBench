from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import threading
from typing import Any

import pandas as pd

from tq_app.contracts import format_contract_label, load_bitget_contract_catalog, load_duckdb_contract_catalog
from tq_app.data_sources.bitget import load_bitget_account_summary
from tq_app.data_sources import DataSource, create_data_source, get_available_data_sources
from tq_app.data_sources.bitget import GRANULARITY_MAP
from tq_app.indicators import build_indicator_registry
from tq_app.models import IndicatorMeta, IndicatorResult

TV_UP = "#089981"
TV_DOWN = "#f23645"
DEFAULT_DURATION_OPTIONS = [60, 300, 900, 1800, 3600, 86400]
DEFAULT_BAR_MODES = [
    {"id": "time", "label": "时间 K 线"},
    {"id": "tick", "label": "Tick 图"},
    {"id": "range", "label": "Range Bar"},
    {"id": "renko", "label": "Renko"},
]
DEFAULT_RANGE_TICKS = 10
DEFAULT_BRICK_LENGTH = 10000


def _contract_has_local_data(contract: dict[str, Any] | None) -> bool:
    if not contract:
        return False
    fields = ("tick_count", "bar_1m_count", "bar_5m_count", "bar_10m_count", "bar_15m_count")
    return any(int(contract.get(field, 0) or 0) > 0 for field in fields)


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
        self.provider = provider
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

    def get_config(self, provider: str | None = None) -> dict[str, Any]:
        effective_provider = self._resolve_provider(provider)
        contracts = self._load_contracts(effective_provider)
        default_symbol = self._default_symbol_for_provider(effective_provider)
        selected_symbol = default_symbol
        current_contract = next((item for item in contracts if item["symbol"] == self.symbol), None)
        if current_contract is not None:
            if effective_provider != "duckdb" or _contract_has_local_data(current_contract):
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
        need_pseudo_orderflow = any(
            indicator_id in selected for indicator_id in {"pseudo_orderflow_5m", "orderflow_gl", "spqrc_signals", "spqrc_panel"}
        )

        data_source = self._get_data_source(
            effective_provider,
            effective_symbol,
            effective_duration,
            effective_bar_mode,
            effective_range_ticks,
            effective_brick_length,
            effective_data_length,
        )
        data_source.configure(enable_pseudo_orderflow=need_pseudo_orderflow)
        bars = data_source.get_bars()
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
            "last_time": normalized.iloc[-1]["datetime"].strftime("%Y-%m-%d %H:%M:%S"),
        }

    def _load_contracts(self, provider: str) -> list[dict[str, Any]]:
        cached = self._contracts_by_provider.get(provider)
        if cached is not None:
            return cached

        if provider == "bitget":
            try:
                contracts = load_bitget_contract_catalog(self.project_root)
            except Exception:
                contracts = []
        elif provider == "duckdb":
            try:
                contracts = load_duckdb_contract_catalog(self.project_root)
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
        candidate = (provider or self.provider).strip()
        if candidate not in get_available_data_sources():
            names = ", ".join(get_available_data_sources())
            raise ValueError(f"未知数据源: {candidate}，可选值: {names}")
        return candidate

    def _default_symbol_for_provider(self, provider: str) -> str:
        contracts = self._load_contracts(provider)
        if provider == "duckdb":
            first_with_data = next((item for item in contracts if _contract_has_local_data(item)), None)
            if first_with_data:
                return str(first_with_data["symbol"])
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
        if provider == "bitget":
            try:
                return load_bitget_account_summary(self.project_root)
            except Exception:
                return {}
        return {}

    @staticmethod
    def _provider_hint(provider: str) -> str:
        if provider == "duckdb":
            return "当前使用本地 DuckDB 回放库。系统会优先使用最接近的本地现成数据源；例如 5 分钟 K 线会优先读取 market_bars_5m，缺失时再回退到本地 tick 重建。"
        if provider == "bitget":
            return "当前使用 Bitget 公共行情。后端通过 WebSocket 订阅实时 K 线，页面按短周期读取最新缓存，不包含交易下单。"
        return ""

    def _refresh_interval_ms(self, provider: str) -> int:
        if provider == "duckdb":
            return 0
        return self.refresh_ms

    @staticmethod
    def _duration_options_for_provider(provider: str) -> list[int]:
        if provider == "bitget":
            return [seconds for seconds in DEFAULT_DURATION_OPTIONS if seconds in GRANULARITY_MAP]
        return DEFAULT_DURATION_OPTIONS

    @staticmethod
    def _bar_modes_for_provider(provider: str) -> list[dict[str, Any]]:
        if provider == "bitget":
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
        normalized["display_time"] = normalized["datetime"].dt.strftime("%Y-%m-%d %H:%M:%S")
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
