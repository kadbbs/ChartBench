from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tq_app.application.live_runtime import PreflightResult, _closed_5m_bars_since_entry
from tq_app.domain import TradeDecision
from tq_app.live_trading import LiveTradingConfig, LiveTradingEngine, TradeExecutionResult


class LiveStartupRefreshTest(unittest.TestCase):
    def _engine(self, root: Path) -> LiveTradingEngine:
        return LiveTradingEngine(
            root,
            LiveTradingConfig(
                mode="live",
                enabled=True,
                dry_run=False,
                log_only=False,
                strategy="stc_1d_1h_reentry_24bar_refresh",
                risk_exits_enabled=True,
                risk_startup_check_bars_5m=24,
                email_enabled=False,
                state_path=root / "state.json",
                log_path=root / "live.log",
                order_log_path=root / "orders.jsonl",
            ),
        )

    @staticmethod
    def _position(anchor: int, *, risk_managed: bool = True) -> dict:
        return {
            "status": "open",
            "source": "live",
            "risk_managed": risk_managed,
            "strategy": "stc_extreme_contrarian_1d_1h_reentry_24bar_refresh",
            "refresh_startup_on_same_side_signal": True,
            "symbol": "BTCUSDT",
            "side": "buy",
            "clientOid": "entry-oid",
            "entry_price": "60000",
            "size": "0.001",
            "managed_size": "0.001",
            "bar_time": anchor - 300,
            "bar_time_label": "entry",
            "created_at": anchor * 1000,
            "risk_startup_anchor_bar_time": anchor,
            "risk_max_favorable_points": "250",
            "risk_max_adverse_points": "-180",
            "risk_protected_stop_points": "100",
            "risk_startup_checked": False,
            "exchange_stop_order_id": "stop-1",
        }

    @staticmethod
    def _decision(bar_time: int) -> TradeDecision:
        return TradeDecision(
            action="place_order",
            symbol="BTCUSDT",
            side="buy",
            bar_time=bar_time,
            bar_time_label="refresh",
            client_oid=f"signal-{bar_time}",
            reason="完整通过低周期、1D 与 1H 确认",
            htf_reentry_allowed=True,
            refresh_startup_on_same_side_signal=True,
        )

    def test_same_side_skip_refreshes_only_startup_anchor_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = self._engine(root)
            anchor = 1_800_000_000
            original = self._position(anchor)
            engine._write_state({"local_positions": [original], "client_oids": []})
            decision = self._decision(anchor + 22 * 300)

            with (
                patch.object(engine, "sync_local_positions_with_exchange"),
                patch.object(engine, "run_preflight", return_value=PreflightResult(ok=True)),
                patch.object(engine, "_trade_client", return_value=object()),
                patch.object(engine, "_entry_time_allowed", return_value=(True, "")),
                patch.object(engine, "_opposite_side_position", return_value=None),
                patch.object(engine, "_same_side_position", return_value={"side": "buy"}),
            ):
                result = engine.execute_decision(decision)
            refreshed = engine._read_state()["local_positions"][0]
            repeated = engine._refresh_startup_window_on_same_side_skip(decision)

        self.assertTrue(result.response["sameSidePosition"])
        self.assertEqual(result.response["startupWindowRefresh"]["elapsed_bars"], 23)
        self.assertIn("不加仓", result.response["message"])
        self.assertEqual(refreshed["risk_startup_anchor_bar_time"], decision.bar_time + 300)
        self.assertEqual(refreshed["risk_startup_refresh_count"], 1)
        self.assertEqual(len(refreshed["risk_startup_refreshes"]), 1)
        self.assertIsNone(repeated)

        # A refresh must not turn the skipped signal into a position change or reset risk history.
        self.assertEqual(refreshed["bar_time"], anchor - 300)
        self.assertEqual(refreshed["entry_price"], "60000")
        self.assertEqual(refreshed["size"], "0.001")
        self.assertEqual(refreshed["managed_size"], "0.001")
        self.assertEqual(refreshed["risk_max_favorable_points"], "250")
        self.assertEqual(refreshed["risk_max_adverse_points"], "-180")
        self.assertEqual(refreshed["risk_protected_stop_points"], "100")
        self.assertEqual(refreshed["exchange_stop_order_id"], "stop-1")

    def test_exact_window_boundary_and_manual_positions_do_not_refresh(self) -> None:
        anchor = 1_800_000_000
        cases = (
            (self._position(anchor), self._decision(anchor + 23 * 300)),
            (self._position(anchor, risk_managed=False), self._decision(anchor + 22 * 300)),
        )
        for position, decision in cases:
            with self.subTest(risk_managed=position["risk_managed"], bar_time=decision.bar_time):
                with tempfile.TemporaryDirectory() as directory:
                    engine = self._engine(Path(directory))
                    engine._write_state({"local_positions": [position]})

                    refresh = engine._refresh_startup_window_on_same_side_skip(decision)
                    persisted = engine._read_state()["local_positions"][0]

                self.assertIsNone(refresh)
                self.assertEqual(persisted["risk_startup_anchor_bar_time"], anchor)

    def test_completed_startup_check_does_not_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            engine = self._engine(Path(directory))
            anchor = 1_800_000_000
            position = self._position(anchor)
            position["risk_startup_checked"] = True
            engine._write_state({"local_positions": [position]})

            refresh = engine._refresh_startup_window_on_same_side_skip(
                self._decision(anchor + 10 * 300)
            )

        self.assertIsNone(refresh)

    def test_refresh_requires_1h_confirmation_and_position_opt_in(self) -> None:
        anchor = 1_800_000_000
        cases = []
        no_confirmation = self._decision(anchor + 10 * 300)
        no_confirmation.htf_reentry_allowed = False
        cases.append((self._position(anchor), no_confirmation))
        old_strategy_position = self._position(anchor)
        old_strategy_position["strategy"] = "stc_extreme_contrarian_1d_1h_reentry"
        old_strategy_position["refresh_startup_on_same_side_signal"] = False
        cases.append((old_strategy_position, self._decision(anchor + 10 * 300)))

        for position, decision in cases:
            with self.subTest(
                confirmed=decision.htf_reentry_allowed,
                position_strategy=position["strategy"],
            ):
                with tempfile.TemporaryDirectory() as directory:
                    engine = self._engine(Path(directory))
                    engine._write_state({"local_positions": [position]})

                    refresh = engine._refresh_startup_window_on_same_side_skip(decision)
                    persisted = engine._read_state()["local_positions"][0]

                self.assertIsNone(refresh)
                self.assertEqual(persisted["risk_startup_anchor_bar_time"], anchor)

    def test_new_live_position_records_strategy_ownership_and_execution_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            engine = self._engine(Path(directory))
            decision = self._decision(1_800_000_000)
            result = TradeExecutionResult(
                decision=decision,
                dry_run=False,
                enabled=True,
                request={"size": "0.001"},
                response={"order": {"orderId": "order-1"}, "entryPrice": "60000"},
            )

            engine._record_local_position_if_needed(result)
            position = engine._read_state()["local_positions"][0]

        self.assertEqual(
            position["strategy"],
            "stc_extreme_contrarian_1d_1h_reentry_24bar_refresh",
        )
        self.assertTrue(position["refresh_startup_on_same_side_signal"])
        self.assertEqual(position["bar_time"], decision.bar_time)
        self.assertEqual(position["risk_startup_anchor_bar_time"], decision.bar_time + 300)

    def test_live_risk_bar_count_prefers_refreshed_anchor(self) -> None:
        original_anchor = 1_800_000_000
        refreshed_anchor = original_anchor + 20 * 300
        position = {
            "bar_time": original_anchor,
            "risk_startup_anchor_bar_time": refreshed_anchor,
        }
        now_ms = (refreshed_anchor + 6 * 300 + 1) * 1000

        self.assertEqual(_closed_5m_bars_since_entry(position, now_ms), 6)

    def test_risk_check_before_signal_keeps_bar_23_eligible_and_bar_24_closed(self) -> None:
        anchor = 1_800_000_000
        position = {"risk_startup_anchor_bar_time": anchor}
        bar_23_signal_time = anchor + 22 * 300
        bar_24_signal_time = anchor + 23 * 300

        just_after_bar_23_close = (bar_23_signal_time + 300 + 1) * 1000
        just_after_bar_24_close = (bar_24_signal_time + 300 + 1) * 1000

        self.assertEqual(
            _closed_5m_bars_since_entry(position, just_after_bar_23_close),
            23,
        )
        self.assertEqual(
            _closed_5m_bars_since_entry(position, just_after_bar_24_close),
            24,
        )


if __name__ == "__main__":
    unittest.main()
