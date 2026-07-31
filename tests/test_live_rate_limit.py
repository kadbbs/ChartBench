from __future__ import annotations

import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from tq_app.application.live_runtime import (
    BitgetFuturesTradeClient,
    LiveTradingConfig,
    LiveTradingEngine,
)


class BitgetRateLimitTest(unittest.TestCase):
    @staticmethod
    def _http_error() -> HTTPError:
        return HTTPError(
            "https://api.bitget.com/api/v2/mix/position/all-position",
            429,
            "Too Many Requests",
            {},
            io.BytesIO(b'{"code":"429","msg":"Too Many Requests"}'),
        )

    @staticmethod
    def _response(payload: dict) -> MagicMock:
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(payload).encode("utf-8")
        return response

    def _client(self, root: Path) -> BitgetFuturesTradeClient:
        credentials = {
            "BITGET_API_KEY": "test-key",
            "BITGET_API_SECRET": "test-secret",
            "BITGET_API_PASSPHRASE": "test-passphrase",
        }
        with patch.dict(os.environ, credentials, clear=False):
            return BitgetFuturesTradeClient(root)

    def test_read_request_retries_http_429_with_backoff(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = self._client(Path(directory))
            success = self._response({"code": "00000", "data": []})
            with (
                patch(
                    "tq_app.application.live_runtime.urlopen",
                    side_effect=[self._http_error(), success],
                ) as mocked_urlopen,
                patch("tq_app.application.live_runtime.time.sleep") as mocked_sleep,
            ):
                payload = client.get_all_positions(product_type="USDT-FUTURES")

        self.assertEqual(payload["code"], "00000")
        self.assertEqual(mocked_urlopen.call_count, 2)
        mocked_sleep.assert_called_once_with(1.0)

    def test_read_request_retries_payload_429(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = self._client(Path(directory))
            limited = self._response({"code": "429", "msg": "Too Many Requests", "data": None})
            success = self._response({"code": "00000", "data": []})
            with (
                patch(
                    "tq_app.application.live_runtime.urlopen",
                    side_effect=[limited, success],
                ) as mocked_urlopen,
                patch("tq_app.application.live_runtime.time.sleep") as mocked_sleep,
            ):
                payload = client.get_all_positions(product_type="USDT-FUTURES")

        self.assertEqual(payload["code"], "00000")
        self.assertEqual(mocked_urlopen.call_count, 2)
        mocked_sleep.assert_called_once_with(1.0)

    def test_write_request_does_not_retry_http_429(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = self._client(Path(directory))
            with (
                patch(
                    "tq_app.application.live_runtime.urlopen",
                    side_effect=self._http_error(),
                ) as mocked_urlopen,
                patch("tq_app.application.live_runtime.time.sleep") as mocked_sleep,
            ):
                with self.assertRaisesRegex(RuntimeError, "HTTP 429"):
                    client.place_order({"symbol": "BTCUSDT"})

        self.assertEqual(mocked_urlopen.call_count, 1)
        mocked_sleep.assert_not_called()


class RuntimePositionSnapshotTest(unittest.TestCase):
    @staticmethod
    def _engine(root: Path, *, mode: str = "live") -> LiveTradingEngine:
        real = mode == "live"
        return LiveTradingEngine(
            root,
            LiveTradingConfig(
                mode=mode,
                enabled=real,
                dry_run=not real,
                log_only=not real,
                risk_exits_enabled=True,
                email_enabled=False,
                state_path=root / "state.json",
                log_path=root / "live.log",
                order_log_path=root / "orders.jsonl",
            ),
        )

    def test_runtime_sync_reuses_one_exchange_position_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            engine = self._engine(Path(directory))
            client = object()
            snapshot = [{"symbol": "BTCUSDT", "side": "buy", "holdSide": "long", "size": "0.01"}]
            with (
                patch.object(engine, "_trade_client", return_value=client),
                patch.object(engine, "_exchange_open_positions", return_value=snapshot) as fetch_positions,
                patch.object(engine, "sync_local_positions_with_exchange") as sync_positions,
                patch.object(engine, "check_live_risk_exits", return_value=[]) as check_risk,
            ):
                result = engine.check_runtime_state(sync_positions=True)

        self.assertEqual(result, [])
        fetch_positions.assert_called_once_with(client)
        self.assertEqual(sync_positions.call_count, 1)
        self.assertEqual(sync_positions.call_args.kwargs["exchange_positions"], snapshot)
        self.assertIs(sync_positions.call_args.kwargs["client"], client)
        self.assertEqual(check_risk.call_count, 1)
        self.assertEqual(check_risk.call_args.kwargs["exchange_positions"], snapshot)
        self.assertIs(check_risk.call_args.kwargs["client"], client)

    def test_runtime_position_fetch_failure_is_nonfatal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            engine = self._engine(Path(directory))
            with (
                patch.object(engine, "_trade_client", return_value=object()),
                patch.object(engine, "_exchange_open_positions", side_effect=RuntimeError("HTTP 429")),
                patch.object(engine, "_warn_position_sync_failure") as warn,
                patch.object(engine, "sync_local_positions_with_exchange") as sync_positions,
                patch.object(engine, "check_live_risk_exits") as check_risk,
            ):
                result = engine.check_runtime_state(sync_positions=True)

        self.assertEqual(result, [])
        warn.assert_called_once()
        sync_positions.assert_not_called()
        check_risk.assert_not_called()

    def test_observation_mode_does_not_fetch_private_positions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            engine = self._engine(Path(directory), mode="email")
            with patch.object(engine, "_trade_client") as trade_client:
                result = engine.check_runtime_state(sync_positions=True)

        self.assertEqual(result, [])
        trade_client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
