from __future__ import annotations

import csv
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import median
from typing import Any

import pandas as pd

from tq_app.backtesting.snapshot import SnapshotBuilder, attach_higher_timeframe, slice_snapshot
from tq_app.backtesting.strategies import KlineStrategy
from tq_app.live_trading import LiveTradingConfig
from tq_app.service import DISPLAY_TIMEZONE


@dataclass(slots=True)
class BacktestConfig:
    symbol: str
    provider: str = "bitget"
    duration_seconds: int = 300
    initial_equity: float = 1_000.0
    risk_per_trade: float = 0.01
    margin_amount: float = 1_000.0
    leverage: float = 10.0
    fee_rate: float = 0.0006
    slippage_rate: float = 0.0
    stop_atr_multiplier: float = 2.0
    tp1_r_multiple: float = 1.0
    tp1_size_ratio: float = 0.5
    tp2_r_multiple: float = 1.5
    atr_period: int = 14
    warmup_bars: int = 80
    output_dir: Path = Path("backtest_outputs/latest")


@dataclass(slots=True)
class BacktestTrade:
    id: int
    side: str
    signal_time: int
    signal_time_label: str
    entry_time: int
    entry_time_label: str
    entry_price: float
    stop_price: float | None
    tp1_price: float | None
    tp2_price: float | None
    qty: float
    exit_time: int | None = None
    exit_time_label: str = ""
    exit_price: float | None = None
    exit_reason: str = ""
    pnl: float = 0.0
    fees: float = 0.0
    net_pnl: float = 0.0
    points: float = 0.0
    fee_points: float = 0.0
    net_points: float = 0.0
    r_multiple: float = 0.0
    signal_reason: str = ""
    partial_exits: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class Position:
    trade: BacktestTrade
    remaining_qty: float
    risk_amount: float


@dataclass(slots=True)
class BacktestResult:
    config: dict[str, Any]
    metrics: dict[str, Any]
    trades: list[BacktestTrade]
    markers: list[dict[str, Any]]
    output_dir: str


