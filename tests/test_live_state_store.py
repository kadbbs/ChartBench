from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tq_app.live_trading import LiveTradingConfig, LiveTradingEngine


class LiveStateStoreTest(unittest.TestCase):
    def _engine(self, root: Path, name: str) -> LiveTradingEngine:
        config = LiveTradingConfig(
            state_path=root / "live_state.json",
            log_path=root / f"{name}.log",
            order_log_path=root / f"{name}.jsonl",
            email_enabled=False,
        )
        return LiveTradingEngine(root, config)

    def test_state_write_is_atomic_and_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = self._engine(root, "atomic")
            engine._write_state({"client_oids": ["oid-1"], "local_positions": []})

            state = engine._read_state()

            self.assertEqual(state["client_oids"], ["oid-1"])
            self.assertEqual(json.loads((root / "live_state.json").read_text()), dict(state))
            self.assertEqual(list(root.glob(".live_state.json.*.tmp")), [])

    def test_corrupt_state_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "live_state.json").write_text("{not-json", encoding="utf-8")
            engine = self._engine(root, "corrupt")

            with self.assertRaisesRegex(RuntimeError, "已停止交易"):
                engine._already_executed("new-order")

    def test_stale_snapshot_cannot_overwrite_newer_process_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = self._engine(root, "first")
            second = self._engine(root, "second")
            first._write_state({"client_oids": ["initial"]})
            stale_state = first._read_state()
            newer_state = second._read_state()
            newer_state["client_oids"].append("second")
            second._write_state(newer_state)
            stale_state["client_oids"].append("first")

            with self.assertRaisesRegex(RuntimeError, "其他进程修改"):
                first._write_state(stale_state)

            final_state = second._read_state()
            self.assertEqual(final_state["client_oids"], ["initial", "second"])

    def test_non_object_state_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "live_state.json").write_text("[]", encoding="utf-8")
            engine = self._engine(root, "shape")

            with self.assertRaisesRegex(RuntimeError, "顶层必须"):
                engine._read_state()


if __name__ == "__main__":
    unittest.main()
