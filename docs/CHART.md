# 图表模块

图表模块用于查看行情 K 线、成交量和指标信号。当前图表默认使用天勤量化 TqSdk 数据；也可以临时切到 Binance 或 Bitget。天勤目前只用于图表，实盘和回测只支持 Binance / Bitget。

## 入口

```bash
./myvenv/bin/python web_tq_chart.py
```

默认监听：

```text
http://0.0.0.0:8050
```

如果希望启动后自动打开浏览器：

```bash
./myvenv/bin/python web_tq_chart.py --open-browser
```

## 常用命令

指定天勤合约和周期：

```bash
./myvenv/bin/python web_tq_chart.py --symbol SHFE.cu2607 --duration 300 --length 800
```

临时切到 Binance / Bitget 图表：

```bash
./myvenv/bin/python web_tq_chart.py --provider binance --symbol BTCUSDT --duration 300
./myvenv/bin/python web_tq_chart.py --provider bitget --symbol BTCUSDT --duration 300
```

指定监听地址和端口：

```bash
./myvenv/bin/python web_tq_chart.py --host 0.0.0.0 --port 8050
```

## 配置

图表默认配置放在 `config/defaults.yaml`：

```yaml
TQ_CHART_DEFAULT_PROVIDER: tianqin
TQ_CHART_DEFAULT_SYMBOL: SHFE.cu2607
TQ_DEFAULT_PROVIDER: binance
TQ_DEFAULT_SYMBOL: BTCUSDT
TQ_DEFAULT_DURATION_SECONDS: 300
TQ_DEFAULT_DATA_LENGTH: 800
TQ_DEFAULT_REFRESH_MS: 200
TQ_DEFAULT_HOST: 0.0.0.0
TQ_DEFAULT_PORT: 8050
TQ_DEFAULT_BAR_MODE: time
TIANQIN_SYMBOLS: SHFE.cu2607,DCE.m2609,CZCE.SR601,SHFE.rb2601
```

天勤账号密码放 `.env`：

```env
TIANQIN_USERNAME=
TIANQIN_PASSWORD=
```

命令行参数优先级最高。例如临时看沪铜：

```bash
./myvenv/bin/python web_tq_chart.py --symbol SHFE.cu2607
```

## 支持的图表模式

当前入口参数保留以下模式：

```text
time   # 时间 K 线，当前主要使用模式
tick   # Tick 图预留
range  # Range Bar 预留
renko  # Renko 预留
```

天勤当前图表链路建议使用 `time`。

## Web API

图表服务由 Flask 提供，主要接口在 `tq_app/web.py`：

```text
GET /                 # 图表页面
GET /api/config       # 当前数据源、合约、周期、指标元信息
GET /api/snapshot     # 当前 K 线、成交量、指标序列
GET /api/stream       # SSE 推送 snapshot 更新
GET /api/health       # 行情源健康状态
```

示例：

```bash
curl "http://127.0.0.1:8050/api/health"
curl "http://127.0.0.1:8050/api/snapshot?symbol=BTCUSDT&duration_seconds=300"
```

## 行情链路

图表模块的核心流程：

1. `web_tq_chart.py` 加载 `config/defaults.yaml` 和 `.env`。
2. 创建 `MarketDataService`。
3. `MarketDataService` 按 provider 创建 Tianqin / Binance / Bitget 数据源。
4. 天勤数据源通过 `TqApi.get_kline_serial()` 订阅 K 线，并用 `wait_update()` 驱动实时更新；Binance / Bitget 使用公共 REST 行情轮询。
5. 服务计算指标。
6. Flask API 返回前端可直接渲染的 snapshot。

关键文件：

```text
web_tq_chart.py
tq_app/web.py
tq_app/service.py
tq_app/data_sources/tianqin.py
tq_app/indicators/
custom_indicators.py
static/app.js
static/styles.css
templates/index.html
```

## 指标

默认图表会加载指标注册表里的默认指标。当前策略重点使用：

```text
merged_dkx_hull_ut
stc
macd
```

`merged_dkx_hull_ut` 来自 `custom_indicators.py`，会生成：

- UT / DKX 买卖 marker。
- Hull 红带/绿带。
- 策略用于判断 K 线和 Hull 带相对位置。

`stc` 和 `macd` 在 `tq_app/indicators/builtin.py`。

## 合约切换

天勤合约代码使用 TqSdk 格式：

```text
SHFE.cu2607
DCE.m2609
CZCE.SR601
SHFE.rb2601
```

命令行示例：

```bash
./myvenv/bin/python web_tq_chart.py --symbol DCE.m2609
```

## 故障排查

如果页面打不开：

- 确认进程还在运行。
- 确认端口没有被占用。
- 本机访问优先试 `http://127.0.0.1:8050`。
- 服务器访问确认安全组或防火墙放行端口。

如果图表没有数据：

- 看 `/api/health`。
- 确认已安装 `tqsdk`。
- 确认 `.env` 已配置 `TIANQIN_USERNAME` / `TIANQIN_PASSWORD`。
- 确认合约代码是 TqSdk 支持的合约代码。
- 确认 `TQ_CHART_DEFAULT_PROVIDER` 是 `tianqin`。
- 网络异常时重启图表进程。
