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

脚本会从 `config/defaults.yaml` 读取默认合约、周期、K 线数量等配置，`.env` 只建议保留 API Key、邮件收件人等本机私密信息。命令行参数优先级更高，例如：

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

当前实盘入口支持分层配置，优先级如下：

```text
config/defaults.yaml < config/profiles/<profile>.yaml < .env < shell 环境变量 < 命令行参数
```

当命令行显式传入 `--profile` 时，profile 会覆盖 `.env` 中同名的非密钥运行配置，避免旧 `.env` 里的 `LIVE_TRADING_MODE=live` 让 `--profile email` 失效。`.env` 仍建议只放 API Key、邮件收件人等私密/本机配置。

查看可用 profile：

```bash
./myvenv/bin/python run_live_trading.py --list-profiles
```

查看最终生效配置，敏感字段会脱敏：

```bash
./myvenv/bin/python run_live_trading.py --profile live_5u --show-config
```

默认运行参数放在 `config/defaults.yaml`：

```yaml
TQ_DEFAULT_PROVIDER: bitget
TQ_DEFAULT_SYMBOL: BTCUSDT
TQ_DEFAULT_DURATION_SECONDS: 300
TQ_DEFAULT_DATA_LENGTH: 800
TQ_DEFAULT_REFRESH_MS: 200
TQ_DEFAULT_BAR_MODE: time
```

运行模式：

```env
# off=关闭信号执行和邮件
# email=仅写观察日志/发邮件，不构造下单请求
# dry_run=构造下单请求并发邮件，但不发送到 Bitget
# live=真实下单
LIVE_TRADING_MODE=email
```

推荐流程：

1. `--profile email`：先确认信号邮件是否符合预期。
2. `--profile dry_run_5u`：检查构造出的 5U/10x/逐仓 Bitget 下单请求。
3. `--profile live_5u --preflight`：确认 API、5U/10x/逐仓配置和预检查通过。
4. `--profile live_5u --continuous`：真实常驻执行。

常用命令：

```bash
./myvenv/bin/python run_live_trading.py --profile email --continuous
./myvenv/bin/python run_live_trading.py --profile dry_run_5u --continuous
./myvenv/bin/python run_live_trading.py --profile live_5u --preflight
./myvenv/bin/python run_live_trading.py --profile live_5u --continuous
```

Bitget 私有接口配置：

```env
BITGET_API_KEY=
BITGET_API_SECRET=
BITGET_API_PASSPHRASE=
BITGET_DEFAULT_PRODUCT_TYPE=USDT-FUTURES
```

API Key 需要至少开启 Trade 权限；如果要合约账户不足 5U 时自动从现货划转，还需要 Transfer 权限。

真实下单固定配置位于 `config/profiles/live_5u.yaml`：

```env
LIVE_TRADING_MODE=live
LIVE_TRADING_MARGIN_AMOUNT=5
LIVE_TRADING_LEVERAGE=10
LIVE_TRADING_MARGIN_MODE=isolated
LIVE_TRADING_AUTO_TRANSFER_FROM_SPOT=true
LIVE_TRADING_AUTO_TRANSFER_MULTIPLIER=1.1
LIVE_TRADING_AUTO_TRANSFER_BUFFER=0
```

当前实盘下单不再要求手工填写 `LIVE_TRADING_ORDER_SIZE`。程序会用 `保证金 5U * 10倍杠杆 / 开仓价格` 自动换算 Bitget 合约下单数量，例如 BTC 65000 时约为 `0.000769 BTC`。若合约最小下单量高于该数量，`--preflight` 会拦截。

兼容旧配置：如果没有设置 `LIVE_TRADING_MODE`，程序仍会读取
`LIVE_TRADING_ENABLED`、`LIVE_TRADING_DRY_RUN`、`LIVE_TRADING_LOG_ONLY`。
旧配置下仍然需要 `LIVE_TRADING_ENABLED=true`、`LIVE_TRADING_DRY_RUN=false`、
`LIVE_TRADING_LOG_ONLY=false` 才会真实下单。

开仓订单类型：

