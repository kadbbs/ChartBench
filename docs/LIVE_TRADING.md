# 实盘模块

实盘模块负责把图表信号转成观察邮件、dry-run 请求或真实 Bitget 市价开仓。真实交易前必须先跑 `email`、`dry_run_5u` 和 `--preflight`。

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
dry_run  # 构造 Bitget 下单请求并发邮件，但不发送到 Bitget
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
BITGET_API_KEY=
BITGET_API_SECRET=
BITGET_API_PASSPHRASE=
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
```

含义：

- 单笔使用 `5U` 保证金。
- 杠杆 `10x`。
- 保证金模式逐仓 `isolated`。
- 使用 Bitget 双向持仓参数格式 `hedge_mode`。
- 策略层面禁止真实多空同时持有。
- 合约账户不足目标预留保证金时，从现货账户自动划转。
- 默认目标预留保证金是 `5 * 1.1 + 0 = 5.5U`。

## Bitget 持仓模式

当前实盘请求使用 Bitget 双向持仓格式：

```text
开多：side=buy, tradeSide=open
开空：side=sell, tradeSide=open
```

注意：

- 需要先在 Bitget App / Web 后台把 `USDT-FUTURES` 切到双向持仓。
- 程序不会在真实信号触发时临时切换 position mode。
- 这样做是为了匹配 Bitget 下单参数，策略上仍然不允许同时持有多空。

反向信号处理：

1. 发现已有反向仓位。
2. 先调用 Bitget `close-positions` 平掉反向仓位。
3. 确认反向仓位消失。
4. 再按新方向开仓。
5. 如果仍检测到反向仓位，拒绝开新仓。

同向仓位处理：

- 已有同方向仓位时，跳过开仓，避免重复加仓。

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

`--preflight` 会查询 Bitget 合约规格，检查最小下单量、价格精度和数量精度。

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

## 策略决策

实盘复用图表 snapshot，默认策略是：

```yaml
LIVE_TRADING_STRATEGY: stc_extreme_contrarian
LIVE_TRADING_USE_CLOSED_BAR: true
LIVE_TRADING_HTF_HULL_FILTER_ENABLED: true
LIVE_TRADING_HTF_HULL_DURATION_SECONDS: 3600
```

默认使用上一根已收完 K 线，减少未收线重绘。

多单观察：

- 同一根 K 线出现 `Buy` 或 `买`。
- `STC < 25`。
- STC 为绿色。
- 红色 Hull 带整体在开仓 K 线 low 下方。
- 1h Hull / STC 过滤允许顺势开多。

空单观察：

- 同一根 K 线出现 `Sell` 或 `卖`。
- `STC > 75`。
- STC 为红色。
- 绿色 Hull 带整体在开仓 K 线 high 上方。
- 1h Hull / STC 过滤允许顺势开空。

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
2. 同步 Bitget 实际持仓到本地账本。
3. 检查本地/交易所同向仓位，有同向则跳过。
4. 检查反向仓位，有反向则先平仓并确认消失。
5. 检查合约账户余额，不足则按配置从现货划转。
6. 构造市价开仓单。
7. 调用 Bitget `place-order`。
8. 写订单日志、状态文件和邮件。

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

真实模式会定期用 Bitget 持仓同步本地账本：

- Bitget 有仓位、本地没有：新增 exchange 来源 open 记录。
- 本地有 open、Bitget 没仓位：标记 closed。
- 双方都有：更新 size、available、unrealizedPL、marginSize。

## API 权限

API Key 至少需要：

- 合约订单读写。
- 合约持仓读写。
- 钱包划转读写，如果启用自动现货转合约。
- 现货资产读取/相关权限，用于查询可划转余额。

## Bitget 官方接口

当前封装使用：

- 查询持仓：`GET /api/v2/mix/position/all-position`
- 查询合约账户：`GET /api/v2/mix/account/accounts`
- 查询现货余额：`GET /api/v2/spot/account/assets`
- 现货转合约：`POST /api/v2/spot/wallet/transfer`
- 预检查设置逐仓：`POST /api/v2/mix/account/set-margin-mode`
- 预检查设置杠杆：`POST /api/v2/mix/account/set-leverage`
- 下单：`POST /api/v2/mix/order/place-order`
- 反向仓位平仓：`POST /api/v2/mix/order/close-positions`

官方文档：

- https://www.bitget.com/api-doc/contract/account/Get-Account-List
- https://www.bitget.com/api-doc/contract/account/Change-Margin-Mode
- https://www.bitget.com/api-doc/contract/account/Change-Leverage
- https://www.bitget.com/api-doc/contract/trade/Place-Order
- https://www.bitget.com/api-doc/contract/trade/Flash-Close-Position
- https://www.bitget.com/api-doc/spot/account/Get-Account-Assets
- https://www.bitget.com/api-doc/spot/account/Wallet-Transfer

## 真实运行前检查清单

1. Bitget 后台已切到 `USDT-FUTURES` 双向持仓。
2. API Key 权限完整。
3. `.env` 已填写 API Key / Secret / Passphrase。
4. `--profile live_5u --show-config` 检查参数正确。
5. `--profile live_5u --preflight` 返回 ok。
6. 先跑过 `email` 和 `dry_run_5u`。
7. 确认合约账户或现货账户有足够 USDT。
