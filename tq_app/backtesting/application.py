from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any

from tq_app.config_profiles import load_backtest_profile, load_layered_env

from .config import build_backtest_live_config
from .engine import (
    DEFAULT_BACKTEST_FEE_RATE,
    BacktestConfig,
    BacktestEngine,
    BacktestResult,
    PreparedBacktestStudy,
)
from .runtime import (
    BacktestMarketRequest,
    PreparedBacktestMarket,
    parse_time_ms,
    prepare_backtest_market,
    resolve_data_length,
)
from .strategies import KlineStrategy, build_strategy


@dataclass(slots=True)
class BacktestRunRequest:
    profile: str = ""
    provider: str = "bitget"
    symbol: str = "BTCUSDT"
    duration_seconds: int = 300
    data_length: int = 800
    strategy: str = "live_decision"
    product_type: str = "USDT-FUTURES"
    kline_type: str = "MARKET"
    start_time: str = ""
    end_time: str = ""
    initial_equity: float = 20_000.0
    risk_per_trade: float = 0.01
    margin_amount: float = 1_000.0
    margin_ratio_per_trade: float = 0.0
    leverage: float = 10.0
    fee_rate: float = DEFAULT_BACKTEST_FEE_RATE
    slippage_rate: float = 0.0
    warmup_bars: int = 80
    risk_exits_enabled: bool = True
    startup_check_bars_5m: int = 24
    startup_max_favorable_points: float = 300.0
    startup_current_points: float = -150.0
    disaster_stop_points: float = -1800.0
    breakeven_trigger_points: float = 800.0
    breakeven_stop_points: float = 100.0
    trailing_trigger_1_points: float = 2000.0
    trailing_protect_1_ratio: float = 0.40
    trailing_trigger_2_points: float = 4000.0
    trailing_protect_2_ratio: float = 0.50
    trailing_trigger_3_points: float = 8000.0
    trailing_protect_3_ratio: float = 0.60
    output_dir: Path = Path("backtest_outputs/latest")
    cache_enabled: bool = False
    cache_dir: Path = Path("data_cache/backtest_klines")
    indicator_params: dict[str, dict[str, Any]] = field(default_factory=dict)
    extra_context: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ResolvedBacktestRun:
    request: BacktestRunRequest
    profile_values: dict[str, str]
    live_config: Any
    strategy: KlineStrategy
    market_request: BacktestMarketRequest
    config: BacktestConfig


