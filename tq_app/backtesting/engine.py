from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
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
    initial_equity: float = 10_000.0
    risk_per_trade: float = 0.01
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
    stop_price: float
    tp1_price: float
    tp2_price: float
    qty: float
    exit_time: int | None = None
    exit_time_label: str = ""
    exit_price: float | None = None
    exit_reason: str = ""
    pnl: float = 0.0
    fees: float = 0.0
    net_pnl: float = 0.0
    r_multiple: float = 0.0
    signal_reason: str = ""
    partial_exits: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class Position:
    trade: BacktestTrade
    remaining_qty: float
    risk_amount: float
    tp1_done: bool = False


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
                indicator_ids=["merged_dkx_hull_ut"],
            )
            htf_snapshot = htf_builder.build_full(htf_bars)

        candles = full_snapshot["candles"]
        time_labels = full_snapshot.get("time_labels") or {}
        atr_values = _atr_values(candles, self.config.atr_period)
        equity = float(self.config.initial_equity)
        equity_curve: list[float] = [equity]
        trades: list[BacktestTrade] = []
        markers: list[dict[str, Any]] = []
        position: Position | None = None
        next_trade_id = 1

        for entry_index in range(max(self.config.warmup_bars, 1), len(candles)):
            candle = candles[entry_index]
            if position is None:
                signal_index = entry_index - 1
                signal_candle = candles[signal_index]
                snapshot = slice_snapshot(full_snapshot, entry_index + 1)
                snapshot = attach_higher_timeframe(snapshot, htf_snapshot, int(candle["time"]))
                signal = self.strategy.evaluate(snapshot)
                if signal.side in {"buy", "sell"}:
                    atr = atr_values[signal_index]
                    if atr is not None and atr > 0:
                        trade, position = self._open_position(
                            trade_id=next_trade_id,
                            side=signal.side,
                            signal_candle=signal_candle,
                            entry_candle=candle,
                            atr=atr,
                            equity=equity,
                            reason=signal.reason,
                            time_labels=time_labels,
                        )
                        next_trade_id += 1
                        trades.append(trade)
                        markers.append(_marker(candle["time"], "belowBar" if signal.side == "buy" else "aboveBar", "#4caf50" if signal.side == "buy" else "#f23645", f"OPEN {signal.side.upper()}"))

            if position is not None:
                realized = self._process_position_bar(position, candle, time_labels)
                if realized:
                    equity += realized
                    equity_curve.append(equity)
                    if position.remaining_qty <= 0:
                        markers.append(_marker(candle["time"], "aboveBar" if position.trade.side == "buy" else "belowBar", "#ff9800", f"CLOSE {position.trade.exit_reason}"))
                        position = None

        if position is not None:
            last_candle = candles[-1]
            equity += self._close_remaining(position, last_candle, "end_of_data", time_labels)
            equity_curve.append(equity)
            markers.append(_marker(last_candle["time"], "aboveBar" if position.trade.side == "buy" else "belowBar", "#ff9800", "CLOSE END"))

        metrics = _metrics(trades, equity_curve, self.config.initial_equity)
        result = BacktestResult(
            config={**asdict(self.config), "output_dir": str(self.config.output_dir), "strategy": self.strategy.name},
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
        atr: float,
        equity: float,
        reason: str,
        time_labels: dict[str, str],
    ) -> tuple[BacktestTrade, Position]:
        entry_price = _apply_slippage(float(entry_candle["open"]), side, self.config.slippage_rate)
        risk_per_unit = atr * self.config.stop_atr_multiplier
        risk_amount = equity * self.config.risk_per_trade
        qty = risk_amount / risk_per_unit
        direction = 1.0 if side == "buy" else -1.0
        stop_price = entry_price - direction * risk_per_unit
        tp1_price = entry_price + direction * risk_per_unit * self.config.tp1_r_multiple
        tp2_price = entry_price + direction * risk_per_unit * self.config.tp2_r_multiple
        trade = BacktestTrade(
            id=trade_id,
            side=side,
            signal_time=int(signal_candle["time"]),
            signal_time_label=time_labels.get(str(signal_candle["time"]), ""),
            entry_time=int(entry_candle["time"]),
            entry_time_label=time_labels.get(str(entry_candle["time"]), ""),
            entry_price=entry_price,
            stop_price=stop_price,
            tp1_price=tp1_price,
            tp2_price=tp2_price,
            qty=qty,
            signal_reason=reason,
        )
        trade.fees += abs(qty * entry_price) * self.config.fee_rate
        return trade, Position(trade=trade, remaining_qty=qty, risk_amount=risk_amount)

    def _process_position_bar(self, position: Position, candle: dict[str, Any], time_labels: dict[str, str]) -> float:
        side = position.trade.side
        high = float(candle["high"])
        low = float(candle["low"])
        realized = 0.0

        stop_hit = low <= position.trade.stop_price if side == "buy" else high >= position.trade.stop_price
        tp1_hit = high >= position.trade.tp1_price if side == "buy" else low <= position.trade.tp1_price
        tp2_hit = high >= position.trade.tp2_price if side == "buy" else low <= position.trade.tp2_price

        if stop_hit:
            return self._close_remaining(position, candle, "stop_loss", time_labels, price=position.trade.stop_price)
        if tp1_hit and not position.tp1_done:
            qty = position.trade.qty * self.config.tp1_size_ratio
            qty = min(qty, position.remaining_qty)
            realized += self._close_partial(position, candle, qty, position.trade.tp1_price, "tp1", time_labels)
            position.tp1_done = True
        if tp2_hit and position.remaining_qty > 0:
            realized += self._close_remaining(position, candle, "tp2", time_labels, price=position.trade.tp2_price)
        return realized

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
        fee = abs(qty * price) * self.config.fee_rate
        position.remaining_qty -= qty
        position.trade.pnl += pnl
        position.trade.fees += fee
        position.trade.partial_exits.append(
            {
                "time": int(candle["time"]),
                "time_label": time_labels.get(str(candle["time"]), ""),
                "price": price,
                "qty": qty,
                "reason": reason,
                "pnl": pnl,
                "fee": fee,
            }
        )
        position.trade.net_pnl = position.trade.pnl - position.trade.fees
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
        trade.r_multiple = trade.net_pnl / position.risk_amount if position.risk_amount else 0.0
        position.remaining_qty = 0.0
        return realized

    def write_outputs(self, result: BacktestResult, snapshot: dict[str, Any]) -> None:
        output_dir = Path(result.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        report_payload = {
            "config": result.config,
            "metrics": result.metrics,
            "trades": [asdict(item) for item in result.trades],
        }
        (output_dir / "report.json").write_text(json.dumps(report_payload, ensure_ascii=False, indent=2), encoding="utf-8")
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


def _atr_values(candles: list[dict[str, Any]], period: int) -> list[float | None]:
    values: list[float | None] = [None] * len(candles)
    true_ranges: list[float] = []
    for index, candle in enumerate(candles):
        if index == 0:
            true_ranges.append(float(candle["high"]) - float(candle["low"]))
            continue
        prev_close = float(candles[index - 1]["close"])
        tr = max(
            float(candle["high"]) - float(candle["low"]),
            abs(float(candle["high"]) - prev_close),
            abs(float(candle["low"]) - prev_close),
        )
        true_ranges.append(tr)
        if index + 1 >= period:
            values[index] = sum(true_ranges[index + 1 - period : index + 1]) / period
    return values


def _pnl(side: str, entry: float, exit_price: float, qty: float) -> float:
    direction = 1.0 if side == "buy" else -1.0
    return (exit_price - entry) * direction * qty


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
    return {
        "initial_equity": initial_equity,
        "final_equity": final_equity,
        "net_profit": final_equity - initial_equity,
        "return_pct": (final_equity / initial_equity - 1.0) * 100 if initial_equity else 0.0,
        "trade_count": len(closed),
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate": len(wins) / len(closed) if closed else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss else None,
        "max_drawdown": _max_drawdown(equity_curve),
        "average_r": sum(item.r_multiple for item in closed) / len(closed) if closed else 0.0,
    }


def _max_drawdown(equity_curve: list[float]) -> float:
    peak = equity_curve[0] if equity_curve else 0.0
    max_dd = 0.0
    for equity in equity_curve:
        peak = max(peak, equity)
        if peak:
            max_dd = min(max_dd, equity / peak - 1.0)
    return max_dd
