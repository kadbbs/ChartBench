from __future__ import annotations

import itertools
import math
from statistics import NormalDist
from typing import Any, Iterable, Sequence

import numpy as np


CRYPTO_PERIODS_PER_YEAR = 365.25
EULER_MASCHERONI = 0.5772156649015329


def performance_metrics(
    equity_values: Sequence[float],
    timestamps: Sequence[int],
    *,
    periods_per_year: float = CRYPTO_PERIODS_PER_YEAR,
    benchmark_annual_sharpe: float = 1.0,
    max_drawdown_pct: float | None = None,
    return_basis: str = "daily_mark_to_market_utc",
) -> dict[str, Any]:
    """Calculate distribution-, drawdown- and significance-aware metrics.

    ``equity_values`` contains an initial anchor followed by equally sampled
    observations. ``timestamps`` uses Unix seconds and must have the same
    length. Risk-free and minimum acceptable returns are intentionally zero:
    the value is written to the result so the UI does not hide that choice.
    """

    equity = np.asarray(equity_values, dtype="float64")
    times = np.asarray(timestamps, dtype="int64")
    if equity.ndim != 1 or times.ndim != 1 or len(equity) != len(times):
        raise ValueError("权益和时间序列必须是一维且长度相同。")
    if len(equity) < 2:
        return _empty_performance_metrics(return_basis)
    if not np.all(np.isfinite(equity)) or np.any(equity <= 0):
        return {
            **_empty_performance_metrics(return_basis),
            "performance_warning": "权益序列包含非正数或非有限值，无法计算复合收益统计。",
        }

    returns = equity[1:] / equity[:-1] - 1.0
    finite = np.isfinite(returns)
    returns = returns[finite]
    if not len(returns):
        return _empty_performance_metrics(return_basis)

    mean_return = float(np.mean(returns))
    volatility = float(np.std(returns, ddof=1)) if len(returns) > 1 else 0.0
    downside = np.minimum(returns, 0.0)
    downside_deviation = float(np.sqrt(np.mean(np.square(downside))))
    annualizer = math.sqrt(periods_per_year)
    daily_sharpe = mean_return / volatility if volatility > 0 else None
    sharpe = daily_sharpe * annualizer if daily_sharpe is not None else None
    sortino = (
        mean_return / downside_deviation * annualizer
        if downside_deviation > 0
        else None
    )
    skewness, pearson_kurtosis = _moments(returns)
    excess_kurtosis = (
        pearson_kurtosis - 3.0
        if pearson_kurtosis is not None
        else None
    )

    elapsed_days = max(
        (int(times[-1]) - int(times[0])) / 86_400.0,
        len(returns) / periods_per_year * 365.25,
        1.0 / periods_per_year * 365.25,
    )
    elapsed_years = elapsed_days / 365.25
    total_return = float(equity[-1] / equity[0] - 1.0)
    cagr = (
        float((equity[-1] / equity[0]) ** (1.0 / elapsed_years) - 1.0)
        if elapsed_years > 0
        else None
    )

    drawdowns = _drawdown_series(equity)
    daily_max_drawdown = abs(float(np.min(drawdowns))) * 100.0
    selected_max_drawdown = (
        max(float(max_drawdown_pct), 0.0)
        if max_drawdown_pct is not None
        else daily_max_drawdown
    )
    ulcer_index = float(np.sqrt(np.mean(np.square(drawdowns)))) * 100.0
    max_drawdown_duration = _max_drawdown_duration_days(equity, times)
    calmar = (
        cagr / (selected_max_drawdown / 100.0)
        if cagr is not None and selected_max_drawdown > 0
        else None
    )
    martin = (
        cagr / (ulcer_index / 100.0)
        if cagr is not None and ulcer_index > 0
        else None
    )

    positive_excess = float(np.sum(np.maximum(returns, 0.0)))
    negative_excess = abs(float(np.sum(np.minimum(returns, 0.0))))
    omega = positive_excess / negative_excess if negative_excess > 0 else None
    quantile_95 = float(np.quantile(returns, 0.05))
    tail_count = max(int(math.ceil(len(returns) * 0.05)), 1)
    tail_95 = np.sort(returns)[:tail_count]
    var_95 = max(-quantile_95, 0.0) * 100.0
    expected_shortfall_95 = (
        max(-float(np.mean(tail_95)), 0.0) * 100.0
        if len(tail_95)
        else 0.0
    )

    psr_zero = probabilistic_sharpe_ratio(
        observed_sharpe=daily_sharpe,
        benchmark_sharpe=0.0,
        observation_count=len(returns),
        skewness=skewness,
        pearson_kurtosis=pearson_kurtosis,
    )
    benchmark_daily = benchmark_annual_sharpe / annualizer
    psr_benchmark = probabilistic_sharpe_ratio(
        observed_sharpe=daily_sharpe,
        benchmark_sharpe=benchmark_daily,
        observation_count=len(returns),
        skewness=skewness,
        pearson_kurtosis=pearson_kurtosis,
    )
    min_track_record = minimum_track_record_length(
        observed_sharpe=daily_sharpe,
        benchmark_sharpe=benchmark_daily,
        skewness=skewness,
        pearson_kurtosis=pearson_kurtosis,
        confidence=0.95,
    )

    return {
        "performance_return_basis": return_basis,
        "performance_periods_per_year": periods_per_year,
        "performance_risk_free_rate_pct": 0.0,
        "performance_minimum_acceptable_return_pct": 0.0,
        "performance_observation_count": int(len(returns)),
        "performance_calendar_days": float(elapsed_days),
        "performance_period_return_pct": total_return * 100.0,
        "cagr_pct": cagr * 100.0 if cagr is not None else None,
        "annualized_volatility_pct": volatility * annualizer * 100.0,
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "calmar_ratio": calmar,
        "omega_ratio": omega,
        "ulcer_index_pct": ulcer_index,
        "martin_ratio": martin,
        "daily_max_drawdown_pct": daily_max_drawdown,
        "max_drawdown_duration_days": max_drawdown_duration,
        "daily_var_95_pct": var_95,
        "daily_expected_shortfall_95_pct": expected_shortfall_95,
        "return_skewness": skewness,
        "return_excess_kurtosis": excess_kurtosis,
        "psr_zero_pct": psr_zero * 100.0 if psr_zero is not None else None,
        "psr_benchmark_annual_sharpe": benchmark_annual_sharpe,
        "psr_benchmark_pct": (
            psr_benchmark * 100.0
            if psr_benchmark is not None
            else None
        ),
        "minimum_track_record_days_95": min_track_record,
        "deflated_sharpe_ratio_pct": None,
        "deflated_sharpe_benchmark": None,
        "_daily_sharpe": daily_sharpe,
        "_daily_returns": returns.tolist(),
    }


