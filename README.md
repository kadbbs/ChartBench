# ChartBench

ChartBench 是一个轻量行情、实盘和回测项目，当前 provider 边界如下：

- 图表模块：默认使用天勤量化 TqSdk 数据，展示实时 K 线、指标、Web 图表和合约切换。
- `binance` / `bitget`：图表、实盘、回测三个模块都支持。
- `tianqin`：目前只支持图表模块，不参与实盘和回测。
- 实盘模块：复用图表信号，执行观察邮件、dry-run 或真实交易所市价开仓，并支持持仓后的实盘风控平仓。
- 回测模块：按 K 线级别复现实盘决策，支持本地 K 线缓存、风控出场、legacy 撮合和参数矩阵。

## 文档入口

详细说明拆到模块文档：

```text
docs/CHART.md          # 图表展示和行情服务
docs/LIVE_TRADING.md   # 实盘/观察/dry-run/预检查
docs/BACKTESTING.md    # 回测、缓存、legacy 模型、参数矩阵
docs/ARCHITECTURE.md   # 领域核心、共享风控和自定义策略注册
docs/CLI.md            # 统一命令行、参数规范和配置检查
```

快速阅读建议：

1. 只看图表：读 [docs/CHART.md](docs/CHART.md)。
2. 准备实盘：读 [docs/LIVE_TRADING.md](docs/LIVE_TRADING.md)，先跑 `email` 和 `dry_run_5u`。
3. 做策略复盘：读 [docs/BACKTESTING.md](docs/BACKTESTING.md)，优先使用缓存 profile。
4. 扩展策略或领域能力：读 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 项目结构

```text
.
├── chartbench.py                    # 推荐的统一命令入口
├── web_tq_chart.py                 # 图表 Web 服务入口
├── run_live_trading.py             # 实盘/观察模式入口
├── run_backtest.py                 # 单次回测入口
├── run_backtest_matrix.py          # 参数矩阵回测入口
├── custom_indicators.py            # 自定义指标：merged_dkx_hull_ut 等
├── custom_strategies.py            # 可选的自定义 Strategy 注册入口
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
    ├── live_trading.py             # 实盘兼容导入入口
    ├── domain/                     # 纯信号、Strategy 注册和共享风控
    ├── application/                # 实盘应用编排
    ├── adapters/                   # 状态持久化等基础设施适配器
    ├── cli/                        # 统一命令路由和公共参数
    ├── configuration/              # 公共默认值、配置展示与校验
    ├── notifications.py            # 邮件发送
    ├── backtesting/                # 回测引擎和策略注册
    ├── data_sources/               # Tianqin/Binance/Bitget 行情接口适配
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
BINANCE_API_KEY=
BINANCE_API_SECRET=
BITGET_API_KEY=
BITGET_API_SECRET=
BITGET_API_PASSPHRASE=
TIANQIN_USERNAME=
TIANQIN_PASSWORD=
RESEND_API_KEY=
RESEND_FROM_EMAIL=
LIVE_TRADING_EMAIL_TO=
```

不要把 `.env`、`logs/`、`backtest_outputs/`、`data_cache/` 提交到仓库。

## 常用命令

推荐统一入口：

```bash
./myvenv/bin/python chartbench.py --help
./myvenv/bin/python chartbench.py chart run
./myvenv/bin/python chartbench.py live run --profile email
./myvenv/bin/python chartbench.py backtest run --profile btc_5m_range_cached
```

完整说明见 [docs/CLI.md](docs/CLI.md)。以下旧入口继续兼容。

启动图表。默认 provider 是 `tianqin`，默认合约来自 `TQ_CHART_DEFAULT_SYMBOL`：

```bash
./myvenv/bin/python web_tq_chart.py
```

显式启用回测研究工作台：

```bash
./myvenv/bin/python chartbench.py chart run --backtest-ui
# 浏览器打开 http://127.0.0.1:8050/backtests
```

工作台支持三年缓存覆盖检查、指标/风控参数组合、后台单任务执行、稳健排名、
参数热力图和候选组合复测。回测运行接口默认关闭；启用后如果服务监听
`0.0.0.0`，请通过防火墙或带认证的反向代理限制访问。

临时看 Binance / Bitget 图表：

