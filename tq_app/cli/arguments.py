from __future__ import annotations

import argparse
from collections.abc import Sequence


def add_market_arguments(
    parser: argparse.ArgumentParser,
    *,
    provider_default: str,
    provider_choices: Sequence[str],
    symbol_default: str,
    duration_default: int,
    data_length_default: int,
) -> None:
    parser.add_argument("--provider", default=provider_default, choices=list(provider_choices), help="行情数据源。")
    parser.add_argument("--symbol", default=symbol_default, help="合约代码。")
    parser.add_argument(
        "--duration-seconds",
        "--duration",
        dest="duration",
        type=int,
        default=duration_default,
        help="K 线周期秒数；--duration 为兼容别名。",
    )
    parser.add_argument(
        "--data-length",
        "--length",
        dest="length",
        type=int,
        default=data_length_default,
        help="行情 K 线数量；--length 为兼容别名。",
    )
