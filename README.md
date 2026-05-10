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
├── signal_callbacks.example.py
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

## Signal / Action 回调

Signal / Action 用来把“行情或指标满足条件”转换成标准事件，然后执行动作。当前版本已经摒弃 JSON 规则文件，改为 Python 回调方式：你写一个函数判断条件，满足时返回 `ctx.signal(...)`，后端负责去重、冷却和动作执行。

当前安全边界很清楚：`log` 和 `noop` 会真实执行；`feishu`、`open_position`、`close_position`、`trade` 只是占位动作，会返回 `action_not_implemented`，不会真的发消息或下单。

处理链路：

```text
Binance 行情 -> 后端快照 -> 后端指标计算 -> Python 回调 -> SignalEvent -> ActionExecutor
```

### 1. 启用回调

默认没有 `signal_callbacks.py` 时，回调系统不会触发任何事件。复制示例文件：

```bash
cp signal_callbacks.example.py signal_callbacks.py
```

然后重启服务：

```bash
./myvenv/bin/python web_tq_chart.py
```

启动后检查是否加载成功：

```bash
curl 'http://127.0.0.1:8050/api/config'
```

关注返回里的 `signals`：

```json
{
  "signals": {
    "enabled": true,
    "callback_count": 2,
    "callbacks": [
      {
        "id": "duo_kong_buy_marker_log",
        "name": "多空线 多 信号日志",
        "enabled": true
      }
    ]
  }
}
```

部署时也可以用环境变量指定回调文件路径：

```env
SIGNAL_CALLBACKS_FILE=/app/signal_callbacks.py
```

加载优先级：

```text
SIGNAL_CALLBACKS_FILE > ./signal_callbacks.py
```

### 2. 回调文件结构

`signal_callbacks.py` 必须提供 `register_callbacks(registry)`。所有策略函数都在这里注册：

```python
from __future__ import annotations

from typing import Any


def register_callbacks(registry: Any) -> None:
    registry.on_snapshot(
        id="duo_kong_buy_marker_log",
        name="多空线 多 信号日志",
        callback=duo_kong_buy_marker_log,
        actions=[{"type": "log", "level": "info"}],
        once_per_bar=True,
        cooldown_seconds=0,
    )


def duo_kong_buy_marker_log(ctx: Any) -> Any:
    marker = ctx.latest_marker("duo_kong_line", "duo_kong_line", "多", lookback_bars=1)
    if marker is None:
        return None
    return ctx.signal(
        condition_type="duo_kong_marker",
        bar_time=int(marker["time"]),
        payload={
            "side": "buy",
            "indicator_id": "duo_kong_line",
            "series_id": "duo_kong_line",
            "marker": marker,
        },
    )
```

`registry.on_snapshot(...)` 参数：

- `id`：唯一 ID。建议只用英文、数字和下划线，例如 `stc_cross_up_25_log`。
- `name`：展示名称，会进入日志和 `/api/snapshot` 的 `signals`。
- `callback`：条件判断函数。
- `actions`：触发后执行的动作列表。当前建议先只用 `log`。
- `enabled`：是否启用，默认 `True`。
- `once_per_bar`：同一根 K 同一个回调只触发一次，默认 `True`。
- `cooldown_seconds`：冷却秒数，默认 `0`。

### 3. 如何新增一个回调

新增回调通常做三件事：

1. 写一个判断函数。
2. 满足条件时返回 `ctx.signal(...)`。
3. 在 `register_callbacks(registry)` 里注册它。

例子：STC 从下方上穿 25 时打印日志：

