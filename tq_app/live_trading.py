"""Backward-compatible live-trading API.

New code should depend on domain services or ``tq_app.application.live_runtime``
directly. This facade keeps existing CLI and external imports stable.
"""

from tq_app.application.live_runtime import (
    BinanceFuturesTradeClient,
    BinanceTickerWebSocket,
    BitgetFuturesTradeClient,
    BitgetTickerWebSocket,
    FuturesTradeClient,
    LiveTradingConfig,
    LiveTradingEngine,
    PreflightResult,
    TickerWebSocket,
    TradeDecision,
    TradeExecutionResult,
    create_futures_trade_client,
    create_ticker_websocket,
)

__all__ = [
    "BinanceFuturesTradeClient",
    "BinanceTickerWebSocket",
    "BitgetFuturesTradeClient",
    "BitgetTickerWebSocket",
    "FuturesTradeClient",
    "LiveTradingConfig",
    "LiveTradingEngine",
    "PreflightResult",
    "TickerWebSocket",
    "TradeDecision",
    "TradeExecutionResult",
    "create_futures_trade_client",
    "create_ticker_websocket",
]