class BacktestApplication:
    """Shared application boundary for CLI, Web and experiment runners."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root

    def resolve(self, request: BacktestRunRequest) -> ResolvedBacktestRun:
        load_layered_env(self.project_root)
        self._validate_request(request)
        profile_values = load_backtest_profile(self.project_root, request.profile)
        live_config = build_backtest_live_config(self.project_root, profile_values)
        strategy = build_strategy(request.strategy, self.project_root, live_config)
        start_time_ms = parse_time_ms(request.start_time)
        end_time_ms = parse_time_ms(request.end_time)
        if start_time_ms is not None and end_time_ms is None:
            raise ValueError("start_time 需要同时指定 end_time。")
        if start_time_ms is not None and end_time_ms is not None and start_time_ms >= end_time_ms:
            raise ValueError("start_time 必须早于 end_time。")
        data_length = resolve_data_length(
            requested_length=request.data_length,
            duration_seconds=request.duration_seconds,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
        )
        market_request = BacktestMarketRequest(
            provider=request.provider,
            symbol=request.symbol,
            product_type=request.product_type,
            duration_seconds=request.duration_seconds,
            data_length=data_length,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
            kline_type=request.kline_type,
            cache_enabled=request.cache_enabled,
            cache_dir=request.cache_dir,
        )
        market_context = {
            "provider": request.provider,
            "symbol": request.symbol.upper(),
            "product_type": request.product_type,
            "kline_type": request.kline_type,
            "duration_seconds": request.duration_seconds,
            "data_length": data_length,
            "start_time_ms": start_time_ms,
            "end_time_ms": end_time_ms,
            "cache_enabled": request.cache_enabled,
            "cache_dir": str(request.cache_dir),
        }
        run_context = {
            "profile": request.profile or None,
            "profile_values": profile_values,
            "market_data": market_context,
            **request.extra_context,
        }
        config = BacktestConfig(
            symbol=request.symbol.upper(),
            provider=request.provider,
            duration_seconds=request.duration_seconds,
            initial_equity=request.initial_equity,
            risk_per_trade=request.risk_per_trade,
            margin_amount=request.margin_amount,
            margin_ratio_per_trade=request.margin_ratio_per_trade,
            leverage=request.leverage,
            fee_rate=request.fee_rate,
            slippage_rate=request.slippage_rate,
            stop_atr_multiplier=float(live_config.stop_atr_multiplier),
            tp1_r_multiple=float(live_config.tp1_r_multiple),
            tp1_size_ratio=float(live_config.tp1_size_ratio),
            tp2_r_multiple=float(live_config.tp2_r_multiple),
            atr_period=live_config.atr_period,
            warmup_bars=request.warmup_bars,
            risk_exits_enabled=request.risk_exits_enabled,
            startup_check_bars_5m=request.startup_check_bars_5m,
            startup_max_favorable_points=request.startup_max_favorable_points,
            startup_current_points=request.startup_current_points,
            disaster_stop_points=request.disaster_stop_points,
            breakeven_trigger_points=request.breakeven_trigger_points,
            breakeven_stop_points=request.breakeven_stop_points,
            trailing_trigger_1_points=request.trailing_trigger_1_points,
            trailing_protect_1_ratio=request.trailing_protect_1_ratio,
            trailing_trigger_2_points=request.trailing_trigger_2_points,
            trailing_protect_2_ratio=request.trailing_protect_2_ratio,
            trailing_trigger_3_points=request.trailing_trigger_3_points,
            trailing_protect_3_ratio=request.trailing_protect_3_ratio,
            run_context=run_context,
            output_dir=request.output_dir,
        )
        return ResolvedBacktestRun(
            request=request,
            profile_values=profile_values,
            live_config=live_config,
            strategy=strategy,
            market_request=market_request,
            config=config,
        )

    def prepare_market(self, resolved: ResolvedBacktestRun) -> PreparedBacktestMarket:
        return prepare_backtest_market(
            project_root=self.project_root,
            request=resolved.market_request,
            live_config=resolved.live_config,
            strategy=resolved.strategy,
        )

    def run(
        self,
        request: BacktestRunRequest,
        *,
        prepared_market: PreparedBacktestMarket | None = None,
        prepared_study: PreparedBacktestStudy | None = None,
        write_artifacts: bool = True,
    ) -> BacktestResult:
        resolved = self.resolve(request)
        prepared = prepared_market or self.prepare_market(resolved)
        return self._engine(resolved, write_artifacts=write_artifacts).run(
            prepared.bars,
            prepared.htf_bars,
            prepared.reentry_htf_bars,
            prepared_study=prepared_study,
        )

    def prepare_study(
        self,
        resolved: ResolvedBacktestRun,
        prepared_market: PreparedBacktestMarket,
    ) -> PreparedBacktestStudy:
        return self._engine(resolved, write_artifacts=False).prepare_study(
            prepared_market.bars,
            prepared_market.htf_bars,
            prepared_market.reentry_htf_bars,
        )

    def _engine(self, resolved: ResolvedBacktestRun, *, write_artifacts: bool) -> BacktestEngine:
        return BacktestEngine(
            project_root=self.project_root,
            config=resolved.config,
            live_config=resolved.live_config,
            strategy=resolved.strategy,
            indicator_params=resolved.request.indicator_params,
            write_artifacts=write_artifacts,
        )

    @staticmethod
    def _validate_request(request: BacktestRunRequest) -> None:
        if request.provider not in {"binance", "bitget"}:
            raise ValueError("回测 provider 仅支持 binance 或 bitget。")
        if not request.symbol.strip():
            raise ValueError("symbol 不能为空。")
        if re.fullmatch(r"[A-Za-z0-9_-]{2,40}", request.symbol.strip()) is None:
            raise ValueError("symbol 只能包含字母、数字、下划线和连字符。")
        if re.fullmatch(r"[A-Za-z0-9_-]{2,40}", request.product_type.strip()) is None:
            raise ValueError("product_type 格式无效。")
        if re.fullmatch(r"[A-Za-z0-9_-]{2,20}", request.kline_type.strip()) is None:
            raise ValueError("kline_type 格式无效。")
        if request.duration_seconds <= 0 or request.data_length <= 0:
            raise ValueError("K 线周期和数量必须是正整数。")
        if request.initial_equity <= 0:
            raise ValueError("初始权益必须大于 0。")
        if request.margin_amount <= 0 and request.margin_ratio_per_trade <= 0:
            raise ValueError("固定保证金或权益比例必须至少有一项大于 0。")
        if request.leverage <= 0:
            raise ValueError("杠杆必须大于 0。")
        if request.fee_rate < 0 or request.slippage_rate < 0:
            raise ValueError("手续费率和滑点率不能为负数。")
        if request.warmup_bars < 1:
            raise ValueError("warmup_bars 必须大于 0。")
