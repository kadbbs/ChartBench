# Bitget Chart Workbench

当前分支只保留 Bitget USDT-FUTURES 公共行情链路。

默认目标：

- 数据源：`bitget`
- 默认合约：`BTCUSDT`
- 默认周期：`3m`
- 实时方式：浏览器只连接本机后端；后端通过 Bitget REST 初始化历史 K 线，并通过 Bitget WebSocket 更新当前 K 线

## 当前能力

- 支持 Bitget 合约目录和合约切换
- 支持 Bitget 时间 K 线周期切换
- 支持主图、成交量、多副图 pane
- 支持十字光标联动和时间标签映射
- 指标只保留 `ATR Bands`、`MACD`、`STC` 和 `多空线`
- 支持后端快照接口补充历史 K 线、指标和侧栏信息

## 实时链路

- 历史 K 线、合约目录与指标：
  后端通过 Bitget REST API 拉取并计算
- 当前 K 线：
  后端订阅 Bitget 官方公共 WebSocket K 线频道，收到实时 K 线后更新本地缓存
- 前端：
  只连接本机后端 `/api/stream`

浏览器不直连 Bitget。

## 项目结构

```text
.
├── web_tq_chart.py
├── custom_indicators.py
├── static/
├── templates/
├── tq_app/
│   ├── web.py
│   ├── service.py
│   ├── contracts.py
│   ├── data_sources/
│   │   ├── base.py
│   │   ├── bitget.py
│   │   └── registry.py
│   └── indicators/
├── orderflow/
├── spqrc_lab/
└── scripts/
```

## 安装

```bash
python3 -m venv myvenv
source myvenv/bin/activate
pip install -r requirements.txt
```

## 环境变量

默认不需要配置。若需要指定 Bitget REST 或 WebSocket 节点，可在 `.env` 中设置：

```env
BITGET_API_BASE=https://api.bitget.com
BITGET_WS_PUBLIC_URL=wss://ws.bitget.com/v2/ws/public
```

默认产品类型为 `USDT-FUTURES`。如需调整合约目录，可配置：

```env
BITGET_PRODUCT_TYPES=USDT-FUTURES
BITGET_DEFAULT_PRODUCT_TYPE=USDT-FUTURES
```

默认启动参数也可以放在 `.env` 中；命令行参数会临时覆盖这些默认值：

```env
TQ_DEFAULT_PROVIDER=bitget
TQ_DEFAULT_SYMBOL=BTCUSDT
TQ_DEFAULT_DURATION_SECONDS=180
TQ_DEFAULT_DATA_LENGTH=800
TQ_DEFAULT_REFRESH_MS=200
TQ_DEFAULT_BAR_MODE=time
TQ_DEFAULT_RANGE_TICKS=10
TQ_DEFAULT_BRICK_LENGTH=10000
TQ_DEFAULT_HOST=0.0.0.0
TQ_DEFAULT_PORT=8050
```

K 线默认使用 Bitget 官方 `MARKET` 成交价口径，对应 App 中普通成交价 K 线。若需要对齐标记价或指数价 K 线，可配置：

```env
BITGET_KLINE_TYPE=MARKET
# 可选：MARK / INDEX
```

如果需要展示账户摘要，可配置只读 API 信息：

```env
BITGET_API_KEY=
BITGET_API_SECRET=
BITGET_API_PASSPHRASE=
```

如果需要通过 Resend 发送邮件提醒，可配置：

```env
RESEND_API_KEY=
RESEND_FROM_EMAIL=onboarding@resend.dev
```

实盘执行模块默认只 dry-run，不会真实下单。确认信号、日志和邮件都正常后，才显式打开：

```env
LIVE_TRADING_ENABLED=false
LIVE_TRADING_DRY_RUN=true
LIVE_TRADING_LOG_ONLY=true
LIVE_TRADING_ORDER_SIZE=
LIVE_TRADING_MARGIN_MODE=crossed
LIVE_TRADING_MARGIN_COIN=USDT
LIVE_TRADING_PRODUCT_TYPE=USDT-FUTURES
LIVE_TRADING_POSITION_MODE=one_way_mode
LIVE_TRADING_SIGNAL_MODE=any
LIVE_TRADING_STRATEGY=stc_extreme_contrarian
LIVE_TRADING_USE_CLOSED_BAR=true
LIVE_TRADING_EMAIL_ENABLED=true
LIVE_TRADING_EMAIL_TO=2924428540@qq.com
```

