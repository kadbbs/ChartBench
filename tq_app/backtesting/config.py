from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from tq_app.live_trading import LiveTradingConfig


ProfileParser = Callable[[str], Any]


def build_backtest_live_config(project_root: Path, profile: dict[str, str]) -> LiveTradingConfig:
    """Build the signal configuration used by a backtest.

    Environment/default values remain the fallback for ad-hoc runs. Named
    profiles can pin every signal-affecting value so a later .env or defaults
    change does not silently alter historical results.
    """
    return apply_backtest_signal_profile(LiveTradingConfig.from_env(project_root), profile)


def apply_backtest_signal_profile(config: LiveTradingConfig, profile: dict[str, str]) -> LiveTradingConfig:
    resolved = replace(config)
    fields: dict[str, tuple[str, ProfileParser]] = {
        "signal_strategy": ("strategy", _parse_string),
        "signal_mode": ("signal_mode", _parse_string),
        "use_closed_bar": ("use_closed_bar", _parse_bool),
        "htf_hull_filter_enabled": ("htf_hull_filter_enabled", _parse_bool),
        "htf_hull_duration_seconds": ("htf_hull_duration_seconds", _parse_positive_int),
        "atr_period": ("atr_period", _parse_positive_int),
        "stop_atr_multiplier": ("stop_atr_multiplier", _parse_string),
        "tp1_r_multiple": ("tp1_r_multiple", _parse_string),
        "tp1_size_ratio": ("tp1_size_ratio", _parse_string),
        "tp2_r_multiple": ("tp2_r_multiple", _parse_string),
    }
    for profile_key, (attribute, parser) in fields.items():
        if profile_key not in profile:
            continue
        try:
            value = parser(str(profile[profile_key]))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"回测 profile 的 {profile_key} 无效: {profile[profile_key]!r}") from exc
        setattr(resolved, attribute, value)
    return resolved


def backtest_signal_config_snapshot(config: LiveTradingConfig) -> dict[str, Any]:
    return {
        "strategy": config.strategy,
        "signal_mode": config.signal_mode,
        "use_closed_bar": config.use_closed_bar,
        "htf_hull_filter_enabled": config.htf_hull_filter_enabled,
        "htf_hull_duration_seconds": config.htf_hull_duration_seconds,
        "atr_period": config.atr_period,
        "stop_atr_multiplier": config.stop_atr_multiplier,
        "tp1_r_multiple": config.tp1_r_multiple,
        "tp1_size_ratio": config.tp1_size_ratio,
        "tp2_r_multiple": config.tp2_r_multiple,
    }


def _parse_string(value: str) -> str:
    parsed = value.strip()
    if not parsed:
        raise ValueError("value must not be empty")
    return parsed


def _parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError("expected a boolean")


def _parse_positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise ValueError("expected a positive integer")
    return parsed
