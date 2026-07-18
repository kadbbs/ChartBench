# Architecture

The project is organized as a modular monolith:

- `tq_app/domain/` contains pure signal strategies, signal evaluation, and risk policy.
- `tq_app/application/` coordinates live-trading workflows.
- `tq_app/adapters/` contains persistence and other infrastructure adapters.
- `tq_app/live_trading.py` is a compatibility facade for existing imports.
- `tq_app/cli/` provides the unified command router and shared argument groups.
- `tq_app/configuration/` owns shared defaults plus read-only config inspection and validation.
- `tq_app/backtesting/runtime.py` prepares low, primary-HTF, and re-entry-HTF market data for both single and matrix backtests.
- `tq_app/backtesting/application.py` is the shared run boundary used by CLI and Web, keeping profile resolution and effective `BacktestConfig` construction consistent.
- `tq_app/backtesting/experiments.py` owns the opt-in Web experiment queue, lightweight matrix results, prepared-study reuse, stability scoring, and chart previews.
- `tq_app/backtesting/web.py` exposes the isolated `/backtests` page and `/api/backtests/*` namespace without changing chart APIs.

## Registering a strategy

Create `custom_strategies.py` in the project root. A strategy receives a
`StrategyContext` and returns a `StrategyResult`; it does not import or modify
exchange clients.

```python
from tq_app.domain import StrategyResult


class MyStrategy:
    name = "my_strategy"
    explanation = {
        "title": "My strategy",
        "summary": "A short description shown in the backtest strategy panel.",
        "tags": ["custom"],
        "sections": [
            {"title": "Entry", "items": ["Open long when a Buy marker appears."]},
        ],
    }

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

The optional JSON-compatible `explanation` metadata is returned by the strategy
catalog and rendered by the backtest UI. If it is omitted, the UI still shows a
fallback panel that identifies the strategy as custom and points to its implementation.

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

`stc_1d_1h_reentry_24bar_refresh` is an opt-in variant. While its own managed
same-side position is open, a new signal that passes the complete low-timeframe,
1D, and required 1H checks is still not allowed to add to the position. If that
signal arrives before the current 24-bar startup window expires, it only moves
the startup-failure timer anchor to the next execution bar for that signal. The original entry, size,
fees, price excursions, disaster stop, breakeven state, and trailing protection
are unchanged. A signal at or after the 24th bar does not refresh the window.
Live refreshes require real-trading mode and a position opened by this exact
opt-in strategy; observation, manually synchronized, and legacy positions do not qualify.

The canonical name is
`stc_extreme_contrarian_1d_1h_reentry_24bar_refresh`; profiles and commands can
use the shorter `stc_1d_1h_reentry_24bar_refresh` alias. This behavior is a
strategy capability carried by the decision into live trading and backtesting,
so existing strategies keep their original same-side skip behavior.

List all registered built-in and custom strategies without starting market data:

```bash
./myvenv/bin/python run_live_trading.py --list-strategies
./myvenv/bin/python run_backtest.py --list-strategies
```

The JSON output groups aliases under their canonical strategy name.

## Shared risk policy

`tq_app.domain.RiskPolicy` owns disaster-stop, breakeven, trailing-protection,
and startup-failure state transitions. Live trading supplies tick observations;
backtesting supplies bar observations. Exchange order placement remains in the
application runtime.
