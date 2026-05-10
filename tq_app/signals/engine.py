from __future__ import annotations

import importlib.util
import logging
import os
import time
from pathlib import Path
from typing import Any

from tq_app.actions import ActionExecutor
from tq_app.signals.models import SignalCallback, SignalEvent

LOGGER = logging.getLogger("tq_app.signals")
DEFAULT_CALLBACKS_FILE = "signal_callbacks.py"


class CallbackRegistry:
    def __init__(self) -> None:
        self.callbacks: list[SignalCallback] = []

    def on_snapshot(
        self,
        *,
        id: str,
        callback: Any,
        name: str | None = None,
        actions: list[dict[str, Any]] | None = None,
        enabled: bool = True,
        once_per_bar: bool = True,
        cooldown_seconds: float = 0.0,
    ) -> None:
        callback_id = str(id).strip()
        if not callback_id:
            raise ValueError("callback id is required")
        if not callable(callback):
            raise TypeError(f"callback {callback_id!r} must be callable")
        self.callbacks.append(
            SignalCallback(
                id=callback_id,
                name=str(name or callback_id),
                handler=callback,
                actions=[item for item in actions or [] if isinstance(item, dict)],
                enabled=bool(enabled),
                once_per_bar=bool(once_per_bar),
                cooldown_seconds=max(float(cooldown_seconds or 0), 0.0),
            )
        )


class SignalContext:
    def __init__(self, snapshot: dict[str, Any], callback: SignalCallback) -> None:
        self.snapshot = snapshot
        self.callback = callback

    @property
    def symbol(self) -> str:
        return str(self.snapshot.get("symbol") or "")

    @property
    def provider(self) -> str:
        return str(self.snapshot.get("provider") or "")

    @property
    def duration_seconds(self) -> int:
        return int(self.snapshot.get("duration_seconds") or 0)

    @property
    def last_price(self) -> float | None:
        return _optional_float(self.snapshot.get("last_close"))

    def candles(self) -> list[dict[str, Any]]:
        return [item for item in self.snapshot.get("candles") or [] if isinstance(item, dict)]

    def last_bar_time(self) -> int:
        candles = self.candles()
        if not candles:
            return 0
        return int(candles[-1].get("time") or 0)

    def display_time(self, bar_time: int | None = None) -> str | None:
        resolved_bar_time = self.last_bar_time() if bar_time is None else int(bar_time or 0)
        return (self.snapshot.get("time_labels") or {}).get(str(resolved_bar_time)) or self.snapshot.get("last_time")

    def indicator_series(self, indicator_id: str, series_id: str) -> dict[str, Any] | None:
        for indicator in self.snapshot.get("indicators") or []:
            if str(indicator.get("id")) != indicator_id:
                continue
            for series in indicator.get("series") or []:
                if str(series.get("id")) == series_id:
                    return series
        return None

    def series_points(self, indicator_id: str, series_id: str, *, with_value: bool = True) -> list[dict[str, Any]]:
        series = self.indicator_series(indicator_id, series_id)
        if series is None:
            return []
        points = [point for point in series.get("data") or [] if isinstance(point, dict)]
        if with_value:
            return [point for point in points if "value" in point]
        return points

    def latest_marker(
        self,
        indicator_id: str,
        series_id: str,
        text: str | None = None,
        *,
        lookback_bars: int = 1,
    ) -> dict[str, Any] | None:
        series = self.indicator_series(indicator_id, series_id)
        if series is None:
            return None
        candidate_times = {
            int(item.get("time"))
            for item in self.candles()[-max(int(lookback_bars or 1), 1) :]
            if item.get("time") is not None
        }
        if not candidate_times:
            return None

        matches: list[dict[str, Any]] = []
        expected_text = None if text is None else str(text).strip()
        for marker in (series.get("options") or {}).get("markers") or []:
            if not isinstance(marker, dict):
                continue
            marker_time = marker.get("time")
            if marker_time is None or int(marker_time) not in candidate_times:
                continue
            if expected_text is not None and str(marker.get("text", "")).strip() != expected_text:
                continue
            matches.append(dict(marker))
        if not matches:
            return None
        return max(matches, key=lambda item: int(item.get("time") or 0))

    def signal(
        self,
        *,
        condition_type: str,
        bar_time: int | None = None,
        price: float | None = None,
        display_time: str | None = None,
        payload: dict[str, Any] | None = None,
        actions: list[dict[str, Any]] | None = None,
        id_suffix: str | None = None,
    ) -> SignalEvent:
        resolved_bar_time = self.last_bar_time() if bar_time is None else int(bar_time or 0)
        suffix = f":{id_suffix}" if id_suffix else ""
        event_id = f"{self.symbol}:{self.duration_seconds}:{self.callback.id}:{resolved_bar_time}{suffix}"
        return SignalEvent(
            id=event_id,
            rule_id=self.callback.id,
            rule_name=self.callback.name,
            symbol=self.symbol,
            provider=self.provider,
            duration_seconds=self.duration_seconds,
            bar_time=resolved_bar_time,
            display_time=display_time if display_time is not None else self.display_time(resolved_bar_time),
            price=self.last_price if price is None else price,
            condition_type=str(condition_type),
            payload=payload or {},
            actions=actions or [],
        )


