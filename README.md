# Binance Chart Workbench

当前分支只保留 Binance USD-M 公共行情链路。

默认目标：

- 数据源：`binance`
- 默认合约：`BTCUSDT`
- 默认周期：`1m`
- 实时方式：浏览器只连接本机后端；后端通过 Binance Futures REST 初始化历史 K 线，并通过 Binance Futures WebSocket 更新当前 K 线

## 当前能力

- 支持 Binance 合约目录和合约切换
- 支持 Binance 时间 K 线周期切换
- 支持主图、成交量、多副图 pane
- 支持十字光标联动和时间标签映射
- 指标只保留 `ATR Bands`、`MACD`、`STC` 和 `多空线`
- 支持后端快照接口补充历史 K 线、指标和侧栏信息
- 支持可配置 Signal / Action 工作流，当前安全动作只实现日志与 no-op

## 实时链路

- 历史 K 线、合约目录与指标：
  后端通过 Binance Futures REST API 拉取并计算
- 当前 K 线：
  后端订阅 Binance Futures 官方 `fstream.binance.com/market` 的 `aggTrade` 和 `kline` WebSocket；`kline` 负责创建官方 K 线，`aggTrade` 只推动已存在当前 K 线的最新价/高低点
- 前端：
  只连接本机后端 `/api/stream`

浏览器不直连 Binance。

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
│   ├── actions/
│   ├── signals/
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

WebSocket 默认只使用 Binance 官方 USD-M Futures market stream 地址：

```env
BINANCE_WS_BASE=wss://fstream.binance.com
```

后端会自动拼成 `wss://fstream.binance.com/market/stream?...`。不建议把 `BINANCE_WS_BASE` 指向非官方 Futures 行情域名；后端会在首根实时 K 线上校验 REST 与 WS 的开盘价，避免历史 K 和实时 K 接到不同数据源。

## 启动

```bash
./myvenv/bin/python web_tq_chart.py
```

默认地址：

```text
http://0.0.0.0:8050
```

部署到服务器后请使用服务器公网 IP 或域名访问，例如 `http://<server-ip>:8050`。

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

## Signal / Action 工作流

Signal / Action 用来把“指标或行情条件”转成标准事件，然后执行动作。当前版本只做安全框架：可以打印日志、返回 no-op，飞书和交易动作不会真实执行。

处理链路：

```text
行情快照 -> 指标计算 -> 条件判断 -> SignalEvent -> ActionExecutor
```

### 启用方式

默认不启用任何规则。先复制示例文件：

```bash
cp signal_rules.example.json signal_rules.json
```

然后把想启用的规则改成：

```json
"enabled": true
```

重启服务后生效：

```bash
./myvenv/bin/python web_tq_chart.py
```

规则文件支持通过环境变量覆盖：

```env
SIGNAL_RULES_FILE=/app/signal_rules.json
SIGNAL_RULES_JSON={"rules":[]}
```

优先级：

```text
SIGNAL_RULES_JSON > SIGNAL_RULES_FILE > ./signal_rules.json
```

### 规则格式

一个规则由 `id`、`condition` 和 `actions` 组成：

```json
{
  "id": "stc_turn_up_below_25",
  "name": "STC turns up below 25",
  "enabled": true,
  "once_per_bar": true,
  "cooldown_seconds": 0,
  "condition": {
    "type": "turns_up_below",
    "indicator_id": "stc",
    "series_id": "stc",
    "threshold": 25
  },
  "actions": [
    {
      "type": "log",
      "level": "info"
    }
  ]
}
```

字段说明：

- `id`：规则唯一 ID，会参与去重。
- `name`：展示名称。
- `enabled`：是否启用。
- `once_per_bar`：同一根 K 线同一个规则只触发一次。
- `cooldown_seconds`：触发后的冷却秒数。
- `condition`：条件配置。
- `actions`：触发后执行的动作列表。

### 条件类型

当前已实现的条件类型：

