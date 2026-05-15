from __future__ import annotations

from tq_app.data_sources.bitget import load_bitget_contract_catalog


def format_contract_label(symbol: str, instrument_name: str | None = None) -> str:
    readable_name = (instrument_name or "").strip()
    if not readable_name:
        return symbol
    return f"{readable_name} · {symbol}"
