from __future__ import annotations

from typing import Any


def register_callbacks(registry: Any) -> None:
    registry.on_snapshot(
        id="duo_kong_buy_marker_log",
        name="多空线 多 信号日志",
        callback=duo_kong_buy_marker_log,
        actions=[{"type": "log", "level": "info"}],
        once_per_bar=True,
        cooldown_seconds=0,
    )
    registry.on_snapshot(
        id="duo_kong_sell_marker_log",
        name="多空线 空 信号日志",
        callback=duo_kong_sell_marker_log,
        actions=[{"type": "log", "level": "info"}],
        once_per_bar=True,
        cooldown_seconds=0,
    )


def duo_kong_buy_marker_log(ctx: Any) -> Any:
    marker = ctx.latest_marker("duo_kong_line", "duo_kong_line", "多", lookback_bars=1)
    if marker is None:
        return None
    return ctx.signal(
        condition_type="duo_kong_marker",
        bar_time=int(marker["time"]),
        payload={
            "side": "buy",
            "indicator_id": "duo_kong_line",
            "series_id": "duo_kong_line",
            "marker": marker,
        },
    )


def duo_kong_sell_marker_log(ctx: Any) -> Any:
    marker = ctx.latest_marker("duo_kong_line", "duo_kong_line", "空", lookback_bars=1)
    if marker is None:
        return None
    return ctx.signal(
        condition_type="duo_kong_marker",
        bar_time=int(marker["time"]),
        payload={
            "side": "sell",
            "indicator_id": "duo_kong_line",
            "series_id": "duo_kong_line",
            "marker": marker,
        },
    )