```python
def register_callbacks(registry: Any) -> None:
    registry.on_snapshot(
        id="stc_cross_up_25_log",
        name="STC 上穿 25 日志",
        callback=stc_cross_up_25_log,
        actions=[{"type": "log", "level": "info"}],
        once_per_bar=True,
    )


def stc_cross_up_25_log(ctx: Any) -> Any:
    points = ctx.series_points("stc", "stc")
    if len(points) < 2:
        return None

    previous = float(points[-2]["value"])
    current = float(points[-1]["value"])
    if not (previous <= 25 < current):
        return None

    return ctx.signal(
        condition_type="stc_cross_up",
        bar_time=int(points[-1]["time"]),
        payload={
            "indicator_id": "stc",
            "series_id": "stc",
            "previous": previous,
            "current": current,
            "threshold": 25,
        },
    )
```

同一个文件可以注册多个回调。每个回调的 `id` 必须不同。

### 4. 多空线买卖点日志示例

示例文件 `signal_callbacks.example.py` 已经包含多空线 `多` / `空` marker 的日志回调。启用后，当多空线在当前 K 线上出现 `多` marker，会打印买点日志；出现 `空` marker，会打印卖点日志。

买点：

```python
def duo_kong_buy_marker_log(ctx: Any) -> Any:
    marker = ctx.latest_marker("duo_kong_line", "duo_kong_line", "多", lookback_bars=1)
    if marker is None:
        return None
    return ctx.signal(
        condition_type="duo_kong_marker",
        bar_time=int(marker["time"]),
        payload={"side": "buy", "marker": marker},
    )
```

卖点：

```python
def duo_kong_sell_marker_log(ctx: Any) -> Any:
    marker = ctx.latest_marker("duo_kong_line", "duo_kong_line", "空", lookback_bars=1)
    if marker is None:
        return None
    return ctx.signal(
        condition_type="duo_kong_marker",
        bar_time=int(marker["time"]),
        payload={"side": "sell", "marker": marker},
    )
```

### 5. `ctx` 可以读取什么

回调函数收到的 `ctx` 是后端快照上下文，常用属性和方法如下：

- `ctx.symbol`：当前合约，例如 `BTCUSDT`。
- `ctx.provider`：当前数据源，例如 `binance`。
- `ctx.duration_seconds`：当前周期秒数，例如 `60`。
- `ctx.last_price`：当前最新收盘价或最新价。
- `ctx.candles()`：当前 K 线列表。
- `ctx.last_bar_time()`：最后一根 K 线时间戳。
- `ctx.display_time(bar_time)`：把时间戳转成后端展示时间。
- `ctx.indicator_series(indicator_id, series_id)`：读取某个指标序列对象。
- `ctx.series_points(indicator_id, series_id)`：读取某个指标序列的 `data` 点。
- `ctx.latest_marker(indicator_id, series_id, text, lookback_bars=1)`：找最近 K 线上的 marker。
- `ctx.signal(...)`：生成标准 `SignalEvent`。

读取 K 线：

```python
candles = ctx.candles()
last = candles[-1]
open_price = float(last["open"])
close_price = float(last["close"])
```

读取 STC：

```python
points = ctx.series_points("stc", "stc")
latest_stc = float(points[-1]["value"])
```

读取 MACD：

```python
diff = ctx.series_points("macd", "macd_diff")
dea = ctx.series_points("macd", "macd_dea")
hist = ctx.series_points("macd", "macd_hist")
```

读取 ATR Bands：

```python
upper = ctx.series_points("atr_bands", "atr_upper")
middle = ctx.series_points("atr_bands", "atr_middle")
lower = ctx.series_points("atr_bands", "atr_lower")
```

### 6. `ctx.signal(...)` 怎么写

`ctx.signal(...)` 是回调触发时返回的事件：

```python
return ctx.signal(
    condition_type="my_condition",
    bar_time=ctx.last_bar_time(),
    price=ctx.last_price,
    payload={"reason": "something happened"},
)
```

常用参数：

- `condition_type`：条件类型名称，自己定义，建议稳定不要频繁改。
- `bar_time`：触发事件对应的 K 线时间。不传时默认最后一根 K。
- `price`：触发时价格。不传时默认 `ctx.last_price`。
- `display_time`：展示时间。不传时后端自动取。
- `payload`：你想记录的细节，例如指标值、方向、阈值。
- `actions`：临时覆盖这个事件的动作；一般不需要传，优先用注册时的 `actions`。
- `id_suffix`：同一个回调同一根 K 需要触发多个事件时，用它区分事件 ID。

