from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import pandas as pd


class DataSource(ABC):
    provider_name: str

    @abstractmethod
    def start(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def stop(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_bars(self) -> pd.DataFrame:
        raise NotImplementedError

    def get_bars_with_status(self) -> tuple[pd.DataFrame, dict[str, Any]]:
        return self.get_bars(), self.status()

    def configure(self, **kwargs) -> None:
        return None

    def wait_for_update(self, last_version: int | None, timeout: float) -> int:
        return 0

    def status(self) -> dict[str, Any]:
        return {}
