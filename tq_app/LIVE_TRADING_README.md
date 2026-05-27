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

只执行 Bitget 私有接口和合约配置预检查：

```bash
./myvenv/bin/python run_live_trading.py --preflight
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

开仓订单类型：

```env
LIVE_TRADING_ENTRY_ORDER_TYPE=market
LIVE_TRADING_MAKER_PRICE_LEVELS=3
LIVE_TRADING_MAKER_RETRY_ATTEMPTS=3
LIVE_TRADING_MAKER_RETRY_DELAY_SECONDS=0.3
LIVE_TRADING_MAKER_FALLBACK_TO_MARKET=false
LIVE_TRADING_ENTRY_TIME_FILTER_ENABLED=false
LIVE_TRADING_ENTRY_TIME_START=20:00
LIVE_TRADING_ENTRY_TIME_END=24:00
LIVE_TRADING_HTF_HULL_FILTER_ENABLED=true
LIVE_TRADING_HTF_HULL_DURATION_SECONDS=3600
```

- `market`：直接市价开仓，然后立刻挂止盈止损。
- `maker`：提交 `post_only` 限价挂单。买单挂在 `best_bid - 3*tick`，卖单挂在 `best_ask + 3*tick`。挂单成交后，常驻监控会再按成交均价挂止盈止损。
- maker 模式如果因为到达交易所时会吃单而被 `post_only` 拒绝，会在同一根信号内重新取盘口、重新计算价格并重试，默认 `3` 次。
- `LIVE_TRADING_MAKER_FALLBACK_TO_MARKET=true` 时，maker 重试仍失败会降级为 market 开仓，并立刻挂止盈止损。默认示例关闭，避免无意吃 taker。
- maker 单不保证成交；如果一直不成交，就不会开仓，也不会挂止盈止损。
- `LIVE_TRADING_ENTRY_TIME_FILTER_ENABLED=true` 时，只允许北京时间 `LIVE_TRADING_ENTRY_TIME_START <= 当前时间 < LIVE_TRADING_ENTRY_TIME_END` 之间新开仓。默认示例为 `20:00-24:00`；已有仓位、保护单监控、止盈止损不受这个限制。
- `LIVE_TRADING_HTF_HULL_FILTER_ENABLED=true` 时，会额外读取 `LIVE_TRADING_HTF_HULL_DURATION_SECONDS=3600` 的 Hull 船体趋势。1h 红色多趋势禁止 5m 开空；1h 绿色空趋势禁止 5m 开多。

止盈止损默认使用标记价作为开仓价格锚点，`2ATR = 1R`：

```env
LIVE_TRADING_TPSL_ENABLED=true
LIVE_TRADING_ENTRY_PRICE_SOURCE=mark_price
LIVE_TRADING_TPSL_TRIGGER_TYPE=mark_price
LIVE_TRADING_ATR_PERIOD=14
LIVE_TRADING_STOP_ATR_MULTIPLIER=2
LIVE_TRADING_TP1_R_MULTIPLE=1
LIVE_TRADING_TP1_SIZE_RATIO=0.5
LIVE_TRADING_TP2_R_MULTIPLE=1.5
LIVE_TRADING_PRICE_DECIMALS=2
LIVE_TRADING_SIZE_DECIMALS=6
LIVE_TRADING_TPSL_RETRY_ATTEMPTS=3
LIVE_TRADING_TPSL_RETRY_DELAY_SECONDS=1
LIVE_TRADING_CLOSE_ON_TPSL_FAILURE=false
LIVE_TRADING_TPSL_MONITOR_ENABLED=true
LIVE_TRADING_TPSL_MONITOR_INTERVAL_SECONDS=30
```

规则：

- 止损：开仓标记价反向 `2ATR`，即 `1R`，全仓止损。
- 第一档止盈：顺向 `1R`，平 `50%`。
- 第二档止盈：顺向 `1.5R`，平剩余仓位。
- 若开启 `LIVE_TRADING_TPSL_ENABLED=true` 但无法计算 ATR 或读取标记价，模块会拒绝开仓，避免裸仓。
- 止盈止损计划单提交失败会自动重试，默认重试 `3` 次。
- 若仍失败，会发送 `[URGENT]` 紧急邮件，邮件内包含开仓响应、失败保护单和已成功提交的保护单。
- `LIVE_TRADING_CLOSE_ON_TPSL_FAILURE=true` 时，保护单最终失败后会调用 Bitget `close-positions` 尝试市价平仓；默认关闭。
- 常驻进程会定期查询已挂保护单状态；止损、止盈触发成交、触发失败或取消时会发送邮件并标记已通知。

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

- 空单观察：同一根 K 线出现 `Sell` 或 `卖`，并且 `STC > 75` 且 STC 为红色，同时绿色空头带 `mhull_down / shull_down` 必须整体位于开仓 K 线 high 上方。
- 多单观察：同一根 K 线出现 `Buy` 或 `买`，并且 `STC < 25` 且 STC 为绿色，同时红色多头带 `mhull_up / shull_up` 必须整体位于开仓 K 线 low 下方。
- 如果红带/绿带穿进开仓 K 线区间，视为中穿，不开仓。
- 高周期过滤：默认再看 1h Hull 船体趋势，1h 红色多趋势时禁止 5m 反向开空，1h 绿色空趋势时禁止 5m 反向开多。

如果策略名不是 `stc_extreme_contrarian`，会退回 marker 模式：

- `any`：`Buy/买` 或 `Sell/卖` 任一触发。
- `ut`：只看 UT Bot 的 `Buy/Sell`。
- `dkx`：只看 DKX 的 `买/卖`。
- `confirmed`：UT 和 DKX 同向同时出现。

## 执行链路

`LiveTradingEngine.execute_decision()` 按顺序执行以下保护：

1. 没有下单信号：跳过，只写普通日志。
2. 同一个 `clientOid` 已执行过：跳过，防止同一根 K 线重复执行。
3. 观察/邮件模式也会尝试查询当前持仓；已有同方向仓位时，跳过开仓提醒并发送“已有同向仓位”邮件。
4. `LIVE_TRADING_LOG_ONLY=true`：观察模式，只写订单日志和发邮件，不构造真实下单请求。
5. `LIVE_TRADING_ORDER_SIZE` 为空：拒绝下单，并发送邮件。
6. `LIVE_TRADING_DRY_RUN=true` 或 `LIVE_TRADING_ENABLED=false`：构造请求但不发送到 Bitget。
7. 真实交易路径：执行 Bitget preflight，检查私有接口、合约、ticker、下单数量和精度。
8. 查询当前持仓。
9. 已有同方向仓位：跳过开仓，写订单日志并发送邮件。
10. 如配置 `LIVE_TRADING_LEVERAGE`，先设置杠杆。
11. market 模式读取 Bitget ticker 标记价，按 ATR 计算止损和两档止盈。
12. market 模式调用 Bitget `place-order` 下 market 开仓单，并立刻挂 1 个止损单和 2 个止盈单。
13. maker 模式读取盘口，提交 `post_only` limit 开仓单；若被拒绝则重新取盘口重试，成功提交后记录到 state 等待成交。
14. maker 开仓成交后，常驻监控按实际成交均价计算并挂止盈止损。
15. 保护单失败会自动重试；最终失败时发送紧急邮件，如启用 `LIVE_TRADING_CLOSE_ON_TPSL_FAILURE=true`，会尝试自动平仓。
16. 常驻循环按 `LIVE_TRADING_TPSL_MONITOR_INTERVAL_SECONDS` 查询保护单历史状态并发送触发通知。

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
- 止盈止损计划单：`POST /api/v2/mix/order/place-tpsl-order`
- 一键平仓：`POST /api/v2/mix/order/close-positions`
- 当前触发单查询：`GET /api/v2/mix/order/orders-plan-pending`
- 历史触发单查询：`GET /api/v2/mix/order/orders-plan-history`

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
- `LIVE_TRADING_PRICE_DECIMALS` 和 `LIVE_TRADING_SIZE_DECIMALS` 需要按合约规格确认。
- 已有同方向仓位会跳过开仓；反向仓位目前不会自动平仓或反手。
- 当前已有基础止损和两档止盈、保护单失败重试和可选自动平仓，但没有撤单、动态追踪、最大仓位、最大日亏损控制。
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

实盘预检查：

```bash
./myvenv/bin/python run_live_trading.py --preflight
```
