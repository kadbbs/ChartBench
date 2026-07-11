from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any


ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class RiskPolicyConfig:
    duration_seconds: int = 300
    startup_check_bars_5m: int = 24
    startup_max_favorable_points: Decimal = Decimal("300")
    startup_current_points: Decimal = Decimal("-120")
    disaster_stop_points: Decimal = Decimal("-1800")
    breakeven_trigger_points: Decimal = Decimal("800")
    breakeven_stop_points: Decimal = Decimal("100")
    trailing_rules: tuple[tuple[Decimal, Decimal], ...] = (
        (Decimal("2000"), Decimal("0.4")),
        (Decimal("4000"), Decimal("0.5")),
        (Decimal("8000"), Decimal("0.6")),
    )

    @classmethod
    def from_live_config(cls, config: object, *, duration_seconds: int = 300) -> "RiskPolicyConfig":
        return cls(
            duration_seconds=max(int(duration_seconds), 1),
            startup_check_bars_5m=max(int(getattr(config, "risk_startup_check_bars_5m", 24)), 1),
            startup_max_favorable_points=_decimal(
                getattr(config, "risk_startup_max_favorable_points", "300")
            ),
            startup_current_points=_decimal(getattr(config, "risk_startup_current_points", "-120")),
            disaster_stop_points=_decimal(getattr(config, "risk_disaster_stop_points", "-1800")),
            breakeven_trigger_points=_decimal(
                getattr(config, "risk_breakeven_trigger_points", "800")
            ),
            breakeven_stop_points=_decimal(getattr(config, "risk_breakeven_stop_points", "100")),
            trailing_rules=tuple(
                (
                    _decimal(getattr(config, f"risk_trailing_trigger_{index}_points")),
                    _decimal(getattr(config, f"risk_trailing_protect_{index}_ratio")),
                )
                for index in (1, 2, 3)
            ),
        )

    @classmethod
    def from_backtest_config(cls, config: object) -> "RiskPolicyConfig":
        return cls(
            duration_seconds=max(int(getattr(config, "duration_seconds", 300)), 1),
            startup_check_bars_5m=max(int(getattr(config, "startup_check_bars_5m", 24)), 1),
            startup_max_favorable_points=_decimal(
                getattr(config, "startup_max_favorable_points", 300)
            ),
            startup_current_points=_decimal(getattr(config, "startup_current_points", -120)),
            disaster_stop_points=_decimal(getattr(config, "disaster_stop_points", -1800)),
            breakeven_trigger_points=_decimal(getattr(config, "breakeven_trigger_points", 800)),
            breakeven_stop_points=_decimal(getattr(config, "breakeven_stop_points", 100)),
            trailing_rules=tuple(
                (
                    _decimal(getattr(config, f"trailing_trigger_{index}_points")),
                    _decimal(getattr(config, f"trailing_protect_{index}_ratio")),
                )
                for index in (1, 2, 3)
            ),
        )

    def startup_check_bars(self) -> int:
        return max(round(self.startup_check_bars_5m * 300 / self.duration_seconds), 1)


@dataclass(frozen=True, slots=True)
class RiskState:
    current_points: Decimal = ZERO
    max_favorable_points: Decimal = ZERO
    max_adverse_points: Decimal = ZERO
    protected_stop_points: Decimal | None = None
    startup_checked: bool = False


@dataclass(frozen=True, slots=True)
class RiskEvaluation:
    state: RiskState
    trigger: str | None = None
    exit_points: Decimal | None = None
    protection_kind: str | None = None


