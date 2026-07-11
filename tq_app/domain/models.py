from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


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
    htf_lock_key: str | None = None
    htf_context: dict[str, Any] = field(default_factory=dict)
    htf_reentry_allowed: bool = False
    htf_reentry_context: dict[str, Any] = field(default_factory=dict)