- `crosses_above`
- `crosses_below`
- `turns_up_below`
- `turns_down_above`

`condition` 字段说明：

- `type`：条件类型。
- `indicator_id`：指标 ID，例如 `stc`、`macd`、`atr_bands`。
- `series_id`：指标序列 ID，例如 STC 的 `stc`、MACD 的 `macd_diff`。
- `threshold`：阈值。

示例：STC 上穿 25：

```json
{
  "type": "crosses_above",
  "indicator_id": "stc",
  "series_id": "stc",
  "threshold": 25
}
```

示例：STC 在 25 下方拐头向上：

```json
{
  "type": "turns_up_below",
  "indicator_id": "stc",
  "series_id": "stc",
  "threshold": 25
}
```

### 动作类型

当前已实现的安全动作：

- `log`
- `noop`

`log` 会把 SignalEvent 输出到后端日志：

```json
{
  "type": "log",
  "level": "info"
}
```

`noop` 什么都不做，只返回动作执行结果：

```json
{
  "type": "noop"
}
```

`feishu`、`open_position`、`close_position` 和 `trade` 现在只会返回 `action_not_implemented`，不会真实发消息或交易。

### 查看触发结果

触发结果会出现在 `/api/snapshot` 返回值的 `signals` 字段里：

```bash
curl 'http://127.0.0.1:8050/api/snapshot?provider=binance&symbol=BTCUSDT&duration_seconds=60&bar_mode=time&data_length=200&indicators=stc'
```

返回示意：

```json
{
  "signals": [
    {
      "id": "BTCUSDT:60:stc_turn_up_below_25:1778395200",
      "rule_id": "stc_turn_up_below_25",
      "rule_name": "STC turns up below 25",
      "symbol": "BTCUSDT",
      "provider": "binance",
      "duration_seconds": 60,
      "bar_time": 1778395200,
      "display_time": "2026-05-10 16:00:00",
      "price": 80650.1,
      "condition_type": "turns_up_below",
      "payload": {
        "indicator_id": "stc",
        "series_id": "stc",
        "previous": 18.2,
        "current": 22.4,
        "threshold": 25
      },
      "actions": [
        {
          "type": "log",
          "ok": true,
          "dry_run": true,
          "level": "info"
        }
      ]
    }
  ]
}
```

### 查看配置是否加载

`/api/config` 会返回 `signals` 摘要：

```bash
curl 'http://127.0.0.1:8050/api/config'
```

关注：

```json
{
  "signals": {
    "enabled": true,
    "rule_count": 2
  }
}
```

### 当前安全边界

- 规则只在后端快照生成时评估。
- 默认不启用任何规则。
- 同一规则同一根 K 默认只触发一次。
- `log` 和 `noop` 是唯一真实执行的动作。
- 飞书和交易动作只是保留接口，不会发消息、不会下单。
- 后续接真实交易前，需要增加账户配置、权限开关、仓位限制、风控和审计日志。

## API

### `GET /api/config`

返回当前 provider、默认 symbol、合约列表、周期选项、图表类型选项、指标元信息和 provider 提示信息。

### `GET /api/snapshot`

返回 K 线快照、成交量、指标结果、最新价、最新时间和侧栏合约信息。

### `GET /api/health`

返回后端行情线程状态、最后消息时间和健康检查结果。Docker healthcheck 使用这个接口。

示例：

```text
/api/snapshot?provider=binance&symbol=BTCUSDT&duration_seconds=60&bar_mode=time&data_length=200&indicators=macd,atr_bands,stc
```

## 验证

```bash
node --check static/app.js
./myvenv/bin/python -m compileall web_tq_chart.py tq_app custom_indicators.py
```

## 当前边界

- 当前实时推送只覆盖 `binance + time`
- ATR、MACD、STC 和多空线仍由后端计算，不是纯前端指标引擎
- WebGL 订单流 pane 已消费真实逐笔成交和盘口快照，但还不是完整 DOM 回放引擎

## License

MIT，见 [LICENSE](LICENSE)。