class RiskPolicy:
    """Pure position-risk state machine used by live ticks and backtest bars."""

    def __init__(self, config: RiskPolicyConfig) -> None:
        self.config = config

    def evaluate_tick(
        self,
        state: RiskState,
        *,
        side: str,
        entry_price: Any,
        current_price: Any,
        bars_since_entry: int | None = None,
    ) -> RiskEvaluation:
        current_points = points(side, entry_price, current_price)
        observed = self._observe(
            state,
            current_points=current_points,
            favorable_points=current_points,
            adverse_points=current_points,
        )
        return self._evaluate(
            observed,
            startup_due=(
                bars_since_entry is not None
                and not observed.startup_checked
                and bars_since_entry >= self.config.startup_check_bars_5m
            ),
        )

    def evaluate_bar(
        self,
        state: RiskState,
        *,
        side: str,
        entry_price: Any,
        high: Any,
        low: Any,
        close: Any,
        bars_since_entry: int,
    ) -> RiskEvaluation:
        entry = _decimal(entry_price)
        high_value = _decimal(high)
        low_value = _decimal(low)
        close_points = points(side, entry, close)
        favorable = high_value - entry if side == "buy" else entry - low_value
        adverse = low_value - entry if side == "buy" else entry - high_value
        observed = self._observe(
            state,
            current_points=close_points,
            favorable_points=favorable,
            adverse_points=adverse,
        )
        evaluation = self._evaluate(
            observed,
            startup_due=(bars_since_entry == self.config.startup_check_bars()),
            protected_stop_touched=_bar_touches_points(
                side, entry, high_value, low_value, observed.protected_stop_points
            ),
        )
        return evaluation

    def evaluate_observed_tick(
        self,
        state: RiskState,
        *,
        bars_since_entry: int | None = None,
    ) -> RiskEvaluation:
        return self._evaluate(
            state,
            startup_due=(
                bars_since_entry is not None
                and not state.startup_checked
                and bars_since_entry >= self.config.startup_check_bars_5m
            ),
        )

    def protected_stop(self, max_favorable: Any, previous: Any = None) -> Decimal | None:
        favorable = _decimal(max_favorable)
        protected = _optional_decimal(previous)
        if favorable >= self.config.breakeven_trigger_points:
            protected = max(protected or Decimal("-Infinity"), self.config.breakeven_stop_points)
        for trigger, ratio in self.config.trailing_rules:
            if favorable >= trigger:
                protected = max(protected or Decimal("-Infinity"), favorable * ratio)
        return protected

    def _observe(
        self,
        state: RiskState,
        *,
        current_points: Decimal,
        favorable_points: Decimal,
        adverse_points: Decimal,
    ) -> RiskState:
        max_favorable = max(state.max_favorable_points, favorable_points)
        return replace(
            state,
            current_points=current_points,
            max_favorable_points=max_favorable,
            max_adverse_points=min(state.max_adverse_points, adverse_points),
            protected_stop_points=self.protected_stop(max_favorable, state.protected_stop_points),
        )

    def _evaluate(
        self,
        state: RiskState,
        *,
        startup_due: bool,
        protected_stop_touched: bool | None = None,
    ) -> RiskEvaluation:
        if (
            state.max_adverse_points <= self.config.disaster_stop_points
            or state.current_points <= self.config.disaster_stop_points
        ):
            return RiskEvaluation(state, "disaster", self.config.disaster_stop_points)

        protected = state.protected_stop_points
        protection_triggered = (
            protected is not None
            and (protected_stop_touched if protected_stop_touched is not None else state.current_points <= protected)
        )
        if protection_triggered:
            kind = "breakeven" if protected <= self.config.breakeven_stop_points else "trailing"
            return RiskEvaluation(state, "protected", protected, kind)

        if startup_due:
            checked_state = replace(state, startup_checked=True)
            if (
                state.max_favorable_points < self.config.startup_max_favorable_points
                and state.current_points < self.config.startup_current_points
            ):
                return RiskEvaluation(checked_state, "startup", state.current_points)
            return RiskEvaluation(checked_state)
        return RiskEvaluation(state)


def points(side: str, entry_price: Any, price: Any) -> Decimal:
    entry = _decimal(entry_price)
    current = _decimal(price)
    return current - entry if side == "buy" else entry - current


def price_for_points(side: str, entry_price: Any, point_value: Any) -> Decimal:
    entry = _decimal(entry_price)
    value = _decimal(point_value)
    return entry + value if side == "buy" else entry - value


def _bar_touches_points(
    side: str,
    entry: Decimal,
    high: Decimal,
    low: Decimal,
    point_value: Decimal | None,
) -> bool:
    if point_value is None:
        return False
    price = price_for_points(side, entry, point_value)
    return low <= price if side == "buy" else high >= price


def _decimal(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _optional_decimal(value: Any) -> Decimal | None:
    return None if value in (None, "") else _decimal(value)
