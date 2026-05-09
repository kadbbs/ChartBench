from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import pandas as pd

from tq_app.models import IndicatorMeta, IndicatorResult


class Indicator(ABC):
    meta: IndicatorMeta

    def resolve_params(self, raw_params: dict[str, Any] | None = None) -> dict[str, Any]:
        resolved: dict[str, Any] = {}
        provided = raw_params or {}
        for definition in self.meta.params:
            key = definition["key"]
            default = definition.get("default")
            value = provided.get(key, default)
            resolved[key] = self._coerce_param(definition, value)
        return resolved

    def _coerce_param(self, definition: dict[str, Any], value: Any) -> Any:
        param_type = definition.get("type", "string")
        if value in (None, ""):
            return definition.get("default")
        if param_type == "int":
            coerced = int(value)
            if definition.get("min") is not None:
                coerced = max(coerced, int(definition["min"]))
            if definition.get("max") is not None:
                coerced = min(coerced, int(definition["max"]))
            return coerced
        if param_type == "float":
            coerced = float(value)
            if definition.get("min") is not None:
                coerced = max(coerced, float(definition["min"]))
            if definition.get("max") is not None:
                coerced = min(coerced, float(definition["max"]))
            return coerced
        if param_type == "bool":
            if isinstance(value, bool):
                return value
            return str(value).lower() in {"1", "true", "yes", "on"}
        return str(value)

    @abstractmethod
    def build(self, bars: pd.DataFrame, params: dict[str, Any] | None = None) -> IndicatorResult:
        raise NotImplementedError


class IndicatorRegistry:
    def __init__(self) -> None:
        self._indicators: dict[str, Indicator] = {}

    def register(self, indicator: Indicator) -> None:
        self._indicators[indicator.meta.id] = indicator

    def get(self, indicator_id: str) -> Indicator:
        try:
            return self._indicators[indicator_id]
        except KeyError as exc:
            names = ", ".join(sorted(self._indicators))
            raise KeyError(f"未知指标: {indicator_id}，可选值: {names}") from exc

    def list_meta(self) -> list[IndicatorMeta]:
        return [item.meta for item in self._indicators.values()]

    def default_ids(self) -> list[str]:
        return [item.meta.id for item in self._indicators.values() if item.meta.enabled_by_default]
