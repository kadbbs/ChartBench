# Repository Guidelines

## Project Structure & Module Organization

This is a compact Python project for Bitget charting, live trading, and K-line backtesting.

- `web_tq_chart.py`, `run_live_trading.py`, and `run_backtest.py` are the main CLI entry points.
- `tq_app/` contains application code: `web.py` for Flask routes, `service.py` for market/indicator aggregation, `live_trading.py` for execution logic, and `notifications.py` for email alerts.
- `tq_app/data_sources/`, `tq_app/indicators/`, and `tq_app/backtesting/` hold provider adapters, indicator loading, and backtest engine/strategies.
- `templates/` and `static/` contain the chart UI.
- Local runtime outputs such as `logs/` and `backtest_outputs/` are ignored and should not be committed.

## Build, Test, and Development Commands

Create and activate a local environment:

```bash
python3 -m venv myvenv
source myvenv/bin/activate
pip install -r requirements.txt
```

Run the chart web app:

```bash
./myvenv/bin/python web_tq_chart.py
```

Run a backtest:

```bash
./myvenv/bin/python run_backtest.py --symbol BTCUSDT --duration 300 --length 1000 --strategy live_decision
```

Run live trading or observation mode:

```bash
./myvenv/bin/python run_live_trading.py --profile email --continuous
./myvenv/bin/python run_live_trading.py --profile live_5u --preflight
```

## Coding Style & Naming Conventions

Use Python 3 style with 4-space indentation, type hints where practical, and `from __future__ import annotations` in new Python modules to match the existing code. Prefer small functions, explicit config parsing, and dataclasses for structured state. Use `snake_case` for functions, variables, files, and modules; use `PascalCase` for classes. Keep frontend files in plain `static/app.js` and `static/styles.css` unless a build system is introduced.

## Testing Guidelines

There is no dedicated test suite yet. For changes to trading or backtesting behavior, validate with at least one deterministic `run_backtest.py` command and inspect `backtest_outputs/latest/report.json` and `trades.csv`. For web changes, run `web_tq_chart.py` locally and verify the chart loads with the default Bitget symbol. If adding tests, place them under `tests/` and name files `test_*.py`.

## Commit & Pull Request Guidelines

Recent commits use short imperative summaries, sometimes in Chinese, for example `Add kline backtest module` or `调整实盘为 Taker 市价开仓`. Keep the first line concise and focused on behavior. Pull requests should include a clear description, affected mode (`chart`, `live`, or `backtest`), commands run for verification, linked issues if any, and screenshots for UI changes.

## Security & Configuration Tips

Copy `.env.example` to `.env` for private credentials only. Runtime defaults live in `config/defaults.yaml`, and mode-specific choices live in `config/profiles/*.yaml`. Never commit `.env`, API keys, email tokens, or generated logs. Real trading must remain explicit: review `config/profiles/live_5u.yaml`, run `./myvenv/bin/python run_live_trading.py --profile live_5u --show-config`, and pass `--preflight` before `--continuous`.
