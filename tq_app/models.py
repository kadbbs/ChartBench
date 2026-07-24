from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class SeriesDefinition:
    id: str
    name: str
    pane: str
    series_type: str
    data: list[dict[str, Any]]
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class IndicatorResult:
    id: str
    name: str
    pane: str
    series: list[SeriesDefinition]
    features: dict[str, list[Any]] = field(default_factory=dict)


@dataclass(slots=True)
class IndicatorMeta:
    id: str
    name: str
    pane: str
    description: str
    enabled_by_default: bool = False
    params: list[dict[str, Any]] = field(default_factory=list)
