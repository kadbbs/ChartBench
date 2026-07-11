# Architecture

The project is organized as a modular monolith:

- `tq_app/domain/` contains pure signal strategies, signal evaluation, and risk policy.
- `tq_app/application/` coordinates live-trading workflows.
- `tq_app/adapters/` contains persistence and other infrastructure adapters.
- `tq_app/live_trading.py` is a compatibility facade for existing imports.

## Registering a strategy

Create `custom_strategies.py` in the project root. A strategy receives a
`StrategyContext` and returns a `StrategyResult`; it does not import or modify
exchange clients.

```python
from tq_app.domain import StrategyResult


class MyStrategy:
    name = "my_strategy"

    def evaluate(self, context):
        if "Buy" in context.marker_texts:
            return StrategyResult("buy", "my buy rule")
        return StrategyResult(None, "no signal")


def register_strategies(registry):
    registry.register("my_strategy", MyStrategy)
```

Select it with `LIVE_TRADING_STRATEGY: my_strategy` in a live profile or
`signal_strategy: my_strategy` in a backtest profile. Both modes use the same
`SignalEvaluator`.

## Shared risk policy

`tq_app.domain.RiskPolicy` owns disaster-stop, breakeven, trailing-protection,
and startup-failure state transitions. Live trading supplies tick observations;
backtesting supplies bar observations. Exchange order placement remains in the
application runtime.
