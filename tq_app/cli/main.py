from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Callable

from tq_app.config_profiles import (
    available_backtest_matrices,
    available_backtest_profiles,
    available_profiles,
)
from tq_app.data_sources import get_available_data_sources
from tq_app.domain import get_strategy_catalog


HELP = """ChartBench unified command line

Usage:
  chartbench.py chart run [options]
  chartbench.py live once|run|preflight [options]
  chartbench.py backtest run [options]
  chartbench.py backtest matrix [options]
  chartbench.py backtest baseline [options]
  chartbench.py backtest heatmap [options]
  chartbench.py list strategies|providers|profiles [--scope live|backtest|matrix]
  chartbench.py config show|validate --scope chart|live|backtest [--profile NAME] [--explain]

Common canonical options:
  --duration-seconds N   Alias of the legacy --duration option
  --data-length N        Alias of the legacy --length option

Existing entrypoint scripts remain supported.
"""


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help", "help"}:
        print(HELP)
        return
    if args[0] == "list":
        _run_list(args[1:])
        return
    if args[0] == "config":
        _run_config(args[1:])
        return
    route = tuple(args[:2])
    forwarded = _translate_common_options(args[2:])
    if route == ("chart", "run"):
        from web_tq_chart import main as command

        _invoke(command, forwarded)
        return
    if route in {("live", "once"), ("live", "run"), ("live", "preflight")}:
        from run_live_trading import main as command

        if route[1] == "run" and "--continuous" not in forwarded:
            forwarded.append("--continuous")
        if route[1] == "preflight" and "--preflight" not in forwarded:
            forwarded.append("--preflight")
        _invoke(command, forwarded)
        return
    if route == ("backtest", "run"):
        from run_backtest import main as command

        _invoke(command, forwarded)
        return
    if route == ("backtest", "matrix"):
        from run_backtest_matrix import main as command

        _invoke(command, forwarded)
        return
    if route in {("backtest", "baseline"), ("backtest", "heatmap")}:
        from run_signal_path_research import main as command

        action = "baseline" if route[1] == "baseline" else "matrix"
        _invoke(command, [action, *forwarded])
        return
    raise SystemExit(f"未知命令: {' '.join(args[:2])}\n\n{HELP}")


def _run_list(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="chartbench.py list")
    parser.add_argument("resource", choices=["strategies", "providers", "profiles"])
    parser.add_argument("--scope", choices=["live", "backtest", "matrix"], default="live")
    args = parser.parse_args(argv)
    root = project_root()
    if args.resource == "strategies":
        payload = {"strategies": get_strategy_catalog(root)}
    elif args.resource == "providers":
        payload = {"providers": get_available_data_sources()}
    elif args.scope == "backtest":
        payload = {"scope": args.scope, "profiles": available_backtest_profiles(root)}
    elif args.scope == "matrix":
        payload = {"scope": args.scope, "profiles": available_backtest_matrices(root)}
    else:
        payload = {"scope": args.scope, "profiles": available_profiles(root)}
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _run_config(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="chartbench.py config")
    parser.add_argument("action", choices=["show", "validate"])
    parser.add_argument("--scope", choices=["chart", "live", "backtest"], required=True)
    parser.add_argument("--profile", default="")
    parser.add_argument("--explain", action="store_true")
    args = parser.parse_args(argv)
    from tq_app.configuration import inspect_configuration, validate_configuration

    root = project_root()
    if args.action == "validate":
        payload = validate_configuration(root, args.scope, args.profile)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        if not payload["valid"]:
            raise SystemExit(2)
        return
    print(
        json.dumps(
            inspect_configuration(root, args.scope, args.profile, explain=args.explain),
            ensure_ascii=False,
            indent=2,
        )
    )


def _translate_common_options(argv: list[str]) -> list[str]:
    aliases = {
        "--duration-seconds": "--duration",
        "--data-length": "--length",
    }
    translated: list[str] = []
    for item in argv:
        key, separator, value = item.partition("=")
        canonical = aliases.get(key, key)
        translated.append(f"{canonical}={value}" if separator else canonical)
    return translated


def _invoke(command: Callable[[], None], argv: list[str]) -> None:
    previous = sys.argv
    try:
        sys.argv = [previous[0], *argv]
        command()
    finally:
        sys.argv = previous
