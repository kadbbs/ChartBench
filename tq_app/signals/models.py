from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class SignalCallback:
    id: str
    name: str
    handler: Callable[[Any], Any]
    actions: list[dict[str, Any]] = field(default_factory=list)
    enabled: bool = True
    once_per_bar: bool = True
    cooldown_seconds: float = 0.0


@dataclass(slots=True)
class SignalEvent:
    id: str
    rule_id: str
    rule_name: str
    symbol: str
    provider: str
    duration_seconds: int
    bar_time: int
    display_time: str | None
    price: float | None
    condition_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    actions: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "symbol": self.symbol,
            "provider": self.provider,
            "duration_seconds": self.duration_seconds,
            "bar_time": self.bar_time,
            "display_time": self.display_time,
            "price": self.price,
            "condition_type": self.condition_type,
            "payload": self.payload,
            "actions": self.actions,
        }
