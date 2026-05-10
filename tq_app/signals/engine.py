from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from tq_app.actions import ActionExecutor
from tq_app.signals.models import SignalEvent, SignalRule

LOGGER = logging.getLogger("tq_app.signals")
DEFAULT_RULES_FILE = "signal_rules.json"


class SignalEngine:
    def __init__(self, project_root: Path, executor: ActionExecutor | None = None) -> None:
        self.project_root = project_root
        self.executor = executor or ActionExecutor(dry_run=True)
        self.rules = self._load_rules()
        self._fired_keys: set[str] = set()
        self._last_fire_at_by_rule: dict[str, float] = {}

    def describe(self) -> dict[str, Any]:
        return {
            "enabled": bool(self.rules),
            "rule_count": len(self.rules),
            "rules": [
                {
                    "id": item.id,
                    "name": item.name,
                    "enabled": item.enabled,
                    "condition": item.condition,
                    "actions": item.actions,
                    "once_per_bar": item.once_per_bar,
                    "cooldown_seconds": item.cooldown_seconds,
                }
                for item in self.rules
            ],
        }

    def evaluate(self, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        if not self.rules:
            return []
        events: list[dict[str, Any]] = []
        for rule in self.rules:
            if not rule.enabled or not self._cooldown_allows(rule):
                continue
            event = self._evaluate_rule(rule, snapshot)
            if event is None:
                continue
            fire_key = f"{event.symbol}:{event.duration_seconds}:{rule.id}:{event.bar_time}"
            if rule.once_per_bar and fire_key in self._fired_keys:
                continue
            action_results = self.executor.execute(event, rule.actions or [{"type": "log"}])
            event.actions.extend(action_results)
            self._fired_keys.add(fire_key)
            self._last_fire_at_by_rule[rule.id] = time.time()
            events.append(event.to_dict())
        return events

    def _cooldown_allows(self, rule: SignalRule) -> bool:
        if rule.cooldown_seconds <= 0:
            return True
        last_fire_at = self._last_fire_at_by_rule.get(rule.id)
        return last_fire_at is None or time.time() - last_fire_at >= rule.cooldown_seconds

    def _evaluate_rule(self, rule: SignalRule, snapshot: dict[str, Any]) -> SignalEvent | None:
        condition = rule.condition or {}
        condition_type = str(condition.get("type", "")).strip()
        points = self._indicator_points(snapshot, condition)
        if len(points) < 2:
            return None

        previous = _point_value(points[-2])
        current = _point_value(points[-1])
        if previous is None or current is None:
            return None

        threshold = _optional_float(condition.get("threshold"))
        if condition_type == "crosses_above":
            matched = threshold is not None and previous <= threshold < current
        elif condition_type == "crosses_below":
            matched = threshold is not None and previous >= threshold > current
        elif condition_type == "turns_up_below":
            matched = self._turns_up(points) and (threshold is None or current < threshold)
        elif condition_type == "turns_down_above":
            matched = self._turns_down(points) and (threshold is None or current > threshold)
        else:
            return None

        if not matched:
            return None

        bar_time = int(points[-1].get("time") or 0)
        price = _last_price(snapshot)
        display_time = (snapshot.get("time_labels") or {}).get(str(bar_time)) or snapshot.get("last_time")
        event_id = f"{snapshot.get('symbol')}:{snapshot.get('duration_seconds')}:{rule.id}:{bar_time}"
        return SignalEvent(
            id=event_id,
            rule_id=rule.id,
            rule_name=rule.name,
            symbol=str(snapshot.get("symbol") or ""),
            provider=str(snapshot.get("provider") or ""),
            duration_seconds=int(snapshot.get("duration_seconds") or 0),
            bar_time=bar_time,
            display_time=display_time,
            price=price,
            condition_type=condition_type,
            payload={
                "indicator_id": condition.get("indicator_id"),
                "series_id": condition.get("series_id"),
                "previous": previous,
                "current": current,
                "threshold": threshold,
            },
        )

    def _indicator_points(self, snapshot: dict[str, Any], condition: dict[str, Any]) -> list[dict[str, Any]]:
        indicator_id = str(condition.get("indicator_id", "")).strip()
        series_id = str(condition.get("series_id", "")).strip()
        for indicator in snapshot.get("indicators") or []:
            if str(indicator.get("id")) != indicator_id:
                continue
            for series in indicator.get("series") or []:
                if str(series.get("id")) == series_id:
                    return [point for point in series.get("data") or [] if "value" in point]
        return []

    @staticmethod
    def _turns_up(points: list[dict[str, Any]]) -> bool:
        if len(points) < 3:
            return False
        left = _point_value(points[-3])
        middle = _point_value(points[-2])
        right = _point_value(points[-1])
        return left is not None and middle is not None and right is not None and left >= middle < right

    @staticmethod
    def _turns_down(points: list[dict[str, Any]]) -> bool:
        if len(points) < 3:
            return False
        left = _point_value(points[-3])
        middle = _point_value(points[-2])
        right = _point_value(points[-1])
        return left is not None and middle is not None and right is not None and left <= middle > right

    def _load_rules(self) -> list[SignalRule]:
        raw = os.getenv("SIGNAL_RULES_JSON", "").strip()
        if raw:
            return self._parse_rules(raw, "SIGNAL_RULES_JSON")

        rules_path = Path(os.getenv("SIGNAL_RULES_FILE", self.project_root / DEFAULT_RULES_FILE))
        if not rules_path.exists():
            return []
        return self._parse_rules(rules_path.read_text(encoding="utf-8"), str(rules_path))

    def _parse_rules(self, raw: str, source: str) -> list[SignalRule]:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            LOGGER.warning("signal rules parse failed from %s: %s", source, exc)
            return []
        items = payload.get("rules", payload) if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            LOGGER.warning("signal rules from %s must be a list", source)
            return []

        rules: list[SignalRule] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            rule_id = str(item.get("id", "")).strip()
            condition = item.get("condition")
            if not rule_id or not isinstance(condition, dict):
                continue
            rules.append(
                SignalRule(
                    id=rule_id,
                    name=str(item.get("name") or rule_id),
                    condition=condition,
                    actions=[action for action in item.get("actions", []) if isinstance(action, dict)],
                    enabled=bool(item.get("enabled", True)),
                    once_per_bar=bool(item.get("once_per_bar", True)),
                    cooldown_seconds=max(float(item.get("cooldown_seconds", 0) or 0), 0.0),
                )
            )
        return rules


def _point_value(point: dict[str, Any]) -> float | None:
    value = point.get("value")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _last_price(snapshot: dict[str, Any]) -> float | None:
    value = snapshot.get("last_close")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
