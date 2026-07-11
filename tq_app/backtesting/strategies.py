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


class KlineStrategy(Protocol):
    name: str

    def evaluate(self, snapshot: dict) -> BacktestSignal:
        raise NotImplementedError


class SignalEvaluatorStrategy:
    def __init__(self, name: str, project_root: Path, config: LiveTradingConfig | None = None) -> None:
        live_config = replace(config) if config is not None else LiveTradingConfig.from_env(project_root)
        strategy_name = live_config.strategy if name == "live_decision" else name
        self.name = name
        load_custom_strategies(project_root)
        self.evaluator = SignalEvaluator(replace(SignalConfig.from_object(live_config), strategy=strategy_name))

    def evaluate(self, snapshot: dict) -> BacktestSignal:
        decision = self.evaluator.evaluate(snapshot)
        if decision.action != "place_order" or decision.side is None:
            return BacktestSignal(side=None, reason=decision.reason, htf_context=decision.htf_context)
        return BacktestSignal(
            side=decision.side,
            reason=decision.reason,
            htf_lock_key=decision.htf_lock_key,
            htf_context=decision.htf_context,
        )


def build_strategy(name: str, project_root: Path, config: LiveTradingConfig | None = None) -> KlineStrategy:
    normalized = name.strip().lower()
    return SignalEvaluatorStrategy(normalized, project_root, config)