```bash
./myvenv/bin/python web_tq_chart.py --provider binance --symbol BTCUSDT
./myvenv/bin/python web_tq_chart.py --provider bitget --symbol BTCUSDT
```

查看实盘 profile：

```bash
./myvenv/bin/python run_live_trading.py --list-profiles
./myvenv/bin/python run_live_trading.py --profile live_5u --show-config
```

查看当前已注册策略（包括 `custom_strategies.py`）：

```bash
./myvenv/bin/python run_live_trading.py --list-strategies
./myvenv/bin/python run_backtest.py --list-strategies
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

回测 1D 主趋势、1H Hull/STC 同向重复开仓策略：

```bash
./myvenv/bin/python run_backtest.py --profile btc_5m_range_cached --strategy stc_1d_1h_reentry
```

legacy 回测，也就是不使用持仓风控出场，只按反向信号换仓：

```bash
./myvenv/bin/python run_backtest.py --profile btc_5m_range_cached_legacy
```

参数矩阵回测：

```bash
./myvenv/bin/python run_backtest_matrix.py --matrix btc_risk_matrix --dry-run
./myvenv/bin/python run_backtest_matrix.py --matrix btc_risk_matrix
```

旧 `--dry-run` 等价于统一命令中的 `--plan`。

## 当前默认交易模型

- 图表默认 `provider=tianqin`，默认合约 `KQ.m@SHFE.cu`；天勤下拉内置国内期货主连品种，并在账号可用时追加各品种未到期具体合约，均显示中文简称，仅影响 `web_tq_chart.py`。
- 实盘/回测支持 `provider=binance` 和 `provider=bitget`，当前项目默认及所有内置 profile 均固定使用 `bitget`。
- 实盘/回测默认交易标的为 Bitget `BTCUSDT`、产品线 `USDT-FUTURES`；Binance `UM-FUTURES` 仅作为可选 provider 保留。
- 实盘真实 profile `live_5u` 使用 `5U` 保证金、`10x`、逐仓，并启用 BTC run_0024 持仓风控出场参数。
- Bitget 实盘下单使用 USDT-FUTURES 双向持仓参数；策略层面禁止真实多空同时持有。
- Bitget 实盘下单使用 USDT-FUTURES 双向持仓参数：开多 `side=buy, tradeSide=open, holdSide=long`，开空 `side=sell, tradeSide=open, holdSide=short`。
- 回测默认总资金 `20000U`，单笔固定使用 `1000U` 保证金，`10x` 杠杆，即 `10000U` 名义价值。
- 回测默认手续费 `fee_rate=0.00023`，在 10x 下一次开平仓合计约为保证金 `0.46%`。
- 回测默认启用持仓风控出场；legacy profile 可关闭。

## 数据链路

图表主链路使用天勤量化 TqSdk：

```text
K 线接口：TqApi.get_kline_serial(symbol, duration_seconds, data_length)
刷新机制：TqApi.wait_update()
账号配置：TIANQIN_USERNAME / TIANQIN_PASSWORD
默认品种：内置 SHFE/DCE/CZCE/INE/GFEX/CFFEX 主连列表，标签形如“沪铜主连 · KQ.m@SHFE.cu”；账号可用时低频查询并缓存未到期具体合约，标签形如“沪铜 · SHFE.cu2607”
```

Binance USD-M Futures 链路：

```text
REST 基础地址：https://fapi.binance.com
WebSocket 行情：wss://fstream.binance.com/market
K 线接口：GET /fapi/v1/klines
市价下单：POST /fapi/v1/order
交易所端条件止损：POST /fapi/v1/algoOrder
持仓查询：GET /fapi/v3/positionRisk
```

Bitget USDT-FUTURES 链路：

```text
REST 基础地址：https://api.bitget.com
WebSocket 行情：wss://ws.bitget.com/v2/ws/public
K 线接口：GET /api/v2/mix/market/candles
市价下单：POST /api/v2/mix/order/place-order
交易所端条件止损：POST /api/v2/mix/order/place-tpsl-order
持仓查询：GET /api/v2/mix/position/all-position
```

## 输出目录

```text
logs/                         # 实盘日志和本地仓位账本
backtest_outputs/             # 单次回测和矩阵回测结果
data_cache/backtest_klines/   # 本地 K 线缓存
```

这些目录都是本地运行产物，默认不应提交。