def probabilistic_sharpe_ratio(
    *,
    observed_sharpe: float | None,
    benchmark_sharpe: float,
    observation_count: int,
    skewness: float | None,
    pearson_kurtosis: float | None,
) -> float | None:
    """Probability that the true Sharpe exceeds ``benchmark_sharpe``.

    This is Bailey and López de Prado's PSR using the first four moments of
    the periodic return distribution. Sharpe inputs must use that same period.
    """

    if (
        observed_sharpe is None
        or skewness is None
        or pearson_kurtosis is None
        or observation_count < 2
    ):
        return None
    denominator_term = (
        1.0
        - skewness * observed_sharpe
        + ((pearson_kurtosis - 1.0) / 4.0) * observed_sharpe**2
    )
    if denominator_term <= 0:
        return None
    statistic = (
        (observed_sharpe - benchmark_sharpe)
        * math.sqrt(observation_count - 1)
        / math.sqrt(denominator_term)
    )
    return NormalDist().cdf(statistic)


def minimum_track_record_length(
    *,
    observed_sharpe: float | None,
    benchmark_sharpe: float,
    skewness: float | None,
    pearson_kurtosis: float | None,
    confidence: float = 0.95,
) -> float | None:
    """Periodic observations needed for PSR to exceed ``confidence``."""

    if (
        observed_sharpe is None
        or observed_sharpe <= benchmark_sharpe
        or skewness is None
        or pearson_kurtosis is None
        or not 0.5 < confidence < 1.0
    ):
        return None
    denominator_term = (
        1.0
        - skewness * observed_sharpe
        + ((pearson_kurtosis - 1.0) / 4.0) * observed_sharpe**2
    )
    spread = observed_sharpe - benchmark_sharpe
    if denominator_term <= 0 or spread <= 0:
        return None
    z_value = NormalDist().inv_cdf(confidence)
    return 1.0 + denominator_term * (z_value / spread) ** 2


