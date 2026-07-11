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

### Built-in 1D/1H re-entry strategy

`stc_1d_1h_reentry` inherits the signal conditions of
`stc_extreme_contrarian`. It fixes the primary Hull/STC trend filter at 1D.
The first entry in a 1D Hull trend segment follows the normal rule; after that
entry is closed, another low-timeframe signal may enter in the same direction
only when the last closed 1H Hull band and 1H STC color point in that direction.
The 1D and 1H STC checks follow `d9461a4`: color alignment only, with no numeric
threshold.

The full canonical name is `stc_extreme_contrarian_1d_1h_reentry`; the shorter
`stc_1d_1h_reentry` alias is intended for profiles and commands.

## Shared risk policy

`tq_app.domain.RiskPolicy` owns disaster-stop, breakeven, trailing-protection,
and startup-failure state transitions. Live trading supplies tick observations;
backtesting supplies bar observations. Exchange order placement remains in the
application runtime.
