from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import dotenv_values


CONFIG_DIR = "config"
DEFAULTS_FILE = "defaults.yaml"
PROFILES_DIR = "profiles"
BACKTESTS_DIR = "backtests"
BACKTEST_MATRICES_DIR = "backtest_matrices"
SENSITIVE_KEY_PARTS = ("API_KEY", "API_SECRET", "PASSPHRASE", "TOKEN", "PASSWORD")


def load_layered_env(project_root: Path, profile: str | None = None, *, profile_overrides_env: bool = False) -> dict[str, str]:
    """Apply layered runtime configuration to os.environ.

    By default the order is defaults < profile < .env < process env.
    For an explicit live-trading --profile selection, callers can set
    profile_overrides_env=True so stale mode/risk values in .env do not block
    the selected runtime profile. Process environment variables still win.

    This deliberately avoids a YAML dependency. The supported YAML subset is a
    flat mapping of KEY: value pairs, with comments and blank lines.
    """
    original_env = dict(os.environ)
    layers: list[dict[str, str]] = []
    defaults_path = project_root / CONFIG_DIR / DEFAULTS_FILE
    if defaults_path.exists():
        layers.append(_read_flat_yaml(defaults_path))

    profile_name = (profile or "").strip()
    profile_values: dict[str, str] = {}
    if profile_name:
        profile_path = _profile_path(project_root, profile_name)
        if not profile_path.exists():
            raise FileNotFoundError(f"配置 profile 不存在: {profile_path}")
        profile_values = _read_flat_yaml(profile_path)
        if not profile_overrides_env:
            layers.append(profile_values)

    env_path = project_root / ".env"
    if env_path.exists():
        layers.append({key: str(value) for key, value in dotenv_values(env_path).items() if value is not None})
    if profile_values and profile_overrides_env:
        layers.append(profile_values)

    merged: dict[str, str] = {}
    for layer in layers:
        merged.update({key: str(value) for key, value in layer.items()})

    for key, value in merged.items():
        if key in original_env:
            continue
        os.environ[key] = value

    return {
        "defaults": str(defaults_path) if defaults_path.exists() else "",
        "profile": profile_name,
        "profile_path": str(_profile_path(project_root, profile_name)) if profile_name else "",
        "env": str(env_path) if env_path.exists() else "",
    }


def available_profiles(project_root: Path) -> list[str]:
    profiles_root = project_root / CONFIG_DIR / PROFILES_DIR
    if not profiles_root.exists():
        return []
    return sorted(path.stem for path in profiles_root.glob("*.yaml") if path.is_file())


def available_backtest_profiles(project_root: Path) -> list[str]:
    profiles_root = project_root / CONFIG_DIR / BACKTESTS_DIR
    if not profiles_root.exists():
        return []
    return sorted(path.stem for path in profiles_root.glob("*.yaml") if path.is_file())


def available_backtest_matrices(project_root: Path) -> list[str]:
    profiles_root = project_root / CONFIG_DIR / BACKTEST_MATRICES_DIR
    if not profiles_root.exists():
        return []
    return sorted(path.stem for path in profiles_root.glob("*.yaml") if path.is_file())


def read_flat_config(path: Path) -> dict[str, str]:
    return _read_flat_yaml(path)


def load_backtest_profile(project_root: Path, profile: str | None) -> dict[str, str]:
    profile_name = (profile or "").strip()
    if not profile_name:
        return {}
    profile_path = _named_config_path(project_root, BACKTESTS_DIR, profile_name)
    if not profile_path.exists():
        raise FileNotFoundError(f"回测 profile 不存在: {profile_path}")
    return _read_flat_yaml(profile_path)


def effective_config_snapshot(keys: list[str] | None = None) -> dict[str, Any]:
    selected_keys = keys or sorted(
        key
        for key in os.environ
        if key.startswith(("LIVE_TRADING_", "TQ_DEFAULT_", "TQ_CHART_", "TIANQIN_", "TQSDK_", "BINANCE_", "BITGET_"))
    )
    snapshot: dict[str, Any] = {}
    for key in selected_keys:
        if key not in os.environ:
            continue
        value = os.environ.get(key, "")
        snapshot[key] = "***" if _is_sensitive_key(key) and value else value
    return snapshot


def _profile_path(project_root: Path, profile_name: str) -> Path:
    return _named_config_path(project_root, PROFILES_DIR, profile_name)


def _named_config_path(project_root: Path, folder_name: str, profile_name: str) -> Path:
    safe_name = profile_name.strip()
    if not safe_name or "/" in safe_name or "\\" in safe_name or safe_name in {".", ".."}:
        raise ValueError(f"无效 profile 名称: {profile_name!r}")
    return project_root / CONFIG_DIR / folder_name / f"{safe_name}.yaml"


def _read_flat_yaml(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise ValueError(f"{path}:{line_number} 只支持 KEY: value 格式")
        key, raw_value = line.split(":", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"{path}:{line_number} 配置键不能为空")
        if key.startswith("-"):
            raise ValueError(f"{path}:{line_number} 当前不支持 YAML 列表")
        values[key] = _normalize_scalar(raw_value.strip())
    return values


def _normalize_scalar(value: str) -> str:
    if not value:
        return ""
    quote = value[0]
    if quote in {"'", '"'} and value.endswith(quote):
        return value[1:-1]
    if " #" in value:
        value = value.split(" #", 1)[0].rstrip()
    return value


def _is_sensitive_key(key: str) -> bool:
    upper_key = key.upper()
    return any(part in upper_key for part in SENSITIVE_KEY_PARTS)
