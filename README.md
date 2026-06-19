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

回测不需要 Bitget API Key，只用 Bitget 公共 K 线接口。第一次使用按下面流程走即可。

### 1. 查看可用回测配置

```bash
./myvenv/bin/python run_backtest.py --list-profiles
```

当前内置两个配置：

- `latest_month`：按最近 N 根 5m K 线回测，适合快速确认功能。
- `btc_5m_range`：按指定开始/结束时间回测，适合复盘某段行情。

配置文件在 `config/backtests/`：

```text
config/backtests/latest_month.yaml
config/backtests/btc_5m_range.yaml
```

### 2. 跑一次默认回测

```bash
./myvenv/bin/python run_backtest.py --profile latest_month
```

成功后会输出 `metrics`、`output_dir` 和实际使用的 `config`。默认结果写到：

```text
backtest_outputs/latest/
```

### 3. 按时间区间回测

打开 `config/backtests/btc_5m_range.yaml`，修改：

```yaml
start_time: "2026-05-31 00:00:00"
end_time: "2026-06-06 00:00:00"
output_dir: backtest_outputs/btc_5m_range
```

然后运行：

```bash
./myvenv/bin/python run_backtest.py --profile btc_5m_range
```

未带时区的时间会按北京时间解析。5m 长区间会使用 Bitget 历史 K 线接口，第一次拉一年数据会比较慢。

### 4. 临时覆盖配置

不想改 YAML 时，可以直接在命令行覆盖某一项：

```bash
./myvenv/bin/python run_backtest.py --profile btc_5m_range --end-time "2026-06-10 00:00:00" --output-dir backtest_outputs/test_run
```

### 5. 查看报告

每次回测会生成：

```text
report.json      # 完整结构化数据，适合程序读取
report.md        # 英文摘要
report_zh.md     # 中文摘要，优先看这个
trades.csv       # 逐笔交易明细，包含点数、手续费、净收益
candles.json     # K 线和开平仓标记，供图表或脚本使用
```

回测会复用当前实盘信号策略和高周期过滤，并按实盘方式撮合：信号 K 线收完后，下一根 K 线开盘开仓；不自动模拟止盈止损，持仓直到出现反向实盘信号时平仓并反向开仓。

当前回测仓位固定为每笔 `1000U` 保证金、`10` 倍杠杆，即 `10000U` 名义价值；默认初始权益也是 `1000U`。报告里会同时展示 USDT 收益和价格点数。

### 常见问题

- 如果回测区间很长但很快结束，并且报告起点不是你填的起点，说明数据没有覆盖到请求区间；当前代码会主动报错避免这种静默截断。
- 如果遇到 SSL EOF 或网络中断，公共行情请求会自动重试；连续失败时重新运行即可。
- `backtest_outputs/` 是本地输出目录，不应提交。

## 环境变量

`.env.example` 保留了图表、Bitget、邮件、实盘、回测相关默认配置。`.env` 可保存本地密钥和运行参数，避免提交。