## 启动

```bash
./myvenv/bin/python web_tq_chart.py
```

邮件发送函数可直接调用：

```python
from tq_app.notifications import send_resend_email

send_resend_email(
    to="2924428540@qq.com",
    subject="Hello World",
    html="<p>Congrats on sending your <strong>first email</strong>!</p>",
)
```

执行一次实盘决策：

```bash
./myvenv/bin/python run_live_trading.py --symbol BTCUSDT --duration 180
```

常驻连续执行实盘决策：

```bash
./myvenv/bin/python run_live_trading.py --symbol BTCUSDT --duration 180 --continuous
```

常驻模式会复用同一个 Bitget 行情服务，等待行情版本更新后重新计算信号。默认使用上一根已收完 K 线，并按目标 K 线时间去重，避免同一根 K 线在 WebSocket 多次更新时反复执行。
常驻进程启动后会发送一封启动邮件，便于确认服务已经上线。

部署前可先跑 Bitget 私有接口和合约配置预检查：

```bash
./myvenv/bin/python run_live_trading.py --preflight
```

模块会读取合并指标、STC、MACD 等已计算信息；默认使用上一根已收完的 K 线信号。当前默认策略是观察模式：

- 空单观察信号：`merged_dkx_hull_ut` 同一根 K 线出现 `Sell` 或 `卖` 任一信号，且 STC 在 `75` 上方并为红色。
- 多单观察信号：`merged_dkx_hull_ut` 同一根 K 线出现 `Buy` 或 `买` 任一信号，且 STC 在 `25` 下方并为绿色。

`LIVE_TRADING_LOG_ONLY=true` 时只打印日志、写入 `logs/live_trading_orders.jsonl` 并发送邮件，不构造真实下单请求。

真实下单前会查询 Bitget 当前持仓；若同一合约已有同方向仓位，模块会跳过开仓、写入订单日志并发送邮件提醒。

默认地址：

```text
http://0.0.0.0:8050
```

部署到服务器后请使用服务器公网 IP 或域名访问，例如 `http://<server-ip>:8050`。

常用参数：

```bash
./myvenv/bin/python web_tq_chart.py \
  --provider bitget \
  --symbol BTCUSDT \
  --duration 180 \
  --length 800 \
  --bar-mode time \
  --host 0.0.0.0 \
  --port 8050
```

`--provider` 仅支持 `bitget`。

## 24x7 运行

容器部署建议带自动重启策略：

```bash
docker build -t tq-chart:latest .
docker run -d \
  --name tq-chart \
  --restart unless-stopped \
  -p 8050:8050 \
  tq-chart:latest
```

健康检查地址：

```text
/api/health
```

## API

### `GET /api/config`

返回当前 provider、默认 symbol、合约列表、周期选项、图表类型选项、指标元信息和 provider 提示信息。

### `GET /api/snapshot`

返回 K 线快照、成交量、指标结果、最新价、最新时间和侧栏合约信息。

### `GET /api/health`

返回后端行情线程状态、最后消息时间和健康检查结果。Docker healthcheck 使用这个接口。

示例：

```text
/api/snapshot?provider=bitget&symbol=BTCUSDT&duration_seconds=180&bar_mode=time&data_length=200&indicators=macd,atr_bands,stc
```

## 验证

```bash
node --check static/app.js
./myvenv/bin/python -m compileall web_tq_chart.py tq_app custom_indicators.py
```

## 当前边界

- 当前实时推送只覆盖 `bitget + time`
- ATR、MACD、STC 和多空线仍由后端计算，不是纯前端指标引擎
- WebGL 订单流 pane 已消费真实逐笔成交和盘口快照，但还不是完整 DOM 回放引擎

## License

MIT，见 [LICENSE](LICENSE)。
