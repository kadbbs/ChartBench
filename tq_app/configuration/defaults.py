from __future__ import annotations

import os


DEFAULT_PROVIDER = "bitget"
DEFAULT_SYMBOL = "BTCUSDT"
DEFAULT_DURATION_SECONDS = 180
DEFAULT_DATA_LENGTH = 800
DEFAULT_REFRESH_MS = 200
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8050
DEFAULT_BAR_MODE = "time"
DEFAULT_RANGE_TICKS = 10
DEFAULT_BRICK_LENGTH = 10000


def env_default_str(name: str, fallback: str) -> str:
    return os.getenv(name, "").strip() or fallback


def env_default_int(name: str, fallback: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return fallback
    try:
        return int(raw)
    except ValueError:
        return fallback