```env
LIVE_TRADING_ENTRY_TIME_FILTER_ENABLED=false
LIVE_TRADING_ENTRY_TIME_START=20:00
LIVE_TRADING_ENTRY_TIME_END=24:00
LIVE_TRADING_HTF_HULL_FILTER_ENABLED=true
LIVE_TRADING_HTF_HULL_DURATION_SECONDS=3600
LIVE_TRADING_LOCAL_POSITION_ENABLED=true
LIVE_TRADING_LOCAL_POSITION_RECORD_OBSERVATION=true
LIVE_TRADING_POSITION_SYNC_ENABLED=true
LIVE_TRADING_POSITION_SYNC_REAL_ONLY=true
```

- 实盘开仓固定使用 Bitget `place-order` 的 `market` 市价单，也就是 taker 路径。
- 实盘保证金模式固定逐仓 `isolated`，`--preflight` 会在无持仓时尝试设置逐仓；真实信号触发时不再临时切换逐仓/全仓，避免 Bitget 因已有持仓或委托拒绝接口。
- 实盘杠杆固定 10 倍，`--preflight` 会设置 `leverage=10`；真实信号触发时不再临时设置杠杆，只发送开仓单。
- 实盘单笔使用 5 USDT 保证金，按当前开仓价格自动换算下单数量。
- 合约账户可用 USDT 不足目标预留保证金时，如果 `LIVE_TRADING_AUTO_TRANSFER_FROM_SPOT=true`，程序会从现货账户划转到 U 本位合约账户；这要求 API Key 开启 Transfer 权限。目标预留保证金 = `LIVE_TRADING_MARGIN_AMOUNT * LIVE_TRADING_AUTO_TRANSFER_MULTIPLIER + LIVE_TRADING_AUTO_TRANSFER_BUFFER`，默认就是 `5 * 1.1 + 0 = 5.5U`。
- 当前实盘模块不再支持 maker/post_only 开仓，不再自动挂止盈止损。
- `LIVE_TRADING_ENTRY_TIME_FILTER_ENABLED=true` 时，只允许北京时间 `LIVE_TRADING_ENTRY_TIME_START <= 当前时间 < LIVE_TRADING_ENTRY_TIME_END` 之间新开仓。默认示例为 `20:00-24:00`；已有仓位和账号持仓同步不受这个限制。
- `LIVE_TRADING_HTF_HULL_FILTER_ENABLED=true` 时，会额外读取 `LIVE_TRADING_HTF_HULL_DURATION_SECONDS=3600` 的 Hull 船体趋势和 STC 颜色。1h Hull 红色多趋势时禁止 5m 开空；1h Hull 绿色空趋势时禁止 5m 开多；如果 1h Hull 趋势和 1h STC 方向不一致，则不做任何动作。
- `LIVE_TRADING_POSITION_SYNC_ENABLED=true` 时，真实交易模式会定期用 Bitget 实际持仓校准本地仓位账本；本地和账号冲突时，以 Bitget 账号持仓为准。
- `LIVE_TRADING_POSITION_SYNC_REAL_ONLY=true` 时，只在真实交易模式校准，避免 only 邮件/观察模式里的本地虚拟仓位被空账号持仓覆盖。
- `LIVE_TRADING_POSITION_SYNC_INTERVAL_SECONDS=30` 控制常驻模式下账号持仓同步间隔。

当前实盘模块不再自动挂止盈止损，也不会因为缺少 ATR 拒绝开仓。以下 R 倍数参数为历史回测参数保留，当前默认实盘式回测不再用它们自动止盈止损：

```env
LIVE_TRADING_ATR_PERIOD=14
LIVE_TRADING_STOP_ATR_MULTIPLIER=2
LIVE_TRADING_TP1_R_MULTIPLE=1
LIVE_TRADING_TP1_SIZE_RATIO=0.5
LIVE_TRADING_TP2_R_MULTIPLE=1.5
```

默认安全状态是不真实下单：

