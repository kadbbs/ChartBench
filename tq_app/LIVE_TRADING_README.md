# Bitget 实盘模块说明

这份文档只说明当前 `bitget-live` 分支里的实盘执行链路，方便 review。

## 入口

一次性执行一轮决策：

```bash
./myvenv/bin/python run_live_trading.py
```

常驻连续执行：

```bash
./myvenv/bin/python run_live_trading.py --continuous
```

常驻进程启动后会发送一封启动邮件，标题包含合约、周期和当前运行状态。

脚本会从 `.env` 读取默认合约、周期、K 线数量等配置。命令行参数优先级更高，例如：

```bash
./myvenv/bin/python run_live_trading.py --symbol BTCUSDT --duration 300 --continuous
```

## 关键文件

- `run_live_trading.py`：实盘入口，负责启动行情服务、获取 snapshot、循环执行。
- `tq_app/live_trading.py`：实盘核心，负责生成交易决策、风控拦截、下单、日志和邮件。
- `tq_app/service.py`：行情和指标聚合层，生成实盘模块消费的 snapshot。
- `tq_app/data_sources/bitget.py`：Bitget REST / WebSocket 行情源。
- `custom_indicators.py`：`merged_dkx_hull_ut` 信号来源。

## 配置

默认运行参数：

```env
TQ_DEFAULT_PROVIDER=bitget
TQ_DEFAULT_SYMBOL=BTCUSDT
TQ_DEFAULT_DURATION_SECONDS=180
TQ_DEFAULT_DATA_LENGTH=800
TQ_DEFAULT_REFRESH_MS=200
TQ_DEFAULT_BAR_MODE=time
```

Bitget 私有接口配置：

```env
BITGET_API_KEY=
BITGET_API_SECRET=
BITGET_API_PASSPHRASE=
BITGET_DEFAULT_PRODUCT_TYPE=USDT-FUTURES
```

真实下单必须同时满足：

```env
LIVE_TRADING_ENABLED=true
LIVE_TRADING_DRY_RUN=false
LIVE_TRADING_LOG_ONLY=false
LIVE_TRADING_ORDER_SIZE=0.001
```

默认安全状态是不真实下单：

```env
LIVE_TRADING_ENABLED=false
LIVE_TRADING_DRY_RUN=true
LIVE_TRADING_LOG_ONLY=true
```

邮件提醒：

```env
RESEND_API_KEY=
RESEND_FROM_EMAIL=onboarding@resend.dev
LIVE_TRADING_EMAIL_ENABLED=true
LIVE_TRADING_EMAIL_TO=
```

## 决策链路

1. `run_live_trading.py` 创建 `MarketDataService`。
2. 服务从 Bitget 拉取 K 线，并计算 `merged_dkx_hull_ut`、`stc`、`macd`。
3. `LiveTradingEngine.evaluate_snapshot()` 选择目标 K 线。
4. 默认 `LIVE_TRADING_USE_CLOSED_BAR=true`，所以使用上一根已收完 K 线。
5. 引擎读取该 K 线上的 marker 文本和 STC 值/颜色。
6. 默认策略是 `stc_extreme_contrarian`。

默认策略规则：

- 空单观察：同一根 K 线出现 `Sell` 或 `卖`，并且 `STC > 75` 且 STC 为红色。
- 多单观察：同一根 K 线出现 `Buy` 或 `买`，并且 `STC < 25` 且 STC 为绿色。

如果策略名不是 `stc_extreme_contrarian`，会退回 marker 模式：

- `any`：`Buy/买` 或 `Sell/卖` 任一触发。
- `ut`：只看 UT Bot 的 `Buy/Sell`。
- `dkx`：只看 DKX 的 `买/卖`。
- `confirmed`：UT 和 DKX 同向同时出现。

## 执行链路

`LiveTradingEngine.execute_decision()` 按顺序执行以下保护：

1. 没有下单信号：跳过，只写普通日志。
2. 同一个 `clientOid` 已执行过：跳过，防止同一根 K 线重复执行。
3. `LIVE_TRADING_LOG_ONLY=true`：观察模式，只写订单日志和发邮件，不构造真实下单请求。
4. `LIVE_TRADING_ORDER_SIZE` 为空：拒绝下单，并发送邮件。
5. `LIVE_TRADING_DRY_RUN=true` 或 `LIVE_TRADING_ENABLED=false`：构造请求但不发送到 Bitget。
6. 真实交易路径：查询当前持仓。
7. 已有同方向仓位：跳过开仓，写订单日志并发送邮件。
8. 如配置 `LIVE_TRADING_LEVERAGE`，先设置杠杆。
9. 调用 Bitget `place-order` 下 market 单。

当前只自动生成 market 单。`LIVE_TRADING_ORDER_TYPE=limit` 会报错，因为还没有限价价格逻辑。

## 幂等与去重

订单幂等键格式：

```text
tq-live-{symbol}-{side}-{bar_time}
```

记录文件：

```text
logs/live_trading_state.json
```

常驻模式还会在进程内按 `bar_time` 去重，避免同一根已收完 K 线因 WebSocket 多次更新被反复执行。

## 日志

普通运行日志：

```text
logs/live_trading.log
```

订单/观察记录：

```text
logs/live_trading_orders.jsonl
```

每一行是一条 JSON，包含：

- `decision`
- `dry_run`
- `enabled`
- `request`
- `response`
- `error`
- `already_executed`

## Bitget 接口

私有接口封装在 `BitgetFuturesTradeClient`：

- 查询持仓：`GET /api/v2/mix/position/all-position`
- 设置杠杆：`POST /api/v2/mix/account/set-leverage`
- 下单：`POST /api/v2/mix/order/place-order`

签名方式是 Bitget v2 风格：

```text
timestamp + method + request_path + body
```

然后用 `API_SECRET` 做 HMAC-SHA256，再 base64。

## Review 重点

- `.env` 不应提交真实 API Key、Secret、Passphrase。
- 真实上线前应先跑 `LIVE_TRADING_LOG_ONLY=true` 观察信号和邮件。
- 再跑 `LIVE_TRADING_LOG_ONLY=false`、`LIVE_TRADING_DRY_RUN=true` 检查订单请求。
- 最后才打开 `LIVE_TRADING_ENABLED=true` 和 `LIVE_TRADING_DRY_RUN=false`。
- `LIVE_TRADING_ORDER_SIZE` 需要按 Bitget 合约规格确认最小下单量。
- 已有同方向仓位会跳过开仓；反向仓位目前不会自动平仓或反手。
- 当前没有止损、止盈、撤单、减仓、最大仓位、最大日亏损控制。
- 常驻进程建议交给 `systemd`、`supervisor` 或 Docker restart policy 托管。

## 快速验证

语法检查：

```bash
./myvenv/bin/python -m compileall run_live_trading.py tq_app/live_trading.py
```

观察模式跑一轮：

```bash
LIVE_TRADING_LOG_ONLY=true ./myvenv/bin/python run_live_trading.py
```

常驻观察：

```bash
LIVE_TRADING_LOG_ONLY=true ./myvenv/bin/python run_live_trading.py --continuous
```
