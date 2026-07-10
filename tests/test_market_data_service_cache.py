from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch

import pandas as pd

from tq_app.models import IndicatorResult, SeriesDefinition
from tq_app.service import BINANCE_PROVIDER, MarketDataService


class FakeDataSource:
    def __init__(self) -> None:
        self.version = 1
        self.bars = pd.DataFrame(
            {
                "datetime": pd.date_range("2026-01-01", periods=3, freq="5min", tz="UTC"),
                "open": [100.0, 101.0, 102.0],
                "high": [102.0, 103.0, 104.0],
                "low": [99.0, 100.0, 101.0],
                "close": [101.0, 102.0, 103.0],
                "volume": [10.0, 11.0, 12.0],
            }
        )

    def get_bars_with_status(self) -> tuple[pd.DataFrame, dict[str, object]]:
        return self.bars, {"version": self.version, "connected": True}


class CountingIndicator:
    def __init__(self) -> None:
        self.build_count = 0
        self._lock = threading.Lock()

    @staticmethod
    def resolve_params(raw_params: dict[str, object] | None = None) -> dict[str, object]:
        return dict(raw_params or {})

    def build(self, bars: pd.DataFrame, params: dict[str, object] | None = None) -> IndicatorResult:
        with self._lock:
            self.build_count += 1
        time.sleep(0.03)
        return IndicatorResult(
            id="counting",
            name="Counting",
            pane="price",
            series=[
                SeriesDefinition(
                    id="value",
                    name="Value",
                    pane="price",
                    series_type="line",
                    data=[
                        {"time": int(row.time), "value": float(row.close)}
                        for row in bars.itertuples()
                    ],
                )
            ],
        )


class FakeRegistry:
    def __init__(self, indicator: CountingIndicator) -> None:
        self.indicator = indicator

    @staticmethod
    def default_ids() -> list[str]:
        return ["counting"]

    def get(self, indicator_id: str) -> CountingIndicator:
        if indicator_id != "counting":
            raise KeyError(indicator_id)
        return self.indicator


class MarketDataServiceCacheTest(unittest.TestCase):
    def _service(self) -> tuple[MarketDataService, FakeDataSource, CountingIndicator]:
        service = MarketDataService(
            provider=BINANCE_PROVIDER,
            symbol="BTCUSDT",
            duration_seconds=300,
            data_length=3,
            refresh_ms=1000,
            project_root=Path(__file__).resolve().parents[1],
        )
        source = FakeDataSource()
        indicator = CountingIndicator()
        service.indicators = FakeRegistry(indicator)
        service._get_data_source = lambda *args, **kwargs: source
        service._symbol_label = lambda provider, symbol: symbol
        service._contract_detail = lambda provider, symbol: {}
        service._provider_account = lambda provider: {}
        return service, source, indicator

    def test_snapshot_singleflight_builds_once_for_concurrent_requests(self) -> None:
        service, _source, indicator = self._service()

        with ThreadPoolExecutor(max_workers=8) as executor:
            snapshots = list(executor.map(lambda _: service.get_snapshot(), range(8)))

        self.assertEqual(indicator.build_count, 1)
        self.assertEqual(len(service._snapshot_cache), 1)
        self.assertTrue(all(item["last_close"] == 103.0 for item in snapshots))
        self.assertEqual(len({id(item) for item in snapshots}), len(snapshots))

    def test_latest_version_replaces_cached_snapshot_without_result_poisoning(self) -> None:
        service, source, indicator = self._service()
        first = service.get_snapshot()
        first["higher_timeframe"] = {"temporary": True}

        cached = service.get_snapshot()
        self.assertNotIn("higher_timeframe", cached)

        source.version = 2
        source.bars.loc[source.bars.index[-1], "close"] = 109.0
        latest = service.get_snapshot()

        self.assertEqual(latest["last_close"], 109.0)
        self.assertEqual(indicator.build_count, 2)
        self.assertEqual(len(service._snapshot_cache), 1)
        self.assertEqual(next(iter(service._snapshot_cache.values())).version, 2)

    def test_provider_account_is_cached_and_returned_as_a_copy(self) -> None:
        service, _source, _indicator = self._service()
        with patch("tq_app.service.load_binance_account_summary", return_value={"equity": 12.5}) as loader:
            first = MarketDataService._provider_account(service, BINANCE_PROVIDER)
            first["equity"] = 0
            second = MarketDataService._provider_account(service, BINANCE_PROVIDER)

        self.assertEqual(second["equity"], 12.5)
        loader.assert_called_once_with(service.project_root)

    def test_concurrent_provider_account_requests_share_one_refresh(self) -> None:
        service, _source, _indicator = self._service()

        def load_account(_root: Path) -> dict[str, float]:
            time.sleep(0.03)
            return {"equity": 12.5}

        with patch("tq_app.service.load_binance_account_summary", side_effect=load_account) as loader:
            with ThreadPoolExecutor(max_workers=6) as executor:
                accounts = list(
                    executor.map(
                        lambda _: MarketDataService._provider_account(service, BINANCE_PROVIDER),
                        range(6),
                    )
                )

        self.assertTrue(all(item["equity"] == 12.5 for item in accounts))
        self.assertEqual(loader.call_count, 1)

    def test_contract_catalog_and_symbol_map_are_reused(self) -> None:
        service, _source, _indicator = self._service()
        contracts = [
            {
                "symbol": "BTCUSDT",
                "name": "BTCUSDT",
                "label": "BTC / USDT",
                "exchange_id": "BINANCE",
                "product_id": "USDT-FUTURES",
            }
        ]
        with patch("tq_app.service.load_binance_contract_catalog", return_value=contracts) as loader:
            self.assertEqual(MarketDataService._symbol_label(service, BINANCE_PROVIDER, "BTCUSDT"), "BTC / USDT")
            detail = MarketDataService._contract_detail(service, BINANCE_PROVIDER, "BTCUSDT")

        self.assertEqual(detail["exchange_id"], "BINANCE")
        loader.assert_called_once_with(service.project_root)

    def test_concurrent_contract_requests_share_one_catalog_load(self) -> None:
        service, _source, _indicator = self._service()
        contracts = [
            {
                "symbol": "BTCUSDT",
                "name": "BTCUSDT",
                "label": "BTC / USDT",
                "exchange_id": "BINANCE",
                "product_id": "USDT-FUTURES",
            }
        ]

        def load_contracts(_root: Path) -> list[dict[str, str]]:
            time.sleep(0.03)
            return contracts

        with patch("tq_app.service.load_binance_contract_catalog", side_effect=load_contracts) as loader:
            with ThreadPoolExecutor(max_workers=6) as executor:
                labels = list(
                    executor.map(
                        lambda _: MarketDataService._symbol_label(service, BINANCE_PROVIDER, "BTCUSDT"),
                        range(6),
                    )
                )

        self.assertEqual(labels, ["BTC / USDT"] * 6)
        self.assertEqual(loader.call_count, 1)


if __name__ == "__main__":
    unittest.main()
