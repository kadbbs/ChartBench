# Binance Chart Workbench

当前分支只保留 Binance USD-M 公共行情链路。

默认目标：

- 数据源：`binance`
- 默认合约：`BTCUSDT`
- 默认周期：`1m`
- 实时方式：前端定时请求后端 `/api/snapshot`，后端通过 Binance Futures REST API 刷新 K 线

## 当前能力

- 支持 Binance 合约目录和合约切换
- 支持 Binance 时间 K 线周期切换
- 支持主图、成交量、多副图 pane
- 支持十字光标联动和时间标签映射
- 指标只保留 `ATR Bands`、`MACD` 和 `多空线`
- 支持通过后端快照接口刷新 K 线、指标和侧栏信息

## 实时链路

- K 线、合约目录与指标：
  后端通过 Binance Futures REST API 拉取并计算
- 前端：
  定时请求本机后端 `/api/snapshot`

当前链路不使用 WebSocket。

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
│   │   ├── binance.py
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

默认不需要配置。若需要指定 Binance Futures REST 节点，可在 `.env` 中设置：

```env
BINANCE_FAPI_BASE=https://fapi.binance.com
```

也可以配置多个备用 REST 节点：

```env
BINANCE_FAPI_BASES=https://fapi.binance.com,https://fapi1.binance.com,https://fapi2.binance.com
```

## 启动

```bash
./myvenv/bin/python web_tq_chart.py
```

默认地址：

```text
http://127.0.0.1:8050
```

常用参数：

```bash
./myvenv/bin/python web_tq_chart.py \
  --provider binance \
  --symbol BTCUSDT \
  --duration 60 \
  --length 800 \
  --bar-mode time \
  --host 0.0.0.0 \
  --port 8050
```

`--provider` 仅支持 `binance`。

## API

### `GET /api/config`

返回当前 provider、默认 symbol、合约列表、周期选项、图表类型选项、指标元信息和 provider 提示信息。

### `GET /api/snapshot`

返回 K 线快照、成交量、指标结果、最新价、最新时间和侧栏合约信息。

示例：

```text
/api/snapshot?provider=binance&symbol=BTCUSDT&duration_seconds=60&bar_mode=time&data_length=200&indicators=macd,atr_bands
```

## 验证

```bash
node --check static/app.js
./myvenv/bin/python -m compileall web_tq_chart.py tq_app custom_indicators.py
```

## 当前边界

- 当前实时推送只覆盖 `binance + time`
- ATR、MACD 和多空线仍由后端计算，不是纯前端指标引擎
- WebGL 订单流 pane 已消费真实逐笔成交和盘口快照，但还不是完整 DOM 回放引擎

## License

MIT，见 [LICENSE](LICENSE)。
