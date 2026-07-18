from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from tq_app.domain import SignalConfig, SignalEvaluator, load_custom_strategies
from tq_app.live_trading import LiveTradingConfig


@dataclass(slots=True)
class BacktestSignal:
    side: str | None
    reason: str
    htf_lock_key: str | None = None
    htf_context: dict | None = None
    htf_reentry_allowed: bool = False
    htf_reentry_context: dict | None = None
    refresh_startup_on_same_side_signal: bool = False


class KlineStrategy(Protocol):
    name: str
    signal_strategy_name: str
    primary_htf_duration_seconds: int
    reentry_confirmation_duration_seconds: int | None
    refresh_startup_on_same_side_signal: bool

    def evaluate(self, snapshot: dict) -> BacktestSignal:
        raise NotImplementedError


class SignalEvaluatorStrategy:
    def __init__(self, name: str, project_root: Path, config: LiveTradingConfig | None = None) -> None:
        live_config = replace(config) if config is not None else LiveTradingConfig.from_env(project_root)
        strategy_name = live_config.strategy if name == "live_decision" else name
        self.name = name
        load_custom_strategies(project_root)
        self.evaluator = SignalEvaluator(replace(SignalConfig.from_object(live_config), strategy=strategy_name))
        self.signal_strategy_name = self.evaluator.strategy.name
        self.primary_htf_duration_seconds = self.evaluator.primary_htf_duration_seconds
        self.reentry_confirmation_duration_seconds = self.evaluator.reentry_confirmation_duration_seconds
        self.refresh_startup_on_same_side_signal = self.evaluator.refresh_startup_on_same_side_signal

    def evaluate(self, snapshot: dict) -> BacktestSignal:
        decision = self.evaluator.evaluate(snapshot)
        if decision.action != "place_order" or decision.side is None:
            return BacktestSignal(
                side=None,
                reason=decision.reason,
                htf_context=decision.htf_context,
                htf_reentry_allowed=decision.htf_reentry_allowed,
                htf_reentry_context=decision.htf_reentry_context,
                refresh_startup_on_same_side_signal=decision.refresh_startup_on_same_side_signal,
            )
        return BacktestSignal(
            side=decision.side,
            reason=decision.reason,
            htf_lock_key=decision.htf_lock_key,
            htf_context=decision.htf_context,
            htf_reentry_allowed=decision.htf_reentry_allowed,
            htf_reentry_context=decision.htf_reentry_context,
            refresh_startup_on_same_side_signal=decision.refresh_startup_on_same_side_signal,
        )


def build_strategy(name: str, project_root: Path, config: LiveTradingConfig | None = None) -> KlineStrategy:
    normalized = name.strip().lower()
    return SignalEvaluatorStrategy(normalized, project_root, config)
