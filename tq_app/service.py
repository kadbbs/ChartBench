from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
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
from tq_app.data_sources.tianqin import (
    TIANQIN_MAX_DURATION_SECONDS,
    load_tianqin_account_summary,
    load_tianqin_contract_catalog,
)
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
DATA_SOURCE_IDLE_TTL_SECONDS = 10 * 60
DATA_SOURCE_MAX_COUNT = 8
SNAPSHOT_CACHE_MAX_ITEMS = 64
SNAPSHOT_DELTA_TAIL_POINTS = 3
PROVIDER_ACCOUNT_CACHE_TTL_SECONDS = 30.0
TIANQIN_CONTRACT_CACHE_TTL_SECONDS = 5 * 60.0
DISPLAY_TIMEZONE = ZoneInfo("Asia/Shanghai")
BITGET_PROVIDER = "bitget"
BINANCE_PROVIDER = "binance"
TIANQIN_PROVIDER = "tianqin"


@dataclass(slots=True)
class SnapshotCacheEntry:
    version: int
    snapshot: dict[str, Any]


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
        self._data_source_last_used: dict[tuple[str, str, int, str, int, int, int], float] = {}
        self._snapshot_cache_lock = threading.Lock()
        self._snapshot_cache_condition = threading.Condition(self._snapshot_cache_lock)
        self._snapshot_cache: dict[tuple[Any, ...], SnapshotCacheEntry] = {}
        self._snapshot_building: set[tuple[Any, ...]] = set()
        self._metadata_lock = threading.Lock()
        self._metadata_condition = threading.Condition(self._metadata_lock)
        self._contracts_by_provider: dict[str, list[dict[str, Any]]] = {}
        self._contract_maps_by_provider: dict[str, dict[str, dict[str, Any]]] = {}
        self._contract_cache_expires_at: dict[str, float] = {}
        self._contract_refreshing: set[str] = set()
        self._provider_account_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._provider_account_refreshing: set[str] = set()
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
            self._data_source_last_used.clear()
        with self._snapshot_cache_lock:
            self._snapshot_cache.clear()
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
        cache_key = self._snapshot_context_key(
            provider=effective_provider,
            symbol=effective_symbol,
            duration_seconds=effective_duration,
            bar_mode=effective_bar_mode,
            range_ticks=effective_range_ticks,
            brick_length=effective_brick_length,
            data_length=effective_data_length,
            indicator_ids=selected,
            indicator_params=all_params,
        )
        while True:
            bars, source_status = data_source.get_bars_with_status()
            source_version = int(source_status.get("version") or 0)
            cached_snapshot = self._cached_snapshot(cache_key, source_version, source_status)
            if cached_snapshot is not None:
                return cached_snapshot
            if not self._claim_snapshot_build(cache_key, source_version):
                continue

            try:
                normalized = self._with_chart_time(bars, effective_bar_mode)

                results: list[IndicatorResult] = []
                for indicator_id in selected:
                    indicator = self.indicators.get(indicator_id)
                    resolved_params = indicator.resolve_params(all_params.get(indicator_id))
                    results.append(indicator.build(normalized, resolved_params))

                last_close = float(normalized.iloc[-1]["close"])
                prev_close = float(normalized.iloc[-2]["close"]) if len(normalized) > 1 else last_close

                snapshot = {
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
                    "stream": dict(source_status),
                }
                self._store_snapshot_cache(cache_key, source_version, snapshot)
                return self._snapshot_for_response(snapshot, source_status)
            finally:
                self._release_snapshot_build(cache_key)

    def build_snapshot_delta(self, previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any] | None:
        if not self._same_snapshot_context(previous, current):
            return None
        stream = current.get("stream") if isinstance(current.get("stream"), dict) else {}
        previous_stream = previous.get("stream") if isinstance(previous.get("stream"), dict) else {}
        tail_times = self._snapshot_tail_times(current)
        return {
            "provider": current.get("provider"),
            "symbol": current.get("symbol"),
            "symbol_label": current.get("symbol_label"),
            "duration_seconds": current.get("duration_seconds"),
            "bar_mode": current.get("bar_mode"),
            "range_ticks": current.get("range_ticks"),
            "brick_length": current.get("brick_length"),
            "data_length": current.get("data_length"),
            "refresh_ms": current.get("refresh_ms"),
            "last_close": current.get("last_close"),
            "last_color": current.get("last_color"),
            "last_time": current.get("last_time"),
            "stream": stream,
            "base_version": int(previous_stream.get("version") or 0),
            "version": int(stream.get("version") or 0),
            "time_labels": {
                key: value
                for key, value in (current.get("time_labels") or {}).items()
                if int(key) in tail_times
            },
            "candles": self._tail_points(current.get("candles") or []),
            "volume": self._tail_points(current.get("volume") or []),
            "indicators": [
                self._indicator_delta(indicator, tail_times)
                for indicator in (current.get("indicators") or [])
                if isinstance(indicator, dict)
            ],
        }

    @staticmethod
    def _same_snapshot_context(previous: dict[str, Any], current: dict[str, Any]) -> bool:
        keys = (
            "provider",
            "symbol",
            "duration_seconds",
            "bar_mode",
            "range_ticks",
            "brick_length",
            "data_length",
        )
        return all(previous.get(key) == current.get(key) for key in keys)

    @staticmethod
    def _tail_points(points: list[dict[str, Any]], count: int = SNAPSHOT_DELTA_TAIL_POINTS) -> list[dict[str, Any]]:
        if not isinstance(points, list):
            return []
        return [dict(point) for point in points[-count:] if isinstance(point, dict)]

    def _snapshot_tail_times(self, snapshot: dict[str, Any]) -> set[int]:
        return {
            int(point.get("time") or 0)
            for point in self._tail_points(snapshot.get("candles") or [])
            if isinstance(point, dict) and point.get("time") is not None
        }

    def _indicator_delta(self, indicator: dict[str, Any], tail_times: set[int]) -> dict[str, Any]:
        return {
            "id": indicator.get("id"),
            "name": indicator.get("name"),
            "pane": indicator.get("pane"),
            "series": [
                self._series_delta(series, tail_times)
                for series in (indicator.get("series") or [])
                if isinstance(series, dict)
            ],
        }

    def _series_delta(self, series: dict[str, Any], tail_times: set[int]) -> dict[str, Any]:
        return {
            "id": series.get("id"),
            "name": series.get("name"),
            "pane": series.get("pane"),
            "series_type": series.get("series_type"),
            "data": self._tail_points(series.get("data") or []),
            "options": self._delta_options(series.get("options") or {}, tail_times),
        }

    @staticmethod
    def _delta_options(options: dict[str, Any], tail_times: set[int]) -> dict[str, Any]:
        if not isinstance(options, dict):
            return {}
        time_filtered_keys = {"markers", "candleMarkers", "barColors"}
        delta: dict[str, Any] = {}
        for key, value in options.items():
            if key not in time_filtered_keys:
                delta[key] = value
                continue
            if not isinstance(value, list):
                continue
            filtered = [
                dict(item)
                for item in value
                if isinstance(item, dict) and int(item.get("time") or 0) in tail_times
            ]
            if filtered:
                delta[key] = filtered
        return delta

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
        with self._metadata_condition:
            cached = self._contracts_by_provider.get(provider)
            cache_is_current = (
                provider != TIANQIN_PROVIDER
                or self._contract_cache_expires_at.get(provider, 0.0) > time.monotonic()
            )
            if cached is not None and cache_is_current:
                return cached
            if provider in self._contract_refreshing:
                self._metadata_condition.wait_for(lambda: provider not in self._contract_refreshing)
                return self._contracts_by_provider.get(provider, [])
            self._contract_refreshing.add(provider)

        contracts: list[dict[str, Any]] = []
        try:
            if provider == TIANQIN_PROVIDER:
                contracts = load_tianqin_contract_catalog(self.project_root)
            elif provider == BINANCE_PROVIDER:
                contracts = load_binance_contract_catalog(self.project_root)
            elif provider == BITGET_PROVIDER:
                contracts = load_bitget_contract_catalog(self.project_root)
        except Exception:
            contracts = []

        try:
            fallback_symbol = self._fallback_symbol_for_provider(provider)
            should_include_runtime_symbol = provider == self.provider and self.symbol and not any(item["symbol"] == self.symbol for item in contracts)
            if should_include_runtime_symbol:
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
            elif not contracts and fallback_symbol:
                contracts = [self._fallback_contract(provider, fallback_symbol)]
            with self._metadata_condition:
                self._contracts_by_provider[provider] = contracts
                if provider == TIANQIN_PROVIDER:
                    self._contract_cache_expires_at[provider] = (
                        time.monotonic() + TIANQIN_CONTRACT_CACHE_TTL_SECONDS
                    )
                self._contract_maps_by_provider[provider] = {
                    str(item.get("symbol") or ""): item
                    for item in contracts
                    if item.get("symbol")
                }
        finally:
            with self._metadata_condition:
                self._contract_refreshing.discard(provider)
                self._metadata_condition.notify_all()
        return contracts

    def _symbol_label(self, provider: str, symbol: str) -> str:
        self._load_contracts(provider)
        with self._metadata_lock:
            contract = self._contract_maps_by_provider.get(provider, {}).get(symbol)
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
        stale_sources: list[DataSource] = []
        with self._source_lock:
            now = time.monotonic()
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
            self._data_source_last_used[key] = now
            stale_sources = self._pop_stale_data_sources_locked(now, keep_key=key)
        for stale_source in stale_sources:
            stale_source.stop()
        return data_source

    def _pop_stale_data_sources_locked(
        self,
        now: float,
        *,
        keep_key: tuple[str, str, int, str, int, int, int],
    ) -> list[DataSource]:
        stale_keys = [
            key
            for key, last_used in self._data_source_last_used.items()
            if key != keep_key and now - last_used > DATA_SOURCE_IDLE_TTL_SECONDS
        ]
        if len(self._data_sources) - len(stale_keys) > DATA_SOURCE_MAX_COUNT:
            candidates = sorted(
                (
                    (last_used, key)
                    for key, last_used in self._data_source_last_used.items()
                    if key != keep_key and key not in stale_keys
                ),
                key=lambda item: item[0],
            )
            overflow = len(self._data_sources) - len(stale_keys) - DATA_SOURCE_MAX_COUNT
            stale_keys.extend(key for _last_used, key in candidates[:max(overflow, 0)])

        stale_sources: list[DataSource] = []
        for stale_key in stale_keys:
            source = self._data_sources.pop(stale_key, None)
            self._data_source_last_used.pop(stale_key, None)
            if source is not None:
                stale_sources.append(source)
        if stale_sources:
            self._clear_snapshot_cache()
        return stale_sources

    def _snapshot_context_key(
        self,
        *,
        provider: str,
        symbol: str,
        duration_seconds: int,
        bar_mode: str,
        range_ticks: int,
        brick_length: int,
        data_length: int,
        indicator_ids: list[str],
        indicator_params: dict[str, dict[str, Any]],
    ) -> tuple[Any, ...]:
        params_signature = json.dumps(indicator_params, ensure_ascii=False, sort_keys=True, default=str)
        return (
            provider,
            symbol,
            duration_seconds,
            bar_mode,
            range_ticks,
            brick_length,
            data_length,
            tuple(indicator_ids),
            params_signature,
        )

    def _cached_snapshot(
        self,
        cache_key: tuple[Any, ...],
        source_version: int,
        source_status: dict[str, Any],
    ) -> dict[str, Any] | None:
        with self._snapshot_cache_condition:
            entry = self._snapshot_cache.get(cache_key)
            if entry is None or entry.version != source_version:
                return None
            self._snapshot_cache.pop(cache_key)
            self._snapshot_cache[cache_key] = entry
            snapshot = entry.snapshot
        return self._snapshot_for_response(snapshot, source_status)

    def _store_snapshot_cache(
        self,
        cache_key: tuple[Any, ...],
        source_version: int,
        snapshot: dict[str, Any],
    ) -> None:
        with self._snapshot_cache_condition:
            self._snapshot_cache.pop(cache_key, None)
            self._snapshot_cache[cache_key] = SnapshotCacheEntry(version=source_version, snapshot=snapshot)
            while len(self._snapshot_cache) > SNAPSHOT_CACHE_MAX_ITEMS:
                self._snapshot_cache.pop(next(iter(self._snapshot_cache)))

    def _claim_snapshot_build(self, cache_key: tuple[Any, ...], source_version: int) -> bool:
        with self._snapshot_cache_condition:
            if cache_key in self._snapshot_building:
                self._snapshot_cache_condition.wait_for(lambda: cache_key not in self._snapshot_building)
                return False
            entry = self._snapshot_cache.get(cache_key)
            if entry is not None and entry.version == source_version:
                return False
            self._snapshot_building.add(cache_key)
            return True

    def _release_snapshot_build(self, cache_key: tuple[Any, ...]) -> None:
        with self._snapshot_cache_condition:
            self._snapshot_building.discard(cache_key)
            self._snapshot_cache_condition.notify_all()

    @staticmethod
    def _snapshot_for_response(snapshot: dict[str, Any], source_status: dict[str, Any]) -> dict[str, Any]:
        response = dict(snapshot)
        response["stream"] = dict(source_status)
        return response

    def _clear_snapshot_cache(self) -> None:
        with self._snapshot_cache_condition:
            self._snapshot_cache.clear()

    def _resolve_provider(self, provider: str | None) -> str:
        candidate = (provider or BINANCE_PROVIDER).strip().lower()
        available = set(get_available_data_sources())
        if candidate not in available:
            raise ValueError(f"未知数据源: {candidate}，当前支持: {', '.join(sorted(available))}")
        return candidate

    def _default_symbol_for_provider(self, provider: str) -> str:
        contracts = self._load_contracts(provider)
        preferred_symbol = self._fallback_symbol_for_provider(provider)
        if preferred_symbol and any(item["symbol"] == preferred_symbol for item in contracts):
            return preferred_symbol
        if contracts:
            return str(contracts[0]["symbol"])
        return self.symbol

    def _fallback_symbol_for_provider(self, provider: str) -> str:
        if provider == TIANQIN_PROVIDER:
            if self.provider == TIANQIN_PROVIDER and self.symbol:
                return self.symbol
            return (
                os.getenv("TQ_CHART_DEFAULT_SYMBOL", "").strip()
                or os.getenv("TIANQIN_DEFAULT_SYMBOL", "").strip()
                or "KQ.m@SHFE.cu"
            )
        return os.getenv("TQ_DEFAULT_SYMBOL", "").strip().upper() or "BTCUSDT"

    @staticmethod
    def _fallback_contract(provider: str, symbol: str) -> dict[str, Any]:
        exchange_id = {
            BINANCE_PROVIDER: "BINANCE",
            BITGET_PROVIDER: "BITGET",
            TIANQIN_PROVIDER: "TIANQIN",
        }.get(provider, "")
        product_id = {
            BINANCE_PROVIDER: "USD-M",
            BITGET_PROVIDER: "USDT-FUTURES",
            TIANQIN_PROVIDER: "TQSDK",
        }.get(provider, "")
        return {
            "symbol": symbol,
            "name": symbol,
            "label": format_contract_label(symbol),
            "exchange_id": exchange_id,
            "product_id": product_id,
        }

    def _contract_detail(self, provider: str, symbol: str) -> dict[str, Any]:
        self._load_contracts(provider)
        with self._metadata_lock:
            contract = self._contract_maps_by_provider.get(provider, {}).get(symbol)
        if not contract:
            return {}
        return dict(contract)

    def _provider_account(self, provider: str) -> dict[str, Any]:
        now = time.monotonic()
        with self._metadata_condition:
            cached = self._provider_account_cache.get(provider)
            if cached is not None and cached[0] > now:
                return dict(cached[1])
            if provider in self._provider_account_refreshing:
                self._metadata_condition.wait_for(lambda: provider not in self._provider_account_refreshing)
                cached = self._provider_account_cache.get(provider)
                return dict(cached[1]) if cached is not None else {}
            self._provider_account_refreshing.add(provider)

        account: dict[str, Any] = {}
        try:
            if provider == TIANQIN_PROVIDER:
                account = load_tianqin_account_summary(self.project_root)
            elif provider == BINANCE_PROVIDER:
                account = load_binance_account_summary(self.project_root)
            elif provider == BITGET_PROVIDER:
                account = load_bitget_account_summary(self.project_root)
        except Exception:
            account = {}
        finally:
            with self._metadata_condition:
                self._provider_account_cache[provider] = (
                    time.monotonic() + PROVIDER_ACCOUNT_CACHE_TTL_SECONDS,
                    dict(account),
                )
                self._provider_account_refreshing.discard(provider)
                self._metadata_condition.notify_all()
        return dict(account)

    @staticmethod
    def _provider_hint(provider: str) -> str:
        if provider == TIANQIN_PROVIDER:
            return "当前使用天勤量化 TqSdk 行情。后端通过 TqApi.get_kline_serial 订阅 K 线，并通过 wait_update 驱动实时刷新；需要在 .env 配置 TIANQIN_USERNAME / TIANQIN_PASSWORD。"
        if provider == BINANCE_PROVIDER:
            return "当前使用 Binance USD-M Futures 公共行情，K 线口径为官方 MARKET 成交价。浏览器只连接本机后端；后端通过 Binance REST 初始化历史 K 线，并通过 Binance WebSocket /market 更新当前 K 线。"
        if provider == BITGET_PROVIDER:
            return "当前使用 Bitget USDT-FUTURES 公共行情，K 线口径默认是官方 MARKET 成交价。浏览器只连接本机后端；后端通过 Bitget REST 初始化历史 K 线，并通过 Bitget WebSocket 更新当前 K 线。"
        return ""

    def _refresh_interval_ms(self, provider: str) -> int:
        return self.refresh_ms

    @staticmethod
    def _duration_options_for_provider(provider: str) -> list[int]:
        if provider == TIANQIN_PROVIDER:
            return [seconds for seconds in DEFAULT_DURATION_OPTIONS if seconds <= TIANQIN_MAX_DURATION_SECONDS]
        if provider == BINANCE_PROVIDER:
            return [seconds for seconds in DEFAULT_DURATION_OPTIONS if seconds in BINANCE_INTERVAL_MAP]
        if provider == BITGET_PROVIDER:
            return [seconds for seconds in DEFAULT_DURATION_OPTIONS if seconds in BITGET_GRANULARITY_MAP]
        return DEFAULT_DURATION_OPTIONS

    @staticmethod
    def _bar_modes_for_provider(provider: str) -> list[dict[str, Any]]:
        if provider in {TIANQIN_PROVIDER, BINANCE_PROVIDER, BITGET_PROVIDER}:
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
