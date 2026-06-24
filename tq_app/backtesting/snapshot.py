from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pandas as pd

from tq_app.indicators import build_indicator_registry
from tq_app.models import IndicatorResult
from tq_app.service import DISPLAY_TIMEZONE, TV_DOWN, TV_UP


class SnapshotBuilder:
    def __init__(
        self,
        *,
        project_root: Path,
        symbol: str,
        provider: str,
        duration_seconds: int,
        indicator_ids: list[str],
        indicator_params: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.symbol = symbol.upper()
        self.provider = provider
        self.duration_seconds = duration_seconds
        self.indicator_ids = indicator_ids
        self.indicator_params = indicator_params or {}
        self.registry = build_indicator_registry(project_root)

    def build_full(self, bars: pd.DataFrame) -> dict[str, Any]:
        normalized = normalize_bars(bars)
        indicators: list[IndicatorResult] = []
        for indicator_id in self.indicator_ids:
            indicator = self.registry.get(indicator_id)
            indicators.append(indicator.build(normalized, indicator.resolve_params(self.indicator_params.get(indicator_id))))
        return {
            "symbol": self.symbol,
            "provider": self.provider,
            "duration_seconds": self.duration_seconds,
            "bar_mode": "time",
            "data_length": len(normalized),
            "time_labels": serialize_time_labels(normalized),
            "candles": serialize_candles(normalized),
            "volume": serialize_volume(normalized),
            "indicators": [serialize_indicator(item) for item in indicators],
            "last_close": float(normalized.iloc[-1]["close"]) if not normalized.empty else None,
        }


def slice_snapshot(snapshot: dict[str, Any], end_exclusive: int) -> dict[str, Any]:
    sliced = dict(snapshot)
    candles = list(snapshot.get("candles") or [])[:end_exclusive]
    allowed_times = {int(item["time"]) for item in candles}
    sliced["candles"] = candles
    sliced["volume"] = [item for item in snapshot.get("volume") or [] if int(item.get("time") or 0) in allowed_times]
    sliced["time_labels"] = {key: value for key, value in (snapshot.get("time_labels") or {}).items() if int(key) in allowed_times}
    sliced["indicators"] = [_slice_indicator(item, allowed_times) for item in snapshot.get("indicators") or []]
    sliced["data_length"] = len(candles)
    sliced["last_close"] = candles[-1]["close"] if candles else None
    return sliced


def attach_higher_timeframe(
    snapshot: dict[str, Any],
    htf_snapshot: dict[str, Any] | None,
    current_time: int,
) -> dict[str, Any]:
    if htf_snapshot is None:
        return snapshot
    htf_candles = [item for item in htf_snapshot.get("candles") or [] if int(item.get("time") or 0) <= current_time]
    htf = slice_snapshot(htf_snapshot, len(htf_candles))
    snapshot["higher_timeframe"] = htf
    return snapshot


class BacktestSnapshotSlicer:
    def __init__(self, snapshot: dict[str, Any], *, max_bars: int) -> None:
        self.snapshot = snapshot
        self.max_bars = max(int(max_bars), 2)
        self.candles = list(snapshot.get("candles") or [])
        self.candle_times = [int(item.get("time") or 0) for item in self.candles]
        self.volume = list(snapshot.get("volume") or [])
        self.volume_times = [int(item.get("time") or 0) for item in self.volume]
        self.time_labels = dict(snapshot.get("time_labels") or {})
        self.indicators = [_CachedIndicator(item) for item in snapshot.get("indicators") or []]

    def slice(self, end_exclusive: int) -> dict[str, Any]:
        end = min(max(int(end_exclusive), 0), len(self.candles))
        start = max(0, end - self.max_bars)
        candles = self.candles[start:end]
        sliced = dict(self.snapshot)
        sliced["candles"] = candles
        sliced["data_length"] = len(candles)
        sliced["last_close"] = candles[-1]["close"] if candles else None
        if not candles:
            sliced["volume"] = []
            sliced["time_labels"] = {}
            sliced["indicators"] = []
            return sliced

        start_time = int(candles[0]["time"])
        end_time = int(candles[-1]["time"])
        volume_start = bisect_left(self.volume_times, start_time)
        volume_end = bisect_right(self.volume_times, end_time)
        sliced["volume"] = self.volume[volume_start:volume_end]
        sliced["time_labels"] = {str(time_value): self.time_labels.get(str(time_value), "") for time_value in self.candle_times[start:end]}
        sliced["indicators"] = [indicator.slice(start_time, end_time) for indicator in self.indicators]
        return sliced

    def slice_until_time(self, current_time: int) -> dict[str, Any]:
        return self.slice(bisect_right(self.candle_times, int(current_time)))


class _CachedIndicator:
    def __init__(self, indicator: dict[str, Any]) -> None:
        self.template = indicator
        self.series = [_CachedSeries(item) for item in indicator.get("series") or []]

    def slice(self, start_time: int, end_time: int) -> dict[str, Any]:
        indicator = dict(self.template)
        indicator["series"] = [series.slice(start_time, end_time) for series in self.series]
        return indicator


class _CachedSeries:
    def __init__(self, series: dict[str, Any]) -> None:
        self.template = series
        self.data = list(series.get("data") or [])
        self.data_times = [int(item.get("time") or 0) for item in self.data]
        options = series.get("options") or {}
        self.marker_keys = [key for key in ("candleMarkers", "markers") if key in options]
        self.markers = {key: list(options.get(key) or []) for key in self.marker_keys}
        self.marker_times = {key: [int(item.get("time") or 0) for item in self.markers[key]] for key in self.marker_keys}

    def slice(self, start_time: int, end_time: int) -> dict[str, Any]:
        series = dict(self.template)
        data_start = bisect_left(self.data_times, start_time)
        data_end = bisect_right(self.data_times, end_time)
        series["data"] = self.data[data_start:data_end]
        if self.marker_keys:
            options = dict(series.get("options") or {})
            for key in self.marker_keys:
                marker_start = bisect_left(self.marker_times[key], start_time)
                marker_end = bisect_right(self.marker_times[key], end_time)
                options[key] = self.markers[key][marker_start:marker_end]
            series["options"] = options
        return series


def normalize_bars(bars: pd.DataFrame) -> pd.DataFrame:
    normalized = bars.copy().reset_index(drop=True)
    datetimes = pd.to_datetime(normalized["datetime"], utc=True, errors="coerce")
    normalized["datetime"] = datetimes
    normalized["time"] = [int(item.timestamp()) for item in datetimes]
    normalized["display_time"] = datetimes.dt.tz_convert(DISPLAY_TIMEZONE).dt.strftime("%Y-%m-%d %H:%M:%S")
    return normalized


def serialize_candles(df: pd.DataFrame) -> list[dict[str, Any]]:
    return [
        {
            "time": int(row.time),
            "open": float(row.open),
            "high": float(row.high),
            "low": float(row.low),
            "close": float(row.close),
        }
        for row in df[["time", "open", "high", "low", "close"]].itertuples(index=False)
    ]


def serialize_volume(df: pd.DataFrame) -> list[dict[str, Any]]:
    return [
        {
            "time": int(row.time),
            "value": float(row.volume),
            "color": TV_UP if float(row.close) >= float(row.open) else TV_DOWN,
        }
        for row in df[["time", "open", "close", "volume"]].itertuples(index=False)
    ]


def serialize_time_labels(df: pd.DataFrame) -> dict[str, str]:
    return {str(int(row.time)): str(row.display_time) for row in df[["time", "display_time"]].itertuples(index=False)}


def serialize_indicator(result: IndicatorResult) -> dict[str, Any]:
    return {
        "id": result.id,
        "name": result.name,
        "pane": result.pane,
        "series": [asdict(series) for series in result.series],
    }


def _slice_indicator(indicator: dict[str, Any], allowed_times: set[int]) -> dict[str, Any]:
    sliced = dict(indicator)
    series_items = []
    for series in indicator.get("series") or []:
        item = dict(series)
        item["data"] = [point for point in series.get("data") or [] if int(point.get("time") or 0) in allowed_times]
        options = dict(series.get("options") or {})
        if "candleMarkers" in options:
            options["candleMarkers"] = [
                marker for marker in options.get("candleMarkers") or [] if int(marker.get("time") or 0) in allowed_times
            ]
        if "markers" in options:
            options["markers"] = [marker for marker in options.get("markers") or [] if int(marker.get("time") or 0) in allowed_times]
        item["options"] = options
        series_items.append(item)
    sliced["series"] = series_items
    return sliced