def attach_deflated_sharpe(
    rows: Sequence[dict[str, Any]],
    *,
    periods_per_year: float = CRYPTO_PERIODS_PER_YEAR,
) -> dict[str, Any]:
    """Attach DSR to every matrix row using all current grid trials."""

    daily_sharpes = [
        float(item["_daily_sharpe"])
        for item in rows
        if item.get("_daily_sharpe") is not None
        and math.isfinite(float(item["_daily_sharpe"]))
    ]
    trial_count = len(rows)
    if trial_count < 2 or len(daily_sharpes) < 2:
        for row in rows:
            row["deflated_sharpe_ratio_pct"] = None
            row["deflated_sharpe_benchmark"] = None
        return {
            "trial_count": trial_count,
            "deflated_sharpe_available": False,
            "deflated_sharpe_note": "至少需要两个有效参数组合。",
        }

    trial_std = float(np.std(np.asarray(daily_sharpes), ddof=1))
    expected_maximum = expected_maximum_sharpe(trial_std, trial_count)
    annualized_benchmark = expected_maximum * math.sqrt(periods_per_year)
    for row in rows:
        daily_sharpe = row.get("_daily_sharpe")
        excess_kurtosis = _optional_number(row.get("return_excess_kurtosis"))
        psr = probabilistic_sharpe_ratio(
            observed_sharpe=(
                float(daily_sharpe)
                if daily_sharpe is not None
                else None
            ),
            benchmark_sharpe=expected_maximum,
            observation_count=int(row.get("performance_observation_count") or 0),
            skewness=_optional_number(row.get("return_skewness")),
            pearson_kurtosis=(
                excess_kurtosis + 3.0
                if excess_kurtosis is not None
                else None
            ),
        )
        row["deflated_sharpe_ratio_pct"] = (
            psr * 100.0
            if psr is not None
            else None
        )
        row["deflated_sharpe_benchmark"] = annualized_benchmark

    return {
        "trial_count": trial_count,
        "valid_sharpe_trial_count": len(daily_sharpes),
        "trial_sharpe_std": trial_std * math.sqrt(periods_per_year),
        "deflated_sharpe_available": True,
        "deflated_sharpe_benchmark": annualized_benchmark,
        "deflated_sharpe_note": (
            "按本次参数矩阵的全部组合数保守校正；未包含本次运行之前人工尝试过的参数。"
        ),
    }


def expected_maximum_sharpe(trial_sharpe_std: float, trial_count: int) -> float:
    if trial_count < 2 or trial_sharpe_std <= 0:
        return 0.0
    normal = NormalDist()
    first = normal.inv_cdf(1.0 - 1.0 / trial_count)
    second = normal.inv_cdf(1.0 - 1.0 / (trial_count * math.e))
    return trial_sharpe_std * (
        (1.0 - EULER_MASCHERONI) * first
        + EULER_MASCHERONI * second
    )


