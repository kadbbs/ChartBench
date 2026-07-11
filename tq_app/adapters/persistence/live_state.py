from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Any, Iterator


class StateSnapshot(dict[str, Any]):
    def __init__(self, values: dict[str, Any], revision: str) -> None:
        super().__init__(values)
        self.revision = revision


class LiveStateStore:
    """Crash-safe and process-safe JSON state repository."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._io_lock = threading.RLock()

    def read(self) -> StateSnapshot:
        with self._io_lock:
            with self._file_lock(exclusive=False):
                return self._load_locked()

    def write(self, state: dict[str, Any]) -> None:
        if not isinstance(state, dict):
            raise TypeError("实盘 state 必须是 JSON object。")
        payload = (json.dumps(state, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        expected_revision = getattr(state, "revision", None)
        with self._io_lock:
            with self._file_lock(exclusive=True):
                current_state = self._load_locked()
                if expected_revision is not None and current_state.revision != expected_revision:
                    raise RuntimeError(
                        "实盘 state 在本次读写之间已被其他进程修改；为避免覆盖较新的 clientOid、仓位或风控状态，已停止本次写入。"
                    )
                self._atomic_write_locked(payload)
                if isinstance(state, StateSnapshot):
                    state.revision = self.revision(payload)

    @contextmanager
    def _file_lock(self, *, exclusive: bool) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_name(f"{self.path.name}.lock")
        with lock_path.open("a+b") as lock_file:
            operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
            fcntl.flock(lock_file.fileno(), operation)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _load_locked(self) -> StateSnapshot:
        if not self.path.exists():
            return StateSnapshot({}, self.revision(None))
        try:
            payload = self.path.read_bytes()
            parsed = json.loads(payload.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"实盘 state 文件损坏或不可读，已停止交易以避免重复下单: {self.path}"
            ) from exc
        if not isinstance(parsed, dict):
            raise RuntimeError(f"实盘 state 顶层必须是 JSON object，已停止交易: {self.path}")
        return StateSnapshot(parsed, self.revision(payload))

    def _atomic_write_locked(self, payload: bytes) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        file_descriptor, temp_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=self.path.parent,
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(file_descriptor, "wb") as temp_file:
                temp_file.write(payload)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.replace(temp_path, self.path)
            directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            directory_fd = os.open(self.path.parent, directory_flags)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    @staticmethod
    def revision(payload: bytes | None) -> str:
        marker = payload if payload is not None else b"<missing-state>"
        return hashlib.sha256(marker).hexdigest()
