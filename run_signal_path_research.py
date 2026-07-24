from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path

from tq_app.backtesting import BacktestApplication
from tq_app.backtesting.experiments import build_path_experiment_spec
from tq_app.backtesting.path_research import (
    artifact_catalog,
    baseline_summary,
    build_signal_path_dataset,
    load_signal_path_dataset,
    run_path_matrix,
    run_percent_trailing_strategy,
    write_path_matrix_csv,
    write_path_strategy_trades,
    write_signal_path_artifacts,
)
from tq_app.runtime import runtime_project_root


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "生成无风控 1D Hull/STC + 5m 信号路径，重放止损止盈热力图矩阵，"
            "或回测开仓价硬止损 + 最佳价移动回撤策略。"
        )
    )
    parser.add_argument(
        "action",
        choices=["baseline", "matrix", "percent_trailing"],
    )
    parser.add_argument("--profile", default="btc_5m_signal_path")
    parser.add_argument("--name", default="")
    parser.add_argument("--provider", choices=["bitget", "binance"])
    parser.add_argument("--symbol")
    parser.add_argument("--start-time")
    parser.add_argument("--end-time")
    parser.add_argument("--context-bars", type=int, default=288)
    parser.add_argument(
        "--baseline-dataset",
        default="",
        help="matrix/percent_trailing 时复用已有 dataset/internal.json.gz，跳过行情与指标计算。",
    )
    parser.add_argument("--stop-unit", choices=["atr", "percent", "points"], default="atr")
    parser.add_argument("--stop-values", default="0.5:3:0.25")
    parser.add_argument("--take-values", default="0.75:6:0.25")
    parser.add_argument("--hard-stop-pct", type=float, default=3.0)
    parser.add_argument("--trailing-activation-pct", type=float, default=8.0)
    parser.add_argument("--trailing-drawdown-pct", type=float, default=10.0)
    parser.add_argument("--max-reentries", type=int, default=0)
    parser.add_argument("--reentry-cooldown-bars", type=int, default=0)
    parser.add_argument(
        "--intrabar-policy",
        choices=["stop_first", "take_first"],
        default="stop_first",
    )
    parser.add_argument(
        "--reveal-test",
        action="store_true",
        help="揭盲最后 20%% 测试段；选参数前不要使用。",
    )
    parser.add_argument("--output-dir", default="")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    project_root = runtime_project_root()
    overrides = {
        key: value
        for key, value in {
            "provider": args.provider,
            "symbol": args.symbol,
            "start_time": args.start_time,
            "end_time": args.end_time,
        }.items()
        if value not in (None, "")
    }
    payload = {
        "workflow": "signal_path",
        "action": args.action,
        "name": args.name,
        "profile": args.profile,
        "overrides": overrides,
        "context_bars": args.context_bars,
        "stop_unit": args.stop_unit,
        "stop_values": args.stop_values,
        "take_values": args.take_values,
        "hard_stop_pct": args.hard_stop_pct,
        "trailing_activation_pct": args.trailing_activation_pct,
        "trailing_drawdown_pct": args.trailing_drawdown_pct,
        "max_reentries": args.max_reentries,
        "reentry_cooldown_bars": args.reentry_cooldown_bars,
        "intrabar_policy": args.intrabar_policy,
        "reveal_test": args.reveal_test,
    }
    spec = build_path_experiment_spec(project_root, payload)
    if args.baseline_dataset:
        if spec.action == "baseline":
            raise SystemExit("--baseline-dataset 只能用于 matrix 或 percent_trailing。")
        dataset_path = Path(args.baseline_dataset)
        if not dataset_path.is_absolute():
            dataset_path = project_root / dataset_path
        dataset = load_signal_path_dataset(dataset_path)
    else:
        application = BacktestApplication(project_root)
        resolved = application.resolve(spec.base_request)
        prepared = application.prepare_market(resolved)
        study = application.prepare_study(resolved, prepared)
        dataset = build_signal_path_dataset(
            resolved,
            prepared,
            study,
            context_bars=spec.context_bars,
        )
        del study, prepared
    output_dir = (
        Path(args.output_dir)
        if args.output_dir
        else Path("backtest_outputs")
        / "signal_path"
        / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{args.action}"
    )
    if not output_dir.is_absolute():
        output_dir = project_root / output_dir
    if not args.baseline_dataset:
        write_signal_path_artifacts(dataset, output_dir)
    if spec.action == "baseline":
        summary = baseline_summary(dataset)
    elif spec.action == "matrix":
        summary = run_path_matrix(
            dataset,
            stop_unit=spec.stop_unit,
            stop_values=spec.stop_values,
            take_values=spec.take_values,
            max_reentries=spec.max_reentries,
            reentry_cooldown_bars=spec.reentry_cooldown_bars,
            intrabar_policy=spec.intrabar_policy,
            reveal_test=spec.reveal_test,
        )
        write_path_matrix_csv(output_dir / "matrix.csv", summary["rows"])
    else:
        summary = run_percent_trailing_strategy(
            dataset,
            hard_stop_pct=spec.hard_stop_pct,
            trailing_activation_pct=spec.trailing_activation_pct,
            trailing_drawdown_pct=spec.trailing_drawdown_pct,
            max_reentries=spec.max_reentries,
            reentry_cooldown_bars=spec.reentry_cooldown_bars,
            reveal_test=spec.reveal_test,
        )
        trade_records = list(summary.pop("_trade_records", []))
        write_path_matrix_csv(output_dir / "strategy.csv", summary["rows"])
        write_path_strategy_trades(
            output_dir / "strategy_trades.csv",
            trade_records,
        )
    summary.update(
        name=spec.name,
        profile=spec.profile,
        artifacts=artifact_catalog(output_dir),
    )
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "type": summary["type"],
                "dataset_id": dataset.manifest["dataset_id"],
                "output_dir": str(output_dir),
                "best": summary.get("best"),
                "artifacts": summary["artifacts"],
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
