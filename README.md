# Bitget Chart Workbench

这条分支已经转向大重构，README 只描述当前有效的实现，不保留旧的 `tq` 路线说明。

当前默认目标：

- 数据源：`bitget`
- 默认合约：`XAUUSDT`
- 默认周期：`1m`
- 实时方式：浏览器直连 Bitget WebSocket

## 当前状态

- 前端主图和成交量由浏览器直接订阅 Bitget 公共 WebSocket
- 后端负责配置、历史快照、指标计算、合约目录和本地数据源封装
- `bitget + time` 是当前主链路
- `duckdb` 仍保留，适合本地历史回放和后续重构复用
- 旧的 `tq` 在线链路已经从主应用移除

## 界面示例

![界面示例](docs/image.png)

## 当前能力

- 支持 `bitget / duckdb` 数据源
- 支持 Bitget 合约切换
- 支持时间 K 线周期切换
- 支持主图、成交量、多副图 pane
- 支持十字光标联动和时间标签映射
- 支持内置指标和 `custom_indicators.py` 动态加载
- 支持 Bitget 实时推送下的图表增量更新
- 支持独立的 WebGL 订单流矩阵 pane
- 支持后端快照接口补充指标和侧栏信息

## 实时链路

当前实时模式分两层：

- K 线、逐笔成交与盘口：
  浏览器直接连接 `wss://ws.bitget.com/v2/ws/public`
- 指标与侧栏摘要：
  由后端 `/api/snapshot` 计算并返回

这意味着：

- 价格主图不再依赖前端定时轮询
- 切换品种和周期后，前端会重建 WebSocket 订阅
- `Orderflow GL` 会消费 `candle + trade + books15`
- 指标不是每一帧都前端本地计算，仍保留后端参与

## 项目结构

```text
.
├── web_tq_chart.py
├── custom_indicators.py
├── custom_indicators.example.py
├── static/
├── templates/
├── tq_app/
│   ├── web.py
│   ├── service.py
│   ├── contracts.py
│   ├── models.py
│   ├── data_sources/
│   └── indicators/
├── orderflow/
├── spqrc_lab/
├── scripts/
├── data/
└── docs/
```

## 环境要求

- Python 3.11+
- Node 仅用于前端脚本语法检查，可选

## 安装

```bash
python3 -m venv myvenv
source myvenv/bin/activate
pip install -r requirements.txt
```

## 环境变量

项目根目录创建 `.env`。

Bitget 公开行情最少只需要：

```env
BITGET_DEFAULT_PRODUCT_TYPE=USDT-FUTURES
```

如果要显示 Bitget 账户摘要，再补：

```env
BITGET_API_KEY=your_key
BITGET_API_SECRET=your_secret
BITGET_API_PASSPHRASE=your_passphrase
```

如果要启用 DuckDB 本地源，可选：

```env
DUCKDB_TICK_DB_PATH=data/duckdb/ticks.duckdb
DUCKDB_SOURCE_PROVIDER=bitget
```

## 启动

默认启动：

```bash
./myvenv/bin/python web_tq_chart.py
```

默认地址：

```text
http://127.0.0.1:8050
```

远程环境：

```bash
./myvenv/bin/python web_tq_chart.py --host 0.0.0.0 --port 8050
```

自动打开浏览器：

```bash
./myvenv/bin/python web_tq_chart.py --open-browser
```

## 常用启动参数

```bash
./myvenv/bin/python web_tq_chart.py \
  --provider bitget \
  --symbol XAUUSDT \
  --duration 60 \
  --length 800 \
  --bar-mode time \
  --refresh-ms 1000 \
  --host 0.0.0.0 \
  --port 8050
```

参数说明：

- `--provider`：当前主用 `bitget`，也可切到 `duckdb`
- `--symbol`：默认合约，例如 `XAUUSDT`
- `--duration`：时间 K 周期，单位秒
- `--length`：默认拉取根数
- `--bar-mode`：当前 Bitget 主链路只建议使用 `time`
- `--refresh-ms`：后端快照刷新节奏，主要影响指标同步
- `--host`：监听地址
- `--port`：监听端口

## API

### `GET /api/config`

返回：

- 当前 provider
- 默认 symbol
- 合约列表
- 周期选项
- 图表类型选项
- 指标元信息
- provider 提示信息

### `GET /api/snapshot`

返回：

- K 线快照
- 成交量
- 指标结果
- 最新价
- 最新时间
- 侧栏所需的 provider / contract 信息

示例：

```text
/api/snapshot?provider=bitget&symbol=XAUUSDT&duration_seconds=60&bar_mode=time&data_length=200&indicators=macd,atr_bands
```

## 指标

内置指标包括：

- `ATR Bands`
- `MACD`
- `SMA 20`
- `Orderflow GL`
- `5分钟仿订单流`
- `SPQRC 信号`
- `SPQRC 面板`

项目根目录的 [custom_indicators.py](custom_indicators.py) 会在启动时自动加载。

最小示例见 [custom_indicators.example.py](custom_indicators.example.py)。

## DuckDB

`duckdb` 还在仓库里，当前主要作用是：

- 保留本地历史数据能力
- 为后续重构提供离线数据入口
- 继续承接 `tick / range / renko / time` 的本地计算

如果当前目标只看 Bitget 实时行情，这部分可以先忽略。

## 验证

前端脚本语法检查：

```bash
node --check static/app.js
```

Python 编译检查：

```bash
./myvenv/bin/python -m compileall web_tq_chart.py tq_app custom_indicators.py
```

## 当前边界

- 浏览器端实时推送目前只覆盖 `bitget + time`
- WebGL pane 已经开始消费真实逐笔成交和盘口快照，但还没有做到完整逐档校验、回放、撤单分析和全量 DOM 引擎
- 指标仍然依赖后端计算，不是纯前端指标引擎
- README 不再记录旧架构和迁移背景，后续以当前分支代码为准

## License

MIT，见 [LICENSE](LICENSE)。
