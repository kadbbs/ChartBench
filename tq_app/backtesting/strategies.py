from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from tq_app.live_trading import LiveTradingConfig, LiveTradingEngine


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


class LiveDecisionStrategy:
    name = "live_decision"

    def __init__(self, project_root: Path, config: LiveTradingConfig | None = None) -> None:
        cfg = config or LiveTradingConfig.from_env(project_root)
        cfg.enabled = False
        cfg.dry_run = True
        cfg.log_only = True
        cfg.email_enabled = False
        self.engine = LiveTradingEngine(project_root, cfg)

    def evaluate(self, snapshot: dict) -> BacktestSignal:
        decision = self.engine.evaluate_snapshot(snapshot)
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
    if normalized in {"live_decision", "stc_extreme_contrarian"}:
        return LiveDecisionStrategy(project_root, config)
    raise KeyError(f"未知回测策略: {name}，当前可选: live_decision")
