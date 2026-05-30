from __future__ import annotations

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
    ) -> None:
        self.symbol = symbol.upper()
        self.provider = provider
        self.duration_seconds = duration_seconds
        self.indicator_ids = indicator_ids
        self.registry = build_indicator_registry(project_root)

    def build_full(self, bars: pd.DataFrame) -> dict[str, Any]:
        normalized = normalize_bars(bars)
        indicators: list[IndicatorResult] = []
        for indicator_id in self.indicator_ids:
            indicator = self.registry.get(indicator_id)
            indicators.append(indicator.build(normalized, indicator.resolve_params(None)))
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