def cscv_probability_of_backtest_overfitting(
    return_series: Sequence[Sequence[float]],
    *,
    periods_per_year: float = CRYPTO_PERIODS_PER_YEAR,
    max_segments: int = 8,
) -> dict[str, Any]:
    """Estimate matrix-level PBO using combinatorially symmetric CV.

    ``return_series`` is strategy-major and all strategies must share the same
    ordered periodic observations.
    """

    if len(return_series) < 2:
        return _unavailable_pbo("至少需要两个参数组合。")
    lengths = {len(item) for item in return_series}
    if len(lengths) != 1:
        return _unavailable_pbo("各参数组合的收益序列长度不一致。")
    observation_count = next(iter(lengths), 0)
    if observation_count < 40:
        return _unavailable_pbo("CSCV 至少需要 40 个等间隔收益观察值。")

    segment_count = min(max_segments, observation_count // 10)
    if segment_count % 2:
        segment_count -= 1
    if segment_count < 4:
        return _unavailable_pbo("CSCV 至少需要四个时间分块。")

    matrix = np.asarray(return_series, dtype="float64").T
    if matrix.ndim != 2 or not np.all(np.isfinite(matrix)):
        return _unavailable_pbo("收益矩阵包含非有限值。")
    blocks = np.array_split(np.arange(observation_count), segment_count)
    half = segment_count // 2
    logits: list[float] = []
    is_values: list[float] = []
    oos_values: list[float] = []
    degradation_values: list[float] = []

    for chosen in itertools.combinations(range(segment_count), half):
        chosen_set = set(chosen)
        is_index = np.concatenate([blocks[index] for index in chosen])
        oos_index = np.concatenate(
            [
                blocks[index]
                for index in range(segment_count)
                if index not in chosen_set
            ]
        )
        is_scores = np.asarray(
            [_selection_sharpe(matrix[is_index, column]) for column in range(matrix.shape[1])]
        )
        oos_scores = np.asarray(
            [_selection_sharpe(matrix[oos_index, column]) for column in range(matrix.shape[1])]
        )
        selected = int(np.argmax(is_scores))
        rank = _average_ascending_rank(oos_scores, selected)
        relative_rank = rank / (matrix.shape[1] + 1.0)
        logits.append(math.log(relative_rank / (1.0 - relative_rank)))
        selected_is = float(is_scores[selected]) * math.sqrt(periods_per_year)
        selected_oos = float(oos_scores[selected]) * math.sqrt(periods_per_year)
        is_values.append(selected_is)
        oos_values.append(selected_oos)
        degradation_values.append(selected_oos - selected_is)

    if not logits:
        return _unavailable_pbo("没有生成有效的 CSCV 切分。")
    return {
        "pbo_available": True,
        "pbo_pct": sum(1 for value in logits if value <= 0.0) / len(logits) * 100.0,
        "cscv_segment_count": segment_count,
        "cscv_split_count": len(logits),
        "cscv_observation_count": observation_count,
        "cscv_median_selected_is_sharpe": float(np.median(is_values)),
        "cscv_median_selected_oos_sharpe": float(np.median(oos_values)),
        "cscv_median_sharpe_degradation": float(np.median(degradation_values)),
        "pbo_interpretation": (
            "PBO 越低越好；它衡量样本内获胜组合在样本外跌到全部组合中位数以下的频率。"
        ),
    }


def realized_daily_equity(
    *,
    initial_equity: float,
    pnl_events: Iterable[tuple[int, float]],
    start_time: int,
    end_time: int,
) -> tuple[list[int], list[float]]:
    """Build a UTC daily realized-equity series for legacy backtests."""

    start = int(start_time)
    end = max(int(end_time), start)
    events = sorted((int(timestamp), float(pnl)) for timestamp, pnl in pnl_events)
    event_index = 0
    equity = float(initial_equity)
    anchor = start - 1
    times = [anchor]
    values = [equity]
    first_day = start // 86_400
    last_day = end // 86_400
    for day in range(first_day, last_day + 1):
        day_end = min((day + 1) * 86_400 - 1, end)
        while event_index < len(events) and events[event_index][0] <= day_end:
            equity += events[event_index][1]
            event_index += 1
        times.append(day_end)
        values.append(equity)
    return times, values


def strip_private_performance_fields(payload: dict[str, Any]) -> None:
    for key in tuple(payload):
        if key.startswith("_"):
            payload.pop(key, None)


def _moments(returns: np.ndarray) -> tuple[float | None, float | None]:
    if len(returns) < 3:
        return None, None
    centered = returns - np.mean(returns)
    variance = float(np.mean(np.square(centered)))
    if variance <= 0:
        return 0.0, 3.0
    scale = math.sqrt(variance)
    skewness = float(np.mean((centered / scale) ** 3))
    pearson_kurtosis = float(np.mean((centered / scale) ** 4))
    return skewness, pearson_kurtosis


def _drawdown_series(equity: np.ndarray) -> np.ndarray:
    peaks = np.maximum.accumulate(equity)
    safe_peaks = np.maximum(peaks, 1e-12)
    return equity / safe_peaks - 1.0


def _max_drawdown_duration_days(
    equity: np.ndarray,
    timestamps: np.ndarray,
) -> float:
    peak_value = float(equity[0])
    peak_time = int(timestamps[0])
    longest = 0.0
    for value, timestamp in zip(equity[1:], timestamps[1:]):
        numeric = float(value)
        current_time = int(timestamp)
        if numeric >= peak_value:
            peak_value = numeric
            peak_time = current_time
        else:
            longest = max(longest, (current_time - peak_time) / 86_400.0)
    return longest


def _selection_sharpe(returns: np.ndarray) -> float:
    mean_return = float(np.mean(returns))
    volatility = float(np.std(returns, ddof=1)) if len(returns) > 1 else 0.0
    if volatility > 1e-15:
        return mean_return / volatility
    if mean_return > 0:
        return 1e6
    if mean_return < 0:
        return -1e6
    return 0.0


def _average_ascending_rank(values: np.ndarray, selected: int) -> float:
    target = float(values[selected])
    lower = int(np.sum(values < target))
    equal = int(np.sum(values == target))
    return lower + (equal + 1.0) / 2.0


def _unavailable_pbo(reason: str) -> dict[str, Any]:
    return {
        "pbo_available": False,
        "pbo_pct": None,
        "cscv_segment_count": None,
        "cscv_split_count": 0,
        "cscv_observation_count": 0,
        "cscv_median_selected_is_sharpe": None,
        "cscv_median_selected_oos_sharpe": None,
        "cscv_median_sharpe_degradation": None,
        "pbo_interpretation": reason,
    }


def _empty_performance_metrics(return_basis: str) -> dict[str, Any]:
    return {
        "performance_return_basis": return_basis,
        "performance_periods_per_year": CRYPTO_PERIODS_PER_YEAR,
        "performance_risk_free_rate_pct": 0.0,
        "performance_minimum_acceptable_return_pct": 0.0,
        "performance_observation_count": 0,
        "performance_calendar_days": 0.0,
        "performance_period_return_pct": None,
        "cagr_pct": None,
        "annualized_volatility_pct": None,
        "sharpe_ratio": None,
        "sortino_ratio": None,
        "calmar_ratio": None,
        "omega_ratio": None,
        "ulcer_index_pct": None,
        "martin_ratio": None,
        "daily_max_drawdown_pct": None,
        "max_drawdown_duration_days": None,
        "daily_var_95_pct": None,
        "daily_expected_shortfall_95_pct": None,
        "return_skewness": None,
        "return_excess_kurtosis": None,
        "psr_zero_pct": None,
        "psr_benchmark_annual_sharpe": 1.0,
        "psr_benchmark_pct": None,
        "minimum_track_record_days_95": None,
        "deflated_sharpe_ratio_pct": None,
        "deflated_sharpe_benchmark": None,
        "_daily_sharpe": None,
        "_daily_returns": [],
    }


def _optional_number(value: Any) -> float | None:
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None
