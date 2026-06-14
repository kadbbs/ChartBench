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

常驻观察或实盘入口：

```bash
./myvenv/bin/python run_live_trading.py --continuous
```

真实交易必须显式打开：

```env
LIVE_TRADING_ENABLED=true
LIVE_TRADING_DRY_RUN=false
LIVE_TRADING_LOG_ONLY=false
```

## 回测

```bash
./myvenv/bin/python run_backtest.py --symbol BTCUSDT --duration 300 --length 1000 --strategy live_decision
```

默认输出：

```text
backtest_outputs/latest/report.json
backtest_outputs/latest/trades.csv
backtest_outputs/latest/candles.json
```

`backtest_outputs/` 是本地输出目录，不应提交。

## 环境变量

`.env.example` 保留了图表、Bitget、邮件、实盘、回测相关默认配置。`.env` 可保存本地密钥和运行参数，避免提交。