返回多个事件也可以：

```python
return [
    ctx.signal(condition_type="event_a", id_suffix="a"),
    ctx.signal(condition_type="event_b", id_suffix="b"),
]
```

### 7. 动作类型

当前真实执行的安全动作：

```python
actions=[{"type": "log", "level": "info"}]
```

`log` 会把完整 `SignalEvent` 输出到后端日志，日志里会出现 `signal_event ...`。

```python
actions=[{"type": "noop"}]
```

`noop` 什么都不做，只返回动作执行结果。

预留但不会真实执行的动作：

```python
actions=[{"type": "feishu"}]
actions=[{"type": "open_position"}]
actions=[{"type": "close_position"}]
actions=[{"type": "trade"}]
```

这些动作现在都会返回 `action_not_implemented`。接真实飞书或交易前，需要再补账户配置、权限开关、仓位限制、风控和审计日志。

### 8. 查看触发结果

触发结果会出现在 `/api/snapshot` 返回值的 `signals` 字段里：

```bash
curl 'http://127.0.0.1:8050/api/snapshot?provider=binance&symbol=BTCUSDT&duration_seconds=60&bar_mode=time&data_length=200&indicators=duo_kong_line'
```

返回示意：

```json
{
  "signals": [
    {
      "id": "BTCUSDT:60:duo_kong_buy_marker_log:1778395200",
      "rule_id": "duo_kong_buy_marker_log",
      "rule_name": "多空线 多 信号日志",
      "symbol": "BTCUSDT",
      "provider": "binance",
      "duration_seconds": 60,
      "bar_time": 1778395200,
      "display_time": "2026-05-10 16:00:00",
      "price": 80650.1,
      "condition_type": "duo_kong_marker",
      "payload": {
        "side": "buy",
        "indicator_id": "duo_kong_line",
        "series_id": "duo_kong_line"
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

字段里暂时仍叫 `rule_id` / `rule_name`，这是为了兼容已有事件格式；在回调模式下它们对应的是 `callback id` / `callback name`。

### 9. 本地测试回调文件

可以不启动浏览器，直接检查回调文件能否加载：

```bash
SIGNAL_CALLBACKS_FILE=signal_callbacks.py ./myvenv/bin/python - <<'PY'
from pathlib import Path
from tq_app.signals import SignalEngine

engine = SignalEngine(Path(".").resolve())
print(engine.describe())
PY
```

如果 `enabled` 是 `true` 且 `callback_count` 大于 0，说明文件已加载。

如果回调函数写错，后端日志会输出：

```text
signal callback <callback_id> failed
```

如果回调文件加载失败，后端日志会输出：

```text
signal callbacks load failed
```

### 10. 常见问题

`/api/config` 里 `signals.enabled` 是 `false`：

确认项目根目录存在 `signal_callbacks.py`，或者 `SIGNAL_CALLBACKS_FILE` 指向的路径正确。

回调没有触发：

确认前端或请求里启用了对应指标。例如多空线回调需要 snapshot 里有 `duo_kong_line` 指标；STC 回调需要有 `stc` 指标。

同一根 K 只触发了一次：

这是 `once_per_bar=True` 的默认行为。如果你明确需要同一根 K 多次触发，可以设置 `once_per_bar=False`，或者在 `ctx.signal(..., id_suffix="xxx")` 里给不同事件不同后缀。

想临时关闭某个回调：

```python
registry.on_snapshot(
    id="my_callback",
    name="临时关闭示例",
    callback=my_callback,
    enabled=False,
)
```

修改 `signal_callbacks.py` 后没生效：

回调文件在服务启动时加载，修改后需要重启 `web_tq_chart.py`。

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
