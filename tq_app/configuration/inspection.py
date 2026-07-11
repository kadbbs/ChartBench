from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from tq_app.config_profiles import read_flat_config
from tq_app.data_sources import get_available_data_sources
from tq_app.domain import get_strategy_catalog


SENSITIVE_PARTS = ("KEY", "SECRET", "PASSWORD", "PASSPHRASE", "TOKEN")


def inspect_configuration(
    project_root: Path,
    scope: str,
    profile: str = "",
    *,
    explain: bool = False,
) -> dict[str, Any]:
    values, sources = _resolve(project_root, scope, profile)
    visible = {
        key: _redact(key, value)
        for key, value in sorted(values.items())
        if _belongs_to_scope(key, scope)
    }
    payload: dict[str, Any] = {"scope": scope, "profile": profile or None, "config": visible}
    if explain:
        payload["config"] = {
            key: {"value": value, "source": sources[key]}
            for key, value in visible.items()
        }
    return payload


def validate_configuration(project_root: Path, scope: str, profile: str = "") -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        values, _sources = _resolve(project_root, scope, profile)
    except Exception as exc:
        return {"scope": scope, "profile": profile or None, "valid": False, "errors": [str(exc)], "warnings": []}

    if scope == "chart":
        provider = values.get("TQ_CHART_DEFAULT_PROVIDER") or values.get("TQ_DEFAULT_PROVIDER") or "tianqin"
        allowed = set(get_available_data_sources())
    elif scope == "live":
        provider = values.get("LIVE_TRADING_PROVIDER") or values.get("TQ_DEFAULT_PROVIDER") or "bitget"
        allowed = {"binance", "bitget"}
    else:
        provider = values.get("provider") or values.get("TQ_DEFAULT_PROVIDER") or "bitget"
        allowed = {"binance", "bitget"}
    if str(provider).lower() not in allowed:
        errors.append(f"provider 无效: {provider}，允许值: {', '.join(sorted(allowed))}")

    strategy_name = ""
    if scope == "live":
        strategy_name = str(values.get("LIVE_TRADING_STRATEGY") or "stc_extreme_contrarian")
    elif scope == "backtest":
        strategy_name = str(values.get("signal_strategy") or values.get("strategy") or "live_decision")
    if strategy_name:
        known = {
            name
            for item in get_strategy_catalog(project_root)
            for name in [str(item["name"]), *(str(alias) for alias in item["aliases"])]
        }
        if strategy_name.lower() not in known:
            errors.append(f"strategy 无效: {strategy_name}，可用策略: {', '.join(sorted(known))}")

    numeric_keys = (
        ("TQ_DEFAULT_DURATION_SECONDS", "TQ_DEFAULT_DATA_LENGTH")
        if scope in {"chart", "live"}
        else ("duration", "length", "warmup_bars")
    )
    for key in numeric_keys:
        raw = values.get(key)
        if raw in (None, ""):
            continue
        try:
            if int(str(raw)) <= 0:
                raise ValueError
        except ValueError:
            errors.append(f"{key} 必须是正整数，当前值: {raw}")

    if scope == "live" and not profile:
        warnings.append("未指定 live profile；真实运行前建议显式选择 email、dry_run_5u 或 live_5u。")
    return {"scope": scope, "profile": profile or None, "valid": not errors, "errors": errors, "warnings": warnings}


def _resolve(project_root: Path, scope: str, profile: str) -> tuple[dict[str, str], dict[str, str]]:
    values: dict[str, str] = {}
    sources: dict[str, str] = {}

    def apply(layer: dict[str, Any], source: str) -> None:
        for key, value in layer.items():
            if value is None:
                continue
            values[str(key)] = str(value)
            sources[str(key)] = source

    defaults = project_root / "config" / "defaults.yaml"
    if defaults.exists():
        apply(read_flat_config(defaults), "config/defaults.yaml")
    env_path = project_root / ".env"
    if env_path.exists():
        apply(dotenv_values(env_path), ".env")
    profile_name = profile.strip()
    if profile_name:
        folder = "profiles" if scope == "live" else "backtests" if scope == "backtest" else ""
        if not folder:
            raise ValueError("chart scope 不使用 profile")
        profile_path = project_root / "config" / folder / f"{profile_name}.yaml"
        if not profile_path.exists():
            raise FileNotFoundError(f"配置 profile 不存在: {profile_path}")
        apply(read_flat_config(profile_path), f"config/{folder}/{profile_name}.yaml")
    apply(os.environ, "process environment")
    return values, sources


def _belongs_to_scope(key: str, scope: str) -> bool:
    if scope == "backtest":
        return key.islower() or key.startswith(("TQ_DEFAULT_", "LIVE_TRADING_", "BITGET_", "BINANCE_"))
    if scope == "chart":
        return key.startswith(("TQ_CHART_", "TQ_DEFAULT_", "TIANQIN_", "TQSDK_"))
    return key.startswith(("LIVE_TRADING_", "TQ_DEFAULT_", "BITGET_", "BINANCE_", "RESEND_"))


def _redact(key: str, value: str) -> str:
    return "***" if value and any(part in key.upper() for part in SENSITIVE_PARTS) else value
