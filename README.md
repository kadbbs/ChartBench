# Bitget ChartBench

ChartBench 是一个围绕 Bitget U 本位永续合约的轻量项目，包含三条核心链路：

- 图表模块：实时 K 线、指标、Web 图表和合约切换。
- 实盘模块：复用图表信号，执行观察邮件、dry-run 或真实 Bitget 市价开仓，并支持持仓后的实盘风控平仓。
- 回测模块：按 K 线级别复现实盘决策，支持本地 K 线缓存、风控出场、legacy 撮合和参数矩阵。

## 文档入口

详细说明拆到三个模块文档：

```text
docs/CHART.md          # 图表展示和行情服务
docs/LIVE_TRADING.md   # 实盘/观察/dry-run/预检查
docs/BACKTESTING.md    # 回测、缓存、legacy 模型、参数矩阵
```

快速阅读建议：

1. 只看图表：读 [docs/CHART.md](docs/CHART.md)。
2. 准备实盘：读 [docs/LIVE_TRADING.md](docs/LIVE_TRADING.md)，先跑 `email` 和 `dry_run_5u`。
3. 做策略复盘：读 [docs/BACKTESTING.md](docs/BACKTESTING.md)，优先使用缓存 profile。

## 项目结构

```text
.
├── web_tq_chart.py                 # 图表 Web 服务入口
├── run_live_trading.py             # 实盘/观察模式入口
├── run_backtest.py                 # 单次回测入口
├── run_backtest_matrix.py          # 参数矩阵回测入口
├── custom_indicators.py            # 自定义指标：merged_dkx_hull_ut 等
├── config/
│   ├── defaults.yaml               # 非密钥默认配置
│   ├── profiles/                   # 实盘/观察运行方案
│   ├── backtests/                  # 单次回测配置
│   └── backtest_matrices/          # 参数矩阵配置
├── docs/                           # 模块文档
├── static/                         # 前端资源
├── templates/                      # Flask 模板
└── tq_app/
    ├── web.py                      # Web API
    ├── service.py                  # 行情和指标聚合
    ├── live_trading.py             # 实盘执行核心
    ├── notifications.py            # 邮件发送
    ├── backtesting/                # 回测引擎和策略注册
    ├── data_sources/               # Bitget 行情/接口适配
    └── indicators/                 # 内置指标
```

## 安装

```bash
python3 -m venv myvenv
source myvenv/bin/activate
pip install -r requirements.txt
```

## 配置原则

配置分成三类：

```text
config/defaults.yaml             # 项目默认值，不放密钥
config/profiles/*.yaml           # 实盘/观察/dry-run 运行方案
config/backtests/*.yaml          # 回测方案
config/backtest_matrices/*.yaml  # 参数矩阵方案
.env                             # 本机私密信息，不提交
```

`.env` 只建议放：

```env
BITGET_API_KEY=
BITGET_API_SECRET=
BITGET_API_PASSPHRASE=
RESEND_API_KEY=
RESEND_FROM_EMAIL=
LIVE_TRADING_EMAIL_TO=
```

不要把 `.env`、`logs/`、`backtest_outputs/`、`data_cache/` 提交到仓库。

## 常用命令

启动图表：

```bash
./myvenv/bin/python web_tq_chart.py
```

查看实盘 profile：

```bash
./myvenv/bin/python run_live_trading.py --list-profiles
./myvenv/bin/python run_live_trading.py --profile live_5u --show-config
```

观察模式：

```bash
./myvenv/bin/python run_live_trading.py --profile email --continuous
```

dry-run：

```bash
./myvenv/bin/python run_live_trading.py --profile dry_run_5u --continuous
```

真实实盘预检查和运行：

```bash
./myvenv/bin/python run_live_trading.py --profile live_5u --preflight
./myvenv/bin/python run_live_trading.py --profile live_5u --continuous
```

单次回测：

```bash
./myvenv/bin/python run_backtest.py --profile btc_5m_range_cached
```

legacy 回测，也就是不使用持仓风控出场，只按反向信号换仓：

```bash
./myvenv/bin/python run_backtest.py --profile btc_5m_range_cached_legacy
```

参数矩阵回测：

```bash
./myvenv/bin/python run_backtest_matrix.py --matrix btc_risk_matrix --dry-run
./myvenv/bin/python run_backtest_matrix.py --matrix btc_risk_matrix
./myvenv/bin/python run_backtest_matrix.py --matrix btc_indicator_param_matrix_v1 --dry-run
./myvenv/bin/python run_backtest_matrix.py --matrix btc_indicator_param_matrix_v1
```

## 当前默认交易模型

- 交易标的默认 `BTCUSDT`，产品线默认 `USDT-FUTURES`。
- 实盘真实 profile `live_5u` 使用 `5U` 保证金、`10x`、逐仓，并启用 BTC run_0024 持仓风控出场参数。
- Bitget 实盘下单使用双向持仓 `hedge_mode` 参数格式，但策略层面禁止真实多空同时持有。
- 回测仓位固定 `1000U` 保证金、`10x`，即 `10000U` 名义价值。
- 回测默认手续费 `fee_rate=0.00023`，在 10x 下一次开平仓合计约为保证金 `0.46%`。
- 回测默认启用持仓风控出场；legacy profile 可关闭。

## 输出目录

```text
logs/                         # 实盘日志和本地仓位账本
backtest_outputs/             # 单次回测和矩阵回测结果
data_cache/backtest_klines/   # 本地 K 线缓存
```

这些目录都是本地运行产物，默认不应提交。
