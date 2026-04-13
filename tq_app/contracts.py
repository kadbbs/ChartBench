from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import duckdb
from tq_app.data_sources.binance import load_binance_contract_catalog
from tq_app.data_sources.bitget import load_bitget_contract_catalog


def format_contract_label(symbol: str, instrument_name: str | None = None) -> str:
    readable_name = (instrument_name or "").strip()
    if not readable_name:
        return symbol
    return f"{readable_name} · {symbol}"


def load_duckdb_contract_catalog(project_root: Path) -> list[dict[str, Any]]:
    db_path_raw = os.getenv("DUCKDB_TICK_DB_PATH", "").strip()
    db_path = Path(db_path_raw).expanduser() if db_path_raw else project_root / "data" / "duckdb" / "ticks.duckdb"
    if not db_path.exists():
        return []

    storage_provider = os.getenv("DUCKDB_SOURCE_PROVIDER", "bitget").strip() or "bitget"
    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = conn.execute(
            """
            WITH tick_stats AS (
                SELECT
                    provider,
                    symbol,
                    min(ts) AS first_tick_at,
                    max(ts) AS last_tick_at,
                    count(*) AS tick_count
                FROM market_ticks
                GROUP BY provider, symbol
            ),
            bar_1m_stats AS (
                SELECT provider, symbol, min(bar_start) AS first_bar_1m_at, max(bar_start) AS last_bar_1m_at, count(*) AS bar_1m_count
                FROM market_bars_1m
                GROUP BY provider, symbol
            ),
            bar_5m_stats AS (
                SELECT provider, symbol, min(bar_start) AS first_bar_5m_at, max(bar_start) AS last_bar_5m_at, count(*) AS bar_5m_count
                FROM market_bars_5m
                GROUP BY provider, symbol
            ),
            bar_10m_stats AS (
                SELECT provider, symbol, min(bar_start) AS first_bar_10m_at, max(bar_start) AS last_bar_10m_at, count(*) AS bar_10m_count
                FROM market_bars_10m
                GROUP BY provider, symbol
            ),
            bar_15m_stats AS (
                SELECT provider, symbol, min(bar_start) AS first_bar_15m_at, max(bar_start) AS last_bar_15m_at, count(*) AS bar_15m_count
                FROM market_bars_15m
                GROUP BY provider, symbol
            )
            SELECT
                meta.symbol,
                meta.instrument_name AS name,
                meta.label,
                meta.exchange_id,
                meta.product_id,
                meta.price_tick,
                meta.volume_multiple,
                meta.delivery_year,
                meta.delivery_month,
                meta.contract_month,
                tick_stats.last_tick_at,
                tick_stats.first_tick_at,
                coalesce(tick_stats.tick_count, 0) AS tick_count,
                coalesce(bar_1m_stats.bar_1m_count, 0) AS bar_1m_count,
                coalesce(bar_5m_stats.bar_5m_count, 0) AS bar_5m_count,
                coalesce(bar_10m_stats.bar_10m_count, 0) AS bar_10m_count,
                coalesce(bar_15m_stats.bar_15m_count, 0) AS bar_15m_count,
                least(
                    coalesce(tick_stats.first_tick_at, TIMESTAMP '9999-12-31 00:00:00'),
                    coalesce(bar_1m_stats.first_bar_1m_at, TIMESTAMP '9999-12-31 00:00:00'),
                    coalesce(bar_5m_stats.first_bar_5m_at, TIMESTAMP '9999-12-31 00:00:00'),
                    coalesce(bar_10m_stats.first_bar_10m_at, TIMESTAMP '9999-12-31 00:00:00'),
                    coalesce(bar_15m_stats.first_bar_15m_at, TIMESTAMP '9999-12-31 00:00:00')
                ) AS first_data_at,
                greatest(
                    coalesce(tick_stats.last_tick_at, TIMESTAMP '0001-01-01 00:00:00'),
                    coalesce(bar_1m_stats.last_bar_1m_at, TIMESTAMP '0001-01-01 00:00:00'),
                    coalesce(bar_5m_stats.last_bar_5m_at, TIMESTAMP '0001-01-01 00:00:00'),
                    coalesce(bar_10m_stats.last_bar_10m_at, TIMESTAMP '0001-01-01 00:00:00'),
                    coalesce(bar_15m_stats.last_bar_15m_at, TIMESTAMP '0001-01-01 00:00:00')
                ) AS last_data_at
            FROM contract_metadata AS meta
            LEFT JOIN tick_stats
                ON tick_stats.provider = meta.provider
               AND tick_stats.symbol = meta.symbol
            LEFT JOIN bar_1m_stats
                ON bar_1m_stats.provider = meta.provider
               AND bar_1m_stats.symbol = meta.symbol
            LEFT JOIN bar_5m_stats
                ON bar_5m_stats.provider = meta.provider
               AND bar_5m_stats.symbol = meta.symbol
            LEFT JOIN bar_10m_stats
                ON bar_10m_stats.provider = meta.provider
               AND bar_10m_stats.symbol = meta.symbol
            LEFT JOIN bar_15m_stats
                ON bar_15m_stats.provider = meta.provider
               AND bar_15m_stats.symbol = meta.symbol
            WHERE meta.provider = ?
            ORDER BY meta.exchange_id, meta.product_id, meta.delivery_year, meta.delivery_month, meta.symbol
            """,
            [storage_provider],
        ).fetchall()
        return [
            {
                "symbol": str(row[0]),
                "name": str(row[1]),
                "label": str(row[2]),
                "exchange_id": str(row[3]),
                "product_id": str(row[4]),
                "price_tick": float(row[5]) if row[5] is not None else None,
                "volume_multiple": float(row[6]) if row[6] is not None else None,
                "delivery_year": int(row[7]) if row[7] is not None else None,
                "delivery_month": int(row[8]) if row[8] is not None else None,
                "contract_month": str(row[9]) if row[9] is not None else "",
                "last_tick_at": str(row[10]) if row[10] is not None else "",
                "first_tick_at": str(row[11]) if row[11] is not None else "",
                "tick_count": int(row[12] or 0),
                "bar_1m_count": int(row[13] or 0),
                "bar_5m_count": int(row[14] or 0),
                "bar_10m_count": int(row[15] or 0),
                "bar_15m_count": int(row[16] or 0),
                "first_data_at": "" if row[17] is None or str(row[17]).startswith("9999-12-31") else str(row[17]),
                "last_data_at": "" if row[18] is None or str(row[18]).startswith("0001-01-01") else str(row[18]),
            }
            for row in rows
        ]
    finally:
        conn.close()
