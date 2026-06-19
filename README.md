# Bitget Chart / Live / Backtest

这是精简后的 Bitget 项目分支，只保留三条核心链路：

- 图表展示：Bitget K 线、指标、前端图表
- 实盘模块：信号评估、邮件提醒、Bitget 下单和反向仓位平仓
- 回测模块：K 线级策略回测、报告和开平仓点输出

## 保留结构

```text
.
├── web_tq_chart.py                 # 图表 Web 服务入口
├── run_live_trading.py             # 实盘/观察模式入口
├── run_backtest.py                 # K 线级回测入口
├── custom_indicators.py            # 自定义指标：merged_dkx_hull_ut 等
├── requirements.txt
├── static/                         # 前端资源
├── templates/                      # Flask 模板
└── tq_app/
    ├── web.py                      # Web API
    ├── service.py                  # 行情和指标聚合
    ├── live_trading.py             # 实盘执行核心
    ├── notifications.py            # 邮件发送
    ├── contracts.py
    ├── models.py
    ├── LIVE_TRADING_README.md
    ├── backtesting/                # 回测引擎和策略注册
    ├── data_sources/               # Bitget 数据源
    └── indicators/                 # 内置指标
```

## 安装

```bash
python3 -m venv myvenv
source myvenv/bin/activate
pip install -r requirements.txt
```

## 图表展示

```bash
./myvenv/bin/python web_tq_chart.py
```

常用默认参数可放到 `.env`：

```env
TQ_DEFAULT_PROVIDER=bitget
TQ_DEFAULT_SYMBOL=BTCUSDT
TQ_DEFAULT_DURATION_SECONDS=300
TQ_DEFAULT_DATA_LENGTH=800
TQ_DEFAULT_REFRESH_MS=200
TQ_DEFAULT_BAR_MODE=time
TQ_DEFAULT_HOST=0.0.0.0
TQ_DEFAULT_PORT=8050
```

## 实盘/观察模式

详见：`tq_app/LIVE_TRADING_README.md`

实盘入口支持分层配置：`config/defaults.yaml` 放项目默认值，`config/profiles/*.yaml` 放运行方案，`.env` 只建议放 API Key、邮件收件人等私密配置。

查看可用方案和最终生效配置：

```bash
./myvenv/bin/python run_live_trading.py --list-profiles
./myvenv/bin/python run_live_trading.py --profile live_5u --show-config
```

常用入口：

```bash
./myvenv/bin/python run_live_trading.py --profile email --continuous
./myvenv/bin/python run_live_trading.py --profile dry_run_5u --continuous
./myvenv/bin/python run_live_trading.py --profile live_5u --preflight
./myvenv/bin/python run_live_trading.py --profile live_5u --continuous
```

真实交易方案 `live_5u` 固定使用 5 USDT 保证金、10 倍杠杆、逐仓，并在合约账户余额不足时从现货账户自动划转。

详见：`tq_app/LIVE_TRADING_README.md`

## 回测

```bash
./myvenv/bin/python run_backtest.py --symbol BTCUSDT --duration 300 --length 1000 --strategy live_decision
```

默认回测会复用当前实盘信号策略和高周期过滤，并按实盘方式撮合：信号 K 线收完后，下一根 K 线开盘开仓；不自动模拟止盈止损，持仓直到出现反向实盘信号时平仓并反向开仓。

默认输出：

```text
backtest_outputs/latest/report.json
backtest_outputs/latest/report.md
backtest_outputs/latest/report_zh.md
backtest_outputs/latest/trades.csv
backtest_outputs/latest/candles.json
```

`backtest_outputs/` 是本地输出目录，不应提交。

## 环境变量

`.env.example` 保留了图表、Bitget、邮件、实盘、回测相关默认配置。`.env` 可保存本地密钥和运行参数，避免提交。