class SignalEngine:
    def __init__(self, project_root: Path, executor: ActionExecutor | None = None) -> None:
        self.project_root = project_root
        self.executor = executor or ActionExecutor(dry_run=True)
        self.callbacks = self._load_callbacks()
        self._fired_keys: set[str] = set()
        self._last_fire_at_by_callback: dict[str, float] = {}

    def describe(self) -> dict[str, Any]:
        return {
            "enabled": bool(self.callbacks),
            "callback_count": len(self.callbacks),
            "callbacks": [
                {
                    "id": item.id,
                    "name": item.name,
                    "enabled": item.enabled,
                    "actions": item.actions,
                    "once_per_bar": item.once_per_bar,
                    "cooldown_seconds": item.cooldown_seconds,
                }
                for item in self.callbacks
            ],
        }

    def evaluate(self, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        if not self.callbacks:
            return []

        events: list[dict[str, Any]] = []
        for callback in self.callbacks:
            if not callback.enabled or not self._cooldown_allows(callback):
                continue
            context = SignalContext(snapshot, callback)
            try:
                callback_events = self._normalize_events(context, callback.handler(context))
            except Exception as exc:
                LOGGER.exception("signal callback %s failed: %s", callback.id, exc)
                continue

            for event in callback_events:
                fire_key = event.id or f"{event.symbol}:{event.duration_seconds}:{callback.id}:{event.bar_time}"
                if callback.once_per_bar and fire_key in self._fired_keys:
                    continue
                action_configs = event.actions or callback.actions or [{"type": "log"}]
                event.actions = self.executor.execute(event, action_configs)
                self._fired_keys.add(fire_key)
                self._last_fire_at_by_callback[callback.id] = time.time()
                events.append(event.to_dict())
        return events

    def _cooldown_allows(self, callback: SignalCallback) -> bool:
        if callback.cooldown_seconds <= 0:
            return True
        last_fire_at = self._last_fire_at_by_callback.get(callback.id)
        return last_fire_at is None or time.time() - last_fire_at >= callback.cooldown_seconds

    def _normalize_events(self, context: SignalContext, result: Any) -> list[SignalEvent]:
        callback = context.callback
        if result in (None, False):
            return []
        if isinstance(result, SignalEvent):
            return [result]
        if isinstance(result, dict):
            return [self._event_from_dict(context, result)]
        if isinstance(result, list) or isinstance(result, tuple):
            events: list[SignalEvent] = []
            for item in result:
                if item in (None, False):
                    continue
                if isinstance(item, SignalEvent):
                    events.append(item)
                elif isinstance(item, dict):
                    events.append(self._event_from_dict(context, item))
                else:
                    LOGGER.warning("signal callback %s returned unsupported item %r", callback.id, type(item).__name__)
            return events
        LOGGER.warning("signal callback %s returned unsupported value %r", callback.id, type(result).__name__)
        return []

    def _event_from_dict(self, context: SignalContext, payload: dict[str, Any]) -> SignalEvent:
        callback = context.callback
        bar_time = int(payload.get("bar_time") or context.last_bar_time())
        event_id = str(payload.get("id") or f"{context.symbol}:{context.duration_seconds}:{callback.id}:{bar_time}")
        return SignalEvent(
            id=event_id,
            rule_id=callback.id,
            rule_name=callback.name,
            symbol=str(payload.get("symbol") or context.symbol),
            provider=str(payload.get("provider") or context.provider),
            duration_seconds=int(payload.get("duration_seconds") or context.duration_seconds),
            bar_time=bar_time,
            display_time=payload.get("display_time") or context.display_time(bar_time),
            price=_optional_float(payload.get("price")) if "price" in payload else context.last_price,
            condition_type=str(payload.get("condition_type") or callback.id),
            payload=payload.get("payload") if isinstance(payload.get("payload"), dict) else {},
            actions=payload.get("actions") if isinstance(payload.get("actions"), list) else [],
        )

    def _load_callbacks(self) -> list[SignalCallback]:
        callbacks_path = Path(os.getenv("SIGNAL_CALLBACKS_FILE", self.project_root / DEFAULT_CALLBACKS_FILE))
        if not callbacks_path.exists():
            return []

        registry = CallbackRegistry()
        spec = importlib.util.spec_from_file_location("tq_signal_callbacks", callbacks_path)
        if spec is None or spec.loader is None:
            LOGGER.warning("signal callbacks module cannot be loaded from %s", callbacks_path)
            return []

        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
            register = getattr(module, "register_callbacks", None)
            if not callable(register):
                LOGGER.warning("signal callbacks module %s must define register_callbacks(registry)", callbacks_path)
                return []
            register(registry)
        except Exception as exc:
            LOGGER.exception("signal callbacks load failed from %s: %s", callbacks_path, exc)
            return []
        return registry.callbacks


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