class BacktestEngine:
    def __init__(
        self,
        *,
        project_root: Path,
        config: BacktestConfig,
        live_config: LiveTradingConfig,
        strategy: KlineStrategy,
    ) -> None:
        self.project_root = project_root
        self.config = config
        self.live_config = live_config
        self.strategy = strategy

    def run(self, bars: pd.DataFrame, htf_bars: pd.DataFrame | None = None) -> BacktestResult:
        if len(bars) < self.config.warmup_bars + 3:
            raise RuntimeError("K 线数量不足，无法完成回测。")

        low_builder = SnapshotBuilder(
            project_root=self.project_root,
            symbol=self.config.symbol,
            provider=self.config.provider,
            duration_seconds=self.config.duration_seconds,
            indicator_ids=["merged_dkx_hull_ut", "stc", "macd"],
        )
        full_snapshot = low_builder.build_full(bars)

        htf_snapshot = None
        if htf_bars is not None and not htf_bars.empty and self.live_config.htf_hull_filter_enabled:
            htf_builder = SnapshotBuilder(
                project_root=self.project_root,
                symbol=self.config.symbol,
                provider=self.config.provider,
                duration_seconds=self.live_config.htf_hull_duration_seconds,
                indicator_ids=["merged_dkx_hull_ut", "stc"],
            )
            htf_snapshot = htf_builder.build_full(htf_bars)

        candles = full_snapshot["candles"]
        time_labels = full_snapshot.get("time_labels") or {}
        equity = float(self.config.initial_equity)
        equity_curve: list[float] = [equity]
        trades: list[BacktestTrade] = []
        markers: list[dict[str, Any]] = []
        position: Position | None = None
        next_trade_id = 1

        for entry_index in range(max(self.config.warmup_bars, 1), len(candles)):
            entry_candle = candles[entry_index]
            signal_index = entry_index - 1
            signal_candle = candles[signal_index]
            snapshot = slice_snapshot(full_snapshot, entry_index + 1)
            snapshot = attach_higher_timeframe(snapshot, htf_snapshot, int(entry_candle["time"]))
            signal = self.strategy.evaluate(snapshot)

            if position is not None and signal.side in {"buy", "sell"} and signal.side != position.trade.side:
                realized = self._close_remaining(
                    position,
                    entry_candle,
                    "reverse_signal",
                    time_labels,
                    price=self._exit_price(entry_candle, position.trade.side),
                )
                equity += realized
                equity_curve.append(equity)
                markers.append(
                    _marker(
                        entry_candle["time"],
                        "aboveBar" if position.trade.side == "buy" else "belowBar",
                        "#ff9800",
                        "CLOSE REVERSE",
                    )
                )
                position = None

            if position is None:
                if signal.side in {"buy", "sell"}:
                    trade, position = self._open_position(
                        trade_id=next_trade_id,
                        side=signal.side,
                        signal_candle=signal_candle,
                        entry_candle=entry_candle,
                        equity=equity,
                        reason=signal.reason,
                        time_labels=time_labels,
                    )
                    next_trade_id += 1
                    trades.append(trade)
                    markers.append(
                        _marker(
                            entry_candle["time"],
                            "belowBar" if signal.side == "buy" else "aboveBar",
                            "#4caf50" if signal.side == "buy" else "#f23645",
                            f"OPEN {signal.side.upper()}",
                        )
                    )

        if position is not None:
            last_candle = candles[-1]
            equity += self._close_remaining(
                position,
                last_candle,
                "end_of_data",
                time_labels,
                price=self._exit_price(last_candle, position.trade.side, close_at_close=True),
            )
            equity_curve.append(equity)
            markers.append(_marker(last_candle["time"], "aboveBar" if position.trade.side == "buy" else "belowBar", "#ff9800", "CLOSE END"))

        metrics = _metrics(trades, equity_curve, self.config.initial_equity)
        result = BacktestResult(
            config={
                **asdict(self.config),
                "output_dir": str(self.config.output_dir),
                "strategy": self.strategy.name,
                "execution_model": "live_reverse_signal",
            },
            metrics=metrics,
            trades=trades,
            markers=markers,
            output_dir=str(self.config.output_dir),
        )
        self.write_outputs(result, full_snapshot)
        return result

    def _open_position(
        self,
        *,
        trade_id: int,
        side: str,
        signal_candle: dict[str, Any],
        entry_candle: dict[str, Any],
        equity: float,
        reason: str,
        time_labels: dict[str, str],
    ) -> tuple[BacktestTrade, Position]:
        entry_price = _apply_slippage(float(entry_candle["open"]), side, self.config.slippage_rate)
        qty = self._order_qty(entry_price, equity)
        risk_amount = abs(qty * entry_price)
        trade = BacktestTrade(
            id=trade_id,
            side=side,
            signal_time=int(signal_candle["time"]),
            signal_time_label=time_labels.get(str(signal_candle["time"]), ""),
            entry_time=int(entry_candle["time"]),
            entry_time_label=time_labels.get(str(entry_candle["time"]), ""),
            entry_price=entry_price,
            stop_price=None,
            tp1_price=None,
            tp2_price=None,
            qty=qty,
            signal_reason=reason,
        )
        trade.fees += abs(qty * entry_price) * self.config.fee_rate
        return trade, Position(trade=trade, remaining_qty=qty, risk_amount=risk_amount)

    def _order_qty(self, entry_price: float, equity: float) -> float:
        notional = max(self.config.margin_amount * self.config.leverage, 0.0)
        if entry_price <= 0 or notional <= 0:
            raise RuntimeError("回测下单数量无效：请检查 margin_amount / leverage。")
        return notional / entry_price

    def _exit_price(self, candle: dict[str, Any], side: str, close_at_close: bool = False) -> float:
        base_price = float(candle["close"] if close_at_close else candle["open"])
        exit_side = "sell" if side == "buy" else "buy"
        return _apply_slippage(base_price, exit_side, self.config.slippage_rate)

    def _close_partial(
        self,
        position: Position,
        candle: dict[str, Any],
        qty: float,
        price: float,
        reason: str,
        time_labels: dict[str, str],
    ) -> float:
        pnl = _pnl(position.trade.side, position.trade.entry_price, price, qty)
        points = _points(position.trade.side, position.trade.entry_price, price)
        fee = abs(qty * price) * self.config.fee_rate
        position.remaining_qty -= qty
        position.trade.pnl += pnl
        position.trade.points += points
        position.trade.fees += fee
        position.trade.partial_exits.append(
            {
                "time": int(candle["time"]),
                "time_label": time_labels.get(str(candle["time"]), ""),
                "price": price,
                "qty": qty,
                "reason": reason,
                "points": points,
                "pnl": pnl,
                "fee": fee,
            }
        )
        position.trade.net_pnl = position.trade.pnl - position.trade.fees
        position.trade.fee_points = position.trade.fees / position.trade.qty if position.trade.qty else 0.0
        position.trade.net_points = position.trade.net_pnl / position.trade.qty if position.trade.qty else 0.0
        return pnl - fee

    def _close_remaining(
        self,
        position: Position,
        candle: dict[str, Any],
        reason: str,
        time_labels: dict[str, str],
        price: float | None = None,
    ) -> float:
        exit_price = price if price is not None else float(candle["close"])
        qty = position.remaining_qty
        realized = self._close_partial(position, candle, qty, exit_price, reason, time_labels)
        trade = position.trade
        trade.exit_time = int(candle["time"])
        trade.exit_time_label = time_labels.get(str(candle["time"]), "")
        trade.exit_price = exit_price
        trade.exit_reason = reason
        trade.net_pnl = trade.pnl - trade.fees
        trade.fee_points = trade.fees / trade.qty if trade.qty else 0.0
        trade.net_points = trade.net_pnl / trade.qty if trade.qty else 0.0
        trade.r_multiple = trade.net_pnl / position.risk_amount if position.risk_amount else 0.0
        position.remaining_qty = 0.0
        return realized

    def write_outputs(self, result: BacktestResult, snapshot: dict[str, Any]) -> None:
        output_dir = Path(result.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        trades_payload = [asdict(item) for item in result.trades]
        report_payload = {
            "summary": _report_summary(result, snapshot),
            "config": result.config,
            "metrics": result.metrics,
            "analysis": _report_analysis(result, snapshot),
            "trades": trades_payload,
        }
        (output_dir / "report.json").write_text(json.dumps(report_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        (output_dir / "report.md").write_text(_markdown_report(report_payload), encoding="utf-8")
        (output_dir / "report_zh.md").write_text(_markdown_report_zh(report_payload), encoding="utf-8")
        chart_payload = {
            "symbol": self.config.symbol,
            "duration_seconds": self.config.duration_seconds,
            "candles": snapshot.get("candles") or [],
            "volume": snapshot.get("volume") or [],
            "time_labels": snapshot.get("time_labels") or {},
            "markers": result.markers,
        }
        (output_dir / "candles.json").write_text(json.dumps(chart_payload, ensure_ascii=False), encoding="utf-8")
        with (output_dir / "trades.csv").open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=list(asdict(result.trades[0]).keys()) if result.trades else ["id"])
            writer.writeheader()
            for trade in result.trades:
                row = asdict(trade)
                row["partial_exits"] = json.dumps(row["partial_exits"], ensure_ascii=False)
                writer.writerow(row)


def _pnl(side: str, entry: float, exit_price: float, qty: float) -> float:
    direction = 1.0 if side == "buy" else -1.0
    return (exit_price - entry) * direction * qty


def _points(side: str, entry: float, exit_price: float) -> float:
    direction = 1.0 if side == "buy" else -1.0
    return (exit_price - entry) * direction


def _apply_slippage(price: float, side: str, slippage_rate: float) -> float:
    direction = 1.0 if side == "buy" else -1.0
    return price * (1.0 + direction * max(slippage_rate, 0.0))


def _marker(time_value: int, position: str, color: str, text: str) -> dict[str, Any]:
    return {"time": int(time_value), "position": position, "color": color, "shape": "arrowUp" if position == "belowBar" else "arrowDown", "text": text}


def _metrics(trades: list[BacktestTrade], equity_curve: list[float], initial_equity: float) -> dict[str, Any]:
    closed = [item for item in trades if item.exit_time is not None]
    wins = [item for item in closed if item.net_pnl > 0]
    losses = [item for item in closed if item.net_pnl < 0]
    gross_profit = sum(item.net_pnl for item in wins)
    gross_loss = abs(sum(item.net_pnl for item in losses))
    final_equity = equity_curve[-1] if equity_curve else initial_equity
    net_profit = final_equity - initial_equity
    total_fees = sum(item.fees for item in closed)
    pnl_values = [item.net_pnl for item in closed]
    r_values = [item.r_multiple for item in closed]
    point_values = [item.points for item in closed]
    net_point_values = [item.net_points for item in closed]
    point_wins = [item.points for item in closed if item.points > 0]
    point_losses = [item.points for item in closed if item.points < 0]
    return {
        "initial_equity": initial_equity,
        "final_equity": final_equity,
        "net_profit": net_profit,
        "return_pct": (final_equity / initial_equity - 1.0) * 100 if initial_equity else 0.0,
        "trade_count": len(closed),
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate": len(wins) / len(closed) if closed else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss else None,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "total_fees": total_fees,
        "fee_to_gross_profit": total_fees / gross_profit if gross_profit else None,
        "max_drawdown": _max_drawdown(equity_curve),
        "average_trade_pnl": sum(pnl_values) / len(pnl_values) if pnl_values else 0.0,
        "median_trade_pnl": median(pnl_values) if pnl_values else 0.0,
        "average_r": sum(r_values) / len(r_values) if r_values else 0.0,
        "median_r": median(r_values) if r_values else 0.0,
        "best_trade": max(pnl_values) if pnl_values else 0.0,
        "worst_trade": min(pnl_values) if pnl_values else 0.0,
        "total_points": sum(point_values),
        "total_net_points": sum(net_point_values),
        "average_points": sum(point_values) / len(point_values) if point_values else 0.0,
        "median_points": median(point_values) if point_values else 0.0,
        "best_points": max(point_values) if point_values else 0.0,
        "worst_points": min(point_values) if point_values else 0.0,
        "gross_profit_points": sum(point_wins),
        "gross_loss_points": abs(sum(point_losses)),
    }


def _max_drawdown(equity_curve: list[float]) -> float:
    peak = equity_curve[0] if equity_curve else 0.0
    max_dd = 0.0
    for equity in equity_curve:
        peak = max(peak, equity)
        if peak:
            max_dd = min(max_dd, equity / peak - 1.0)
    return max_dd


def _report_summary(result: BacktestResult, snapshot: dict[str, Any]) -> dict[str, Any]:
    candles = snapshot.get("candles") or []
    first_candle = candles[0] if candles else {}
    last_candle = candles[-1] if candles else {}
    metrics = result.metrics
    return {
        "title": f"{result.config.get('symbol')} {result.config.get('duration_seconds')}s 回测报告",
        "strategy": result.config.get("strategy"),
        "execution_model": result.config.get("execution_model"),
        "period": {
            "start": _time_label(snapshot, int(first_candle.get("time") or 0)) if first_candle else "",
            "end": _time_label(snapshot, int(last_candle.get("time") or 0)) if last_candle else "",
            "bars": len(candles),
        },
        "headline": {
            "net_profit": metrics.get("net_profit", 0.0),
            "return_pct": metrics.get("return_pct", 0.0),
            "max_drawdown_pct": abs((metrics.get("max_drawdown") or 0.0) * 100),
            "trade_count": metrics.get("trade_count", 0),
            "win_rate_pct": (metrics.get("win_rate") or 0.0) * 100,
            "profit_factor": metrics.get("profit_factor"),
            "total_points": metrics.get("total_points", 0.0),
            "total_net_points": metrics.get("total_net_points", 0.0),
        },
        "professional_note": "当前回测按实盘式反向信号换仓模型撮合，不自动模拟止盈止损；仓位固定为 1000U 保证金、10倍杠杆，未包含资金费率、爆仓强平、盘口冲击和真实成交滑点。",
    }


def _report_analysis(result: BacktestResult, snapshot: dict[str, Any]) -> dict[str, Any]:
    closed = [item for item in result.trades if item.exit_time is not None]
    return {
        "market": _market_summary(snapshot),
        "risk": _risk_summary(result),
        "trade_quality": _trade_quality_summary(closed),
        "points": _points_summary(closed),
        "holding_time": _holding_time_summary(closed),
        "breakdowns": {
            "by_side": _group_trade_summary(closed, "side"),
            "by_exit_reason": _group_trade_summary(closed, "exit_reason"),
            "by_entry_day": _period_trade_summary(closed, "%Y-%m-%d"),
            "by_entry_month": _period_trade_summary(closed, "%Y-%m"),
        },
        "key_trades": _key_trades(closed),
        "assumptions": [
            "信号在目标 K 线收完后确认，下一根 K 线 open 成交。",
            "出现反向实盘信号时，回测在同一根入场 K 线 open 平旧仓并开新仓。",
            "每笔固定使用 1000U 保证金，并按 10 倍杠杆放大为 10000U 名义价值。",
            "手续费按成交名义价值双边计入；slippage_rate 按开平仓方向调整价格。",
            "未模拟资金费率、爆仓强平、最小下单量、价格精度、盘口深度、订单失败和真实 API 延迟。",
        ],
    }


def _market_summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    candles = snapshot.get("candles") or []
    if len(candles) < 2:
        return {}
    first = candles[0]
    last = candles[-1]
    first_close = float(first["close"])
    last_close = float(last["close"])
    highs = [float(item["high"]) for item in candles]
    lows = [float(item["low"]) for item in candles]
    volumes = [float(item.get("volume") or item.get("value") or 0) for item in snapshot.get("volume") or []]
    return {
        "start_close": first_close,
        "end_close": last_close,
        "market_return_pct": (last_close / first_close - 1.0) * 100 if first_close else 0.0,
        "highest_high": max(highs),
        "lowest_low": min(lows),
        "range_pct": (max(highs) / min(lows) - 1.0) * 100 if min(lows) else 0.0,
        "total_volume": sum(volumes),
        "average_bar_volume": sum(volumes) / len(volumes) if volumes else 0.0,
    }


def _risk_summary(result: BacktestResult) -> dict[str, Any]:
    metrics = result.metrics
    closed = [item for item in result.trades if item.exit_time is not None]
    returns = [item.r_multiple for item in closed]
    downside = [value for value in returns if value < 0]
    avg_return = sum(returns) / len(returns) if returns else 0.0
    std_return = _sample_std(returns)
    downside_std = _sample_std(downside)
    return {
        "max_drawdown_pct": abs((metrics.get("max_drawdown") or 0.0) * 100),
        "return_to_drawdown": _safe_div(metrics.get("return_pct") or 0.0, abs((metrics.get("max_drawdown") or 0.0) * 100)),
        "trade_return_std_r": std_return,
        "sharpe_like_per_trade": _safe_div(avg_return, std_return),
        "sortino_like_per_trade": _safe_div(avg_return, downside_std),
        "largest_loss": metrics.get("worst_trade", 0.0),
        "fee_drag_pct_of_initial_equity": _safe_div(metrics.get("total_fees") or 0.0, metrics.get("initial_equity") or 0.0) * 100,
    }


def _trade_quality_summary(trades: list[BacktestTrade]) -> dict[str, Any]:
    wins = [item for item in trades if item.net_pnl > 0]
    losses = [item for item in trades if item.net_pnl < 0]
    win_values = [item.net_pnl for item in wins]
    loss_values = [item.net_pnl for item in losses]
    avg_win = sum(win_values) / len(win_values) if win_values else 0.0
    avg_loss = sum(loss_values) / len(loss_values) if loss_values else 0.0
    win_rate = len(wins) / len(trades) if trades else 0.0
    return {
        "expectancy": win_rate * avg_win + (1.0 - win_rate) * avg_loss if trades else 0.0,
        "average_win": avg_win,
        "average_loss": avg_loss,
        "payoff_ratio": _safe_div(avg_win, abs(avg_loss)),
        "largest_win": max(win_values) if win_values else 0.0,
        "largest_loss": min(loss_values) if loss_values else 0.0,
        "consecutive_wins": _max_streak(trades, True),
        "consecutive_losses": _max_streak(trades, False),
        "average_minutes_per_trade": sum(_minutes_held(item) for item in trades) / len(trades) if trades else 0.0,
    }


def _points_summary(trades: list[BacktestTrade]) -> dict[str, Any]:
    point_values = [item.points for item in trades]
    net_point_values = [item.net_points for item in trades]
    fee_point_values = [item.fee_points for item in trades]
    wins = [item for item in trades if item.points > 0]
    losses = [item for item in trades if item.points < 0]
    return {
        "total_points": sum(point_values),
        "total_net_points": sum(net_point_values),
        "average_points": sum(point_values) / len(point_values) if point_values else 0.0,
        "median_points": median(point_values) if point_values else 0.0,
        "best_points": max(point_values) if point_values else 0.0,
        "worst_points": min(point_values) if point_values else 0.0,
        "gross_profit_points": sum(item.points for item in wins),
        "gross_loss_points": abs(sum(item.points for item in losses)),
        "average_fee_points": sum(fee_point_values) / len(fee_point_values) if fee_point_values else 0.0,
        "point_win_rate": len(wins) / len(trades) if trades else 0.0,
    }


def _holding_time_summary(trades: list[BacktestTrade]) -> dict[str, Any]:
    durations = [_duration_seconds(item) for item in trades if item.exit_time is not None]
    if not durations:
        return {"average_seconds": 0, "median_seconds": 0, "max_seconds": 0, "min_seconds": 0}
    return {
        "average_seconds": sum(durations) / len(durations),
        "median_seconds": median(durations),
        "max_seconds": max(durations),
        "min_seconds": min(durations),
        "average_hours": sum(durations) / len(durations) / 3600,
        "median_hours": median(durations) / 3600,
    }


def _group_trade_summary(trades: list[BacktestTrade], field_name: str) -> dict[str, Any]:
    groups: dict[str, list[BacktestTrade]] = {}
    for trade in trades:
        groups.setdefault(str(getattr(trade, field_name) or "-"), []).append(trade)
    return {key: _trade_group_stats(items) for key, items in sorted(groups.items())}


def _period_trade_summary(trades: list[BacktestTrade], fmt: str) -> dict[str, Any]:
    groups: dict[str, list[BacktestTrade]] = {}
    for trade in trades:
        label = _timestamp_label(trade.entry_time, fmt)
        groups.setdefault(label, []).append(trade)
    return {key: _trade_group_stats(items) for key, items in sorted(groups.items())}


def _trade_group_stats(trades: list[BacktestTrade]) -> dict[str, Any]:
    pnl_values = [item.net_pnl for item in trades]
    wins = [item for item in trades if item.net_pnl > 0]
    return {
        "trade_count": len(trades),
        "net_pnl": sum(pnl_values),
        "points": sum(item.points for item in trades),
        "net_points": sum(item.net_points for item in trades),
        "average_pnl": sum(pnl_values) / len(pnl_values) if pnl_values else 0.0,
        "average_points": sum(item.points for item in trades) / len(trades) if trades else 0.0,
        "win_rate": len(wins) / len(trades) if trades else 0.0,
        "best_trade": max(pnl_values) if pnl_values else 0.0,
        "worst_trade": min(pnl_values) if pnl_values else 0.0,
        "best_points": max((item.points for item in trades), default=0.0),
        "worst_points": min((item.points for item in trades), default=0.0),
        "fees": sum(item.fees for item in trades),
    }


def _key_trades(trades: list[BacktestTrade]) -> dict[str, Any]:
    if not trades:
        return {}
    best = max(trades, key=lambda item: item.net_pnl)
    worst = min(trades, key=lambda item: item.net_pnl)
    longest = max(trades, key=_duration_seconds)
    return {
        "best": _compact_trade(best),
        "worst": _compact_trade(worst),
        "longest_holding": _compact_trade(longest),
    }


def _compact_trade(trade: BacktestTrade) -> dict[str, Any]:
    return {
        "id": trade.id,
        "side": trade.side,
        "entry_time": trade.entry_time_label,
        "exit_time": trade.exit_time_label,
        "entry_price": trade.entry_price,
        "exit_price": trade.exit_price,
        "points": trade.points,
        "net_points": trade.net_points,
        "net_pnl": trade.net_pnl,
        "r_multiple": trade.r_multiple,
        "exit_reason": trade.exit_reason,
        "holding_hours": _duration_seconds(trade) / 3600,
    }


def _markdown_report(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    headline = summary.get("headline") or {}
    analysis = report.get("analysis") or {}
    market = analysis.get("market") or {}
    risk = analysis.get("risk") or {}
    quality = analysis.get("trade_quality") or {}
    points = analysis.get("points") or {}
    lines = [
        f"# {summary.get('title', 'Backtest Report')}",
        "",
        "## Executive Summary",
        f"- Strategy: `{summary.get('strategy')}`",
        f"- Execution model: `{summary.get('execution_model')}`",
        f"- Period: {summary.get('period', {}).get('start', '')} -> {summary.get('period', {}).get('end', '')}",
        f"- Net profit: {_fmt(headline.get('net_profit'))} ({_fmt(headline.get('return_pct'))}%)",
        f"- Total points: {_fmt(headline.get('total_points'))} | Net points after fees: {_fmt(headline.get('total_net_points'))}",
        f"- Max drawdown: {_fmt(headline.get('max_drawdown_pct'))}%",
        f"- Trades: {headline.get('trade_count', 0)} | Win rate: {_fmt(headline.get('win_rate_pct'))}% | Profit factor: {_fmt(headline.get('profit_factor'))}",
        "",
        "## Market Context",
        f"- Market return: {_fmt(market.get('market_return_pct'))}%",
        f"- Range: {_fmt(market.get('range_pct'))}% | High: {_fmt(market.get('highest_high'))} | Low: {_fmt(market.get('lowest_low'))}",
        "",
        "## Trade Quality",
        f"- Expectancy: {_fmt(quality.get('expectancy'))}",
        f"- Average win / loss: {_fmt(quality.get('average_win'))} / {_fmt(quality.get('average_loss'))}",
        f"- Payoff ratio: {_fmt(quality.get('payoff_ratio'))}",
        f"- Max consecutive wins / losses: {quality.get('consecutive_wins', 0)} / {quality.get('consecutive_losses', 0)}",
        "",
        "## Points",
        f"- Gross points: {_fmt(points.get('total_points'))}",
        f"- Net points after fees: {_fmt(points.get('total_net_points'))}",
        f"- Best / worst points: {_fmt(points.get('best_points'))} / {_fmt(points.get('worst_points'))}",
        f"- Average fee points: {_fmt(points.get('average_fee_points'))}",
        "",
        "## Risk",
        f"- Return to drawdown: {_fmt(risk.get('return_to_drawdown'))}",
        f"- Sharpe-like per trade: {_fmt(risk.get('sharpe_like_per_trade'))}",
        f"- Fee drag: {_fmt(risk.get('fee_drag_pct_of_initial_equity'))}% of initial equity",
        "",
        "## Assumptions",
    ]
    lines.extend(f"- {item}" for item in analysis.get("assumptions") or [])
    lines.append("")
    return "\n".join(lines)


def _markdown_report_zh(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    headline = summary.get("headline") or {}
    analysis = report.get("analysis") or {}
    market = analysis.get("market") or {}
    risk = analysis.get("risk") or {}
    quality = analysis.get("trade_quality") or {}
    points = analysis.get("points") or {}
    holding = analysis.get("holding_time") or {}
    breakdowns = analysis.get("breakdowns") or {}
    lines = [
        f"# {summary.get('title', '回测报告')}",
        "",
        "## 核心结论",
        f"- 策略：`{summary.get('strategy')}`",
        f"- 撮合模型：`{summary.get('execution_model')}`",
        "- 仓位：1000U 保证金，10倍杠杆",
        f"- 回测区间：{summary.get('period', {}).get('start', '')} 至 {summary.get('period', {}).get('end', '')}",
        f"- K 线数量：{summary.get('period', {}).get('bars', 0)}",
        f"- 净收益：{_fmt(headline.get('net_profit'))}，收益率：{_fmt(headline.get('return_pct'))}%",
        f"- 总点数：{_fmt(headline.get('total_points'))}，扣费后等效点数：{_fmt(headline.get('total_net_points'))}",
        f"- 最大回撤：{_fmt(headline.get('max_drawdown_pct'))}%",
        f"- 交易次数：{headline.get('trade_count', 0)}，胜率：{_fmt(headline.get('win_rate_pct'))}%，盈利因子：{_fmt(headline.get('profit_factor'))}",
        f"- 说明：{summary.get('professional_note', '')}",
        "",
        "## 市场背景",
        f"- 区间起止收盘价：{_fmt(market.get('start_close'))} -> {_fmt(market.get('end_close'))}",
        f"- 标的区间涨跌幅：{_fmt(market.get('market_return_pct'))}%",
        f"- 区间最高/最低：{_fmt(market.get('highest_high'))} / {_fmt(market.get('lowest_low'))}",
        f"- 区间振幅：{_fmt(market.get('range_pct'))}%",
        f"- 总成交量/平均每根成交量：{_fmt(market.get('total_volume'))} / {_fmt(market.get('average_bar_volume'))}",
        "",
        "## 交易质量",
        f"- 单笔期望收益：{_fmt(quality.get('expectancy'))}",
        f"- 平均盈利/平均亏损：{_fmt(quality.get('average_win'))} / {_fmt(quality.get('average_loss'))}",
        f"- 盈亏比：{_fmt(quality.get('payoff_ratio'))}",
        f"- 最大单笔盈利/亏损：{_fmt(quality.get('largest_win'))} / {_fmt(quality.get('largest_loss'))}",
        f"- 最大连续盈利/亏损：{quality.get('consecutive_wins', 0)} / {quality.get('consecutive_losses', 0)}",
        f"- 平均持仓分钟数：{_fmt(quality.get('average_minutes_per_trade'))}",
        "",
        "## 点数表现",
        f"- 毛点数合计：{_fmt(points.get('total_points'))}",
        f"- 扣费后等效点数合计：{_fmt(points.get('total_net_points'))}",
        f"- 平均/中位点数：{_fmt(points.get('average_points'))} / {_fmt(points.get('median_points'))}",
        f"- 最佳/最差单笔点数：{_fmt(points.get('best_points'))} / {_fmt(points.get('worst_points'))}",
        f"- 盈利点数/亏损点数：{_fmt(points.get('gross_profit_points'))} / {_fmt(points.get('gross_loss_points'))}",
        f"- 平均手续费折算点数：{_fmt(points.get('average_fee_points'))}",
        "",
        "## 风险表现",
        f"- 收益回撤比：{_fmt(risk.get('return_to_drawdown'))}",
        f"- 每笔类 Sharpe：{_fmt(risk.get('sharpe_like_per_trade'))}",
        f"- 每笔类 Sortino：{_fmt(risk.get('sortino_like_per_trade'))}",
        f"- 单笔收益标准差 R：{_fmt(risk.get('trade_return_std_r'))}",
        f"- 手续费拖累：{_fmt(risk.get('fee_drag_pct_of_initial_equity'))}% 初始资金",
        "",
        "## 持仓时间",
        f"- 平均/中位持仓小时：{_fmt(holding.get('average_hours'))} / {_fmt(holding.get('median_hours'))}",
        f"- 最长/最短持仓秒数：{_fmt(holding.get('max_seconds'))} / {_fmt(holding.get('min_seconds'))}",
        "",
        "## 分组表现",
        "### 按方向",
        _markdown_group_table_zh(breakdowns.get("by_side") or {}),
        "",
        "### 按退出原因",
        _markdown_group_table_zh(breakdowns.get("by_exit_reason") or {}),
        "",
        "### 按进场日期",
        _markdown_group_table_zh(breakdowns.get("by_entry_day") or {}),
        "",
        "## 关键交易",
        _markdown_key_trades_zh(analysis.get("key_trades") or {}),
        "",
        "## 回测假设",
    ]
    lines.extend(f"- {item}" for item in analysis.get("assumptions") or [])
    lines.append("")
    return "\n".join(lines)


def _markdown_group_table_zh(groups: dict[str, Any]) -> str:
    if not groups:
        return "暂无数据"
    lines = [
        "| 分组 | 交易数 | 净收益 | 点数 | 平均点数 | 胜率 | 最佳点数 | 最差点数 | 手续费 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, stats in groups.items():
        lines.append(
            "| "
            f"{name} | "
            f"{stats.get('trade_count', 0)} | "
            f"{_fmt(stats.get('net_pnl'))} | "
            f"{_fmt(stats.get('points'))} | "
            f"{_fmt(stats.get('average_points'))} | "
            f"{_fmt((stats.get('win_rate') or 0.0) * 100)}% | "
            f"{_fmt(stats.get('best_points'))} | "
            f"{_fmt(stats.get('worst_points'))} | "
            f"{_fmt(stats.get('fees'))} |"
        )
    return "\n".join(lines)


def _markdown_key_trades_zh(key_trades: dict[str, Any]) -> str:
    if not key_trades:
        return "暂无数据"
    label_map = {
        "best": "最佳交易",
        "worst": "最差交易",
        "longest_holding": "最长持仓",
    }
    lines = []
    for key, trade in key_trades.items():
        lines.append(
            "- "
            f"{label_map.get(key, key)}：#{trade.get('id')} "
            f"{trade.get('side')}，"
            f"{trade.get('entry_time')} -> {trade.get('exit_time')}，"
            f"点数 {_fmt(trade.get('points'))}，"
            f"扣费后点数 {_fmt(trade.get('net_points'))}，"
            f"净收益 {_fmt(trade.get('net_pnl'))}，"
            f"R={_fmt(trade.get('r_multiple'))}，"
            f"退出原因 {trade.get('exit_reason')}"
        )
    return "\n".join(lines)


def _time_label(snapshot: dict[str, Any], time_value: int) -> str:
    if not time_value:
        return ""
    label = str((snapshot.get("time_labels") or {}).get(str(time_value)) or "").strip()
    return label or _timestamp_label(time_value, "%Y-%m-%d %H:%M:%S")


def _timestamp_label(timestamp: int | None, fmt: str) -> str:
    if not timestamp:
        return "-"
    return pd.Timestamp(timestamp, unit="s", tz="UTC").tz_convert(DISPLAY_TIMEZONE).strftime(fmt)


def _duration_seconds(trade: BacktestTrade) -> int:
    if trade.exit_time is None:
        return 0
    return max(int(trade.exit_time) - int(trade.entry_time), 0)


def _minutes_held(trade: BacktestTrade) -> float:
    duration = _duration_seconds(trade)
    return duration / 60 if duration else 0.0


def _sample_std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    average = sum(values) / len(values)
    variance = sum((value - average) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(variance)


def _safe_div(numerator: float | None, denominator: float | None) -> float | None:
    if denominator in (None, 0):
        return None
    return float(numerator or 0.0) / float(denominator)


def _max_streak(trades: list[BacktestTrade], winning: bool) -> int:
    best = 0
    current = 0
    for trade in trades:
        matched = trade.net_pnl > 0 if winning else trade.net_pnl < 0
        current = current + 1 if matched else 0
        best = max(best, current)
    return best


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, (int, float)):
        return f"{value:.4f}"
    return str(value)