```env
LIVE_TRADING_MODE=email
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
- 高周期过滤：默认再看 1h Hull 船体趋势和 1h STC 颜色。1h Hull 红色多趋势需要 1h STC 同为上升逻辑；1h Hull 绿色空趋势需要 1h STC 同为下降逻辑。二者方向不一致时不做任何动作。

如果策略名不是 `stc_extreme_contrarian`，会退回 marker 模式：

- `any`：`Buy/买` 或 `Sell/卖` 任一触发。
- `ut`：只看 UT Bot 的 `Buy/Sell`。
- `dkx`：只看 DKX 的 `买/卖`。
- `confirmed`：UT 和 DKX 同向同时出现。

## 执行链路

`LiveTradingEngine.execute_decision()` 按顺序执行以下保护：

1. 没有下单信号：跳过，只写普通日志。
1. 同一个 `clientOid` 已执行过：跳过，防止同一根 K 线重复执行。
1. 真实交易模式会先用 Bitget 实际持仓同步本地 `local_positions`；同步成功后账号为准，本地多出的 open 仓位会标记为 closed，账号里存在但本地没有的仓位会写入本地。
1. 查询本地仓位账本；已有同方向 open 仓位时，跳过开仓并发送“已有同向仓位”邮件。
1. 观察/邮件模式也会尝试查询当前持仓；已有同方向仓位时，跳过开仓提醒并发送“已有同向仓位”邮件。
1. `LIVE_TRADING_MODE=off`：只写普通运行日志，不写订单记录、不发邮件、不记录虚拟仓位。
1. `LIVE_TRADING_MODE=email`：只写订单观察日志和发邮件，不构造真实下单请求。
1. `LIVE_TRADING_MODE=dry_run`：按 5U/10x 自动计算下单数量，构造请求但不发送到 Bitget。
1. `LIVE_TRADING_MODE=live`：执行运行时 preflight，检查私有接口、合约、ticker、5U/10x 数量、精度、合约账户余额和现货可划转余额；不会在信号触发时临时切换逐仓/全仓或杠杆。
1. 查询当前持仓。
1. 已有同方向仓位：跳过开仓，写订单日志并发送邮件。
1. 已有反方向仓位：先调用 Bitget `close-positions` 平掉反向仓位，并确认反向仓位消失；如果仍存在，拒绝继续开仓。
1. 合约账户不足目标预留保证金时，从现货账户划转到 U 本位合约账户。
1. 调用 Bitget `place-order` 下 `market` 市价开仓单，不再自动挂止盈止损。
1. 常驻循环会继续同步账号持仓到本地账本。

当前实盘只生成 taker 市价单，不再读取 maker/post_only 相关环境变量。

## 幂等与去重

订单幂等键格式：

```text
tq-live-{symbol}-{side}-{bar_time}
```

## K 线级回测

当前分支提供独立回测入口：

```bash
./myvenv/bin/python run_backtest.py --profile latest_month
```

指定时间区间：

```bash
./myvenv/bin/python run_backtest.py --profile btc_5m_range
```

- `live_decision`：复用当前实盘信号判断，包括 5m 策略、1h Hull 趋势过滤和本地 Hull 带位置过滤。
- 回测配置文件位于 `config/backtests/*.yaml`；命令行参数仍可临时覆盖配置文件。
- 回测按 K 线级别撮合：信号在目标 K 线收完后确认，下一根 K 线 open 开仓。
- `--start-time` / `--end-time` 支持秒/毫秒时间戳或 ISO 时间；未带时区时按北京时间解析。只指定 `--end-time` 时仍沿用 `--length` 向前取 N 根 K 线。
- 回测退出方式与当前实盘执行保持一致：不自动模拟止盈止损，已有仓位会一直持有，直到出现反向实盘信号时在下一根 K 线 open 平仓，并按新方向重新开仓。
- 回测仓位固定为每笔 1000U 保证金、10 倍杠杆，即 10000U 名义价值；默认初始权益也是 1000U。
- 回测模块支持多策略扩展：新增策略只需要实现 `KlineStrategy.evaluate(snapshot)` 并在 `tq_app/backtesting/strategies.py` 注册。

默认输出目录：

```text
backtest_outputs/latest/
```

输出文件：

- `report.json`：参数、收益、胜率、最大回撤、交易明细。
- `report.md`：更适合人工阅读的专业摘要，包含收益、市场背景、交易质量、风险和假设。
- `report_zh.md`：中文专业摘要，包含分组表现和关键交易说明。
- `trades.csv`：开仓、平仓、分批止盈、R 倍数。
- `candles.json`：K 线、成交量、开仓/平仓 markers，可供前端或脚本画图。

记录文件：

```text
logs/live_trading_state.json
```

本地仓位账本字段在 `local_positions`。真实交易模式下，常驻进程会按账号持仓同步它：

- Bitget 有仓位，本地没有：新增 `source=exchange` 的 open 记录。
- 本地有 open，Bitget 没仓位：标记为 `status=closed`，`close_reason=exchange_sync_no_position`。
- 双方都有：更新 size、available、unrealizedPL、marginSize 和 `synced_at`。

only 邮件/观察模式默认不会用空账号覆盖本地虚拟仓位，因为 `LIVE_TRADING_POSITION_SYNC_REAL_ONLY=true`。

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
- 查询合约账户：`GET /api/v2/mix/account/accounts`
- 查询现货余额：`GET /api/v2/spot/account/assets`
- 现货转合约：`POST /api/v2/spot/wallet/transfer`
- 预检查设置逐仓：`POST /api/v2/mix/account/set-margin-mode`
- 预检查设置杠杆：`POST /api/v2/mix/account/set-leverage`
- 下单：`POST /api/v2/mix/order/place-order`
- 反向仓位平仓：`POST /api/v2/mix/order/close-positions`

官方文档参考：

- 合约账户查询：https://www.bitget.com/api-doc/contract/account/Get-Account-List
- 设置逐仓/全仓模式：https://www.bitget.com/api-doc/contract/account/Change-Margin-Mode
- 设置杠杆：https://www.bitget.com/api-doc/contract/account/Change-Leverage
- 合约下单：https://www.bitget.com/api-doc/contract/trade/Place-Order
- 合约一键平仓：https://www.bitget.com/api-doc/contract/trade/Flash-Close-Position
- 现货余额查询：https://www.bitget.com/api-doc/spot/account/Get-Account-Assets
- 现货/合约账户划转：https://www.bitget.com/api-doc/spot/account/Wallet-Transfer

签名方式是 Bitget v2 风格：

```text
timestamp + method + request_path + body
```

然后用 `API_SECRET` 做 HMAC-SHA256，再 base64。

## Review 重点

- `.env` 不应提交真实 API Key、Secret、Passphrase。
- 真实上线前应先跑 `LIVE_TRADING_MODE=email` 观察信号和邮件。
- 再跑 `LIVE_TRADING_MODE=dry_run` 检查订单请求。
- 最后才切到 `LIVE_TRADING_MODE=live`。
- `LIVE_TRADING_MARGIN_AMOUNT=5`、`LIVE_TRADING_LEVERAGE=10` 和逐仓是当前实盘固定配置。
- API Key 必须开启 Trade；若启用自动划转，还必须开启 Transfer。
- `--preflight` 必须通过；尤其要确认 5U/10x 自动计算出的 size 不低于 Bitget 合约最小下单量。
- `LIVE_TRADING_PRICE_DECIMALS` 和 `LIVE_TRADING_SIZE_DECIMALS` 需要按合约规格确认。
- 已有同方向仓位会跳过开仓；反向仓位会先平仓，确认反向仓位消失后才继续开仓。
- 当前实盘模块不再自动挂止盈止损，也没有最大仓位、最大日亏损控制。
- 常驻进程建议交给 `systemd`、`supervisor` 或 Docker restart policy 托管。

## 快速验证

语法检查：

```bash
./myvenv/bin/python -m compileall run_live_trading.py tq_app/live_trading.py
```

观察模式跑一轮：

```bash
LIVE_TRADING_MODE=email ./myvenv/bin/python run_live_trading.py
```

常驻观察：

```bash
LIVE_TRADING_MODE=email ./myvenv/bin/python run_live_trading.py --continuous
```

实盘预检查：

```bash
./myvenv/bin/python run_live_trading.py --preflight
```
