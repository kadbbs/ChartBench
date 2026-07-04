# 实盘模块

实盘模块负责把图表信号转成观察邮件、dry-run 请求或真实 Binance USD-M 市价开仓。真实交易前必须先跑 `email`、`dry_run_5u` 和 `--preflight`。

## 入口

查看可用 profile：

```bash
./myvenv/bin/python run_live_trading.py --list-profiles
```

查看最终生效配置，敏感字段会脱敏：

```bash
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

真实交易预检查：

```bash
./myvenv/bin/python run_live_trading.py --profile live_5u --preflight
```

真实常驻：

```bash
./myvenv/bin/python run_live_trading.py --profile live_5u --continuous
```

## 运行模式

```text
off      # 关闭信号执行和邮件
email    # 只写观察日志和发邮件，不构造下单请求
dry_run  # 构造 Binance 下单请求并发邮件，但不发送到 Binance
live     # 真实下单
```

推荐顺序：

1. `email`：确认信号邮件是否符合预期。
2. `dry_run_5u`：确认请求参数、数量、方向和日志。
3. `live_5u --preflight`：确认账户权限、合约、杠杆、逐仓、余额。
4. `live_5u --continuous`：真实运行。

## 配置分层

配置优先级：

```text
config/defaults.yaml < config/profiles/<profile>.yaml < .env < shell 环境变量 < 命令行参数
```

使用 `--profile` 时，profile 会覆盖 `.env` 中同名非密钥运行配置，避免旧 `.env` 里的运行模式影响当前 profile。

`.env` 建议只放：

```env
BINANCE_API_KEY=
BINANCE_API_SECRET=
RESEND_API_KEY=
RESEND_FROM_EMAIL=
LIVE_TRADING_EMAIL_TO=
```

## 当前真实交易规格

`config/profiles/live_5u.yaml` 当前用于真实交易：

```yaml
LIVE_TRADING_MODE: live
LIVE_TRADING_MARGIN_AMOUNT: 5
LIVE_TRADING_LEVERAGE: 10
LIVE_TRADING_MARGIN_MODE: isolated
LIVE_TRADING_POSITION_MODE: hedge_mode
LIVE_TRADING_AUTO_TRANSFER_FROM_SPOT: true
LIVE_TRADING_AUTO_TRANSFER_MULTIPLIER: 1.1
LIVE_TRADING_AUTO_TRANSFER_BUFFER: 0
LIVE_TRADING_RISK_EXITS_ENABLED: true
```

含义：

- 单笔使用 `5U` 保证金。
- 杠杆 `10x`。
- 保证金模式逐仓 `isolated`。
- 使用 Binance Hedge Mode 参数格式。
- 策略层面禁止真实多空同时持有。
- 合约账户不足目标预留保证金时，从现货账户自动划转。
- 默认目标预留保证金是 `5 * 1.1 + 0 = 5.5U`。
- 真实持仓后启用 BTC run_0024 风控出场参数。

## Binance 持仓模式

当前实盘请求使用 Binance Hedge Mode 格式：

```text
开多：side=BUY, positionSide=LONG
开空：side=SELL, positionSide=SHORT
```

注意：

- 需要先在 Binance Futures 后台把 USD-M Futures 切到 Hedge Mode。
- 程序不会在真实信号触发时临时切换 position mode。
- 这样做是为了匹配 Binance 双向持仓参数，策略上仍然不允许同时持有多空。

反向信号处理：

1. 发现已有反向仓位。
2. 先提交 Binance 市价单平掉反向仓位。
3. 确认反向仓位消失。
4. 再按新方向开仓。
5. 如果仍检测到反向仓位，拒绝开新仓。

同向仓位处理：

- 已有同方向仓位时，跳过开仓，避免重复加仓。
- 同一个高周期 Hull 同色段内，同方向只允许开一次仓。
- 只要某个 `symbol + side + 高周期 Hull 同色段` 已经真实开过仓，即使后面手动平仓、风控平仓或同步发现仓位消失，本段也不会再开同向仓位。
- Hull 高周期颜色切换后，锁 key 变化，才允许同方向再次出现一次新开仓机会。

## 下单数量

当前不需要手工配置 `LIVE_TRADING_ORDER_SIZE`。

程序按公式计算：

```text
下单数量 = 保证金 * 杠杆 / 开仓价格
```

例如 BTC 价格 65000：

```text
5U * 10 / 65000 = 0.000769 BTC
```

`--preflight` 会查询 Binance 合约规格，检查最小下单量、价格精度和数量精度。

## 自动划转

如果：

```yaml
LIVE_TRADING_AUTO_TRANSFER_FROM_SPOT: true
```

且合约账户可用 USDT 小于目标预留保证金，则会从现货账户划转到 U 本位合约账户。

目标预留保证金：

```text
LIVE_TRADING_MARGIN_AMOUNT * LIVE_TRADING_AUTO_TRANSFER_MULTIPLIER + LIVE_TRADING_AUTO_TRANSFER_BUFFER
```

默认：

```text
5 * 1.1 + 0 = 5.5U
```

API Key 需要 Transfer 权限。

## 持仓风控出场

`live_5u` 当前启用持仓后的实盘风控：

```yaml
LIVE_TRADING_RISK_EXITS_ENABLED: true
LIVE_TRADING_RISK_CHECK_INTERVAL_SECONDS: 5
LIVE_TRADING_RISK_WEBSOCKET_TICKER_ENABLED: true
LIVE_TRADING_RISK_WEBSOCKET_TICKER_STALE_SECONDS: 5
LIVE_TRADING_RISK_ERROR_EMAIL_COOLDOWN_SECONDS: 300
LIVE_TRADING_EXCHANGE_DISASTER_SL_ENABLED: true
LIVE_TRADING_RISK_CLOSE_MANAGED_SIZE_ONLY: true
LIVE_TRADING_RISK_PRICE_SOURCE: mark_price
LIVE_TRADING_RISK_STARTUP_CHECK_BARS_5M: 24
LIVE_TRADING_RISK_STARTUP_MAX_FAVORABLE_POINTS: 300
LIVE_TRADING_RISK_STARTUP_CURRENT_POINTS: -120
LIVE_TRADING_RISK_DISASTER_STOP_POINTS: -1800
LIVE_TRADING_RISK_BREAKEVEN_TRIGGER_POINTS: 800
LIVE_TRADING_RISK_BREAKEVEN_STOP_POINTS: 100
LIVE_TRADING_RISK_TRAILING_TRIGGER_1_POINTS: 2000
LIVE_TRADING_RISK_TRAILING_PROTECT_1_RATIO: 0.4
LIVE_TRADING_RISK_TRAILING_TRIGGER_2_POINTS: 4000
LIVE_TRADING_RISK_TRAILING_PROTECT_2_RATIO: 0.5
LIVE_TRADING_RISK_TRAILING_TRIGGER_3_POINTS: 8000
LIVE_TRADING_RISK_TRAILING_PROTECT_3_RATIO: 0.6
```

含义：

- 点数按开仓价到当前标记价计算；多单是 `当前价 - 开仓价`，空单是 `开仓价 - 当前价`。
- 启动失败止损：开仓后第 `24` 根 5m K 线检查一次，如果最大浮盈 `< 300` 点且当前点数 `< -120` 点，市价平仓。
- 灾难硬止损：任何时候最大浮亏或当前浮亏达到 `-1800` 点，市价平仓。
- 保本保护：最大浮盈达到 `800` 点后，保护线抬到 `+100` 点。
- 移动保护：最大浮盈达到 `2000 / 4000 / 8000` 点后，分别保护最大浮盈的 `40% / 50% / 60%`。

实现方式：

- 开仓成功后会先确认 Binance 已能查到同向持仓，再设置交易所服务器端灾难止损。
- 交易所端灾难止损使用 Binance `POST /fapi/v1/algoOrder`，`algoType=CONDITIONAL`、`type=STOP_MARKET`，按本策略计算出的数量覆盖。
- 当保本/移动保护线抬高时，会取消旧的 Binance 条件止损并重新挂更高保护价的条件止损。
- 本地常驻进程会订阅 Binance 公共 WebSocket ticker，收到 tick 后立即用最新标记价检查已管理仓位风控。
- 如果 WebSocket ticker 超过 `LIVE_TRADING_RISK_WEBSOCKET_TICKER_STALE_SECONDS` 未更新，会回退 REST ticker。
- 持仓同步仍按 `LIVE_TRADING_POSITION_SYNC_INTERVAL_SECONDS` 定期查询 Binance，tick 风控不会每个 tick 都查私有持仓接口。
- 本地触发风控时优先只平本策略记录的 `managed_size`；如果交易所仓位大小一致，则按该方向仓位数量提交市价平仓单。
- 如果只平了 `managed_size` 后交易所仍有同方向剩余仓位，剩余仓位会被视为手动/外部仓位，自动排除风控直到该方向仓位清空。
- 平仓提交后会再次查询 Binance 持仓，确认该方向仓位已关闭或已减少到目标 size，否则不把本地仓位标记为 closed。
- 本地确认仓位关闭时，会尝试通过 Binance `DELETE /fapi/v1/algoOrder` 清理已知止损计划单。
- 风控异常邮件有 `LIVE_TRADING_RISK_ERROR_EMAIL_COOLDOWN_SECONDS` 冷却，当前同一仓位同类异常 `300` 秒最多发一次。
- 风控状态写入 `logs/live_trading_state.json`，包括入场价、当前点数、最大浮盈、最大浮亏、保护线和启动检查状态。
- 未知来源的交易所仓位默认不自动接管风控，避免把手动仓位误当成本策略仓位平掉。
- 该逻辑只在 `LIVE_TRADING_MODE=live` 下真实平仓；`email` / `dry_run` 不会调用平仓接口。

重要限制：

- 服务器端只挂灾难止损；启动失败、保本和移动保护仍依赖本地常驻进程。
- 服务器端止损会尽量随保护线上移，但修改失败时仍会触发本地异常邮件；是否已真正生效以 Binance 返回为准。
- 本地最大浮盈/浮亏由 Binance WebSocket ticker 推动更新；WebSocket 断线或过期时会自动退回 REST ticker。

## 策略决策

实盘复用图表 snapshot，默认策略是：

```yaml
LIVE_TRADING_STRATEGY: stc_extreme_contrarian
LIVE_TRADING_USE_CLOSED_BAR: true
LIVE_TRADING_HTF_HULL_FILTER_ENABLED: true
LIVE_TRADING_HTF_HULL_DURATION_SECONDS: 14400
```

默认使用上一根已收完 K 线，减少未收线重绘。

多单观察：

- 同一根 K 线出现 `Buy` 或 `买`。
- `STC < 25`。
- STC 为绿色。
- 红色 Hull 带整体在开仓 K 线 low 下方。
- 高周期 Hull / STC 过滤允许顺势开多。

空单观察：

- 同一根 K 线出现 `Sell` 或 `卖`。
- `STC > 75`。
- STC 为红色。
- 绿色 Hull 带整体在开仓 K 线 high 上方。
- 高周期 Hull / STC 过滤允许顺势开空。

如果不使用 `stc_extreme_contrarian`，会退回 marker 模式：

```text
any        # Buy/买 或 Sell/卖 任一触发
ut         # 只看 UT Bot 的 Buy/Sell
dkx        # 只看 DKX 的 买/卖
confirmed  # UT 和 DKX 同向同时出现
```

## 执行链路

真实模式一次信号的关键顺序：

1. 同一 `clientOid` 去重。
2. 同步 Binance 实际持仓到本地账本，并在常驻进程里检查持仓风控。
3. 检查本地/交易所同向仓位，有同向则跳过。
4. 检查反向仓位，有反向则先平仓并确认消失。
5. 检查合约账户余额，不足则按配置从现货划转。
6. 构造市价开仓单。
7. 调用 Binance `POST /fapi/v1/order`。
8. 确认同向持仓已出现，并设置交易所端灾难止损。
9. 写订单日志、状态文件和邮件。

当前真实开仓固定是 taker 市价单，不再支持 maker/post_only。

## 日志和状态

普通日志：

```text
logs/live_trading.log
```

订单/观察 JSONL：

```text
logs/live_trading_orders.jsonl
```

本地仓位账本：

```text
logs/live_trading_state.json
```

真实模式会定期用 Binance 持仓同步本地账本：

- Binance 有仓位、本地没有：新增 exchange 来源 open 记录。
- 本地有 open、Binance 没仓位：标记 closed。
- 双方都有：更新 size、available、unrealizedPL、marginSize。

## API 权限

API Key 至少需要：

- 合约订单读写。
- 合约持仓读写。
- 钱包划转读写，如果启用自动现货转合约。
- 现货资产读取/相关权限，用于查询可划转余额。

## Binance 官方接口

当前封装使用：

- 查询持仓：`GET /fapi/v3/positionRisk`
- 查询合约账户：`GET /fapi/v3/account`
- 查询现货余额：`GET /api/v3/account`
- 现货转 USD-M Futures：`POST /sapi/v1/asset/transfer`，`type=MAIN_UMFUTURE`
- 预检查设置逐仓：`POST /fapi/v1/marginType`
- 预检查设置杠杆：`POST /fapi/v1/leverage`
- 下单/平仓：`POST /fapi/v1/order`
- 交易所端条件止损：`POST /fapi/v1/algoOrder`
- 取消交易所端条件止损：`DELETE /fapi/v1/algoOrder`
- 公共 ticker WebSocket：`wss://fstream.binance.com/market/ws/<stream>`

官方文档：

- https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info
- https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Kline-Candlestick-Data
- https://developers.binance.com/docs/derivatives/usds-margined-futures/trade/rest-api
- https://developers.binance.com/docs/derivatives/usds-margined-futures/trade/rest-api/New-Algo-Order
- https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams

## 真实运行前检查清单

1. Binance USD-M Futures 后台已切到 Hedge Mode。
2. API Key 权限完整。
3. `.env` 已填写 `BINANCE_API_KEY` / `BINANCE_API_SECRET`。
4. `--profile live_5u --show-config` 检查参数正确。
5. `--profile live_5u --preflight` 返回 ok。
6. 先跑过 `email` 和 `dry_run_5u`。
7. 确认合约账户或现货账户有足够 USDT。
