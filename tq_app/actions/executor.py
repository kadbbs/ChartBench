from __future__ import annotations

import json
import logging
import time
from typing import Any

from tq_app.signals.models import SignalEvent

LOGGER = logging.getLogger("tq_app.signals")


class ActionExecutor:
    def __init__(self, dry_run: bool = True) -> None:
        self.dry_run = dry_run

    def execute(self, event: SignalEvent, action_configs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for action_config in action_configs:
            action_type = str(action_config.get("type", "log")).strip().lower()
            started_at = time.time()
            try:
                result = self._execute_one(event, action_type, action_config)
            except Exception as exc:
                result = {
                    "type": action_type,
                    "ok": False,
                    "dry_run": self.dry_run,
                    "error": str(exc),
                }
            result["duration_ms"] = round((time.time() - started_at) * 1000, 3)
            results.append(result)
        return results

    def _execute_one(self, event: SignalEvent, action_type: str, action_config: dict[str, Any]) -> dict[str, Any]:
        if action_type == "noop":
            return {"type": action_type, "ok": True, "dry_run": self.dry_run}
        if action_type == "log":
            return self._log(event, action_config)
        if action_type in {"feishu", "open_position", "close_position", "trade"}:
            return {
                "type": action_type,
                "ok": False,
                "dry_run": True,
                "skipped": True,
                "reason": "action_not_implemented",
            }
        return {
            "type": action_type,
            "ok": False,
            "dry_run": self.dry_run,
            "skipped": True,
            "reason": "unknown_action_type",
        }

    def _log(self, event: SignalEvent, action_config: dict[str, Any]) -> dict[str, Any]:
        level_name = str(action_config.get("level", "info")).upper()
        level = getattr(logging, level_name, logging.INFO)
        LOGGER.log(level, "signal_event %s", json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True))
        return {"type": "log", "ok": True, "dry_run": self.dry_run, "level": level_name.lower()}
