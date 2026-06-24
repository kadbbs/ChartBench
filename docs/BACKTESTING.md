# 回测模块

回测模块用于按 K 线级别复现实盘信号判断、开平仓撮合、手续费、风控出场和报告生成。它不需要 Bitget API Key，只使用公共行情接口和本地 K 线缓存。

## 入口

单次回测：

```bash
./myvenv/bin/python run_backtest.py --profile btc_5m_range_cached
```

参数矩阵回测：

```bash
./myvenv/bin/python run_backtest_matrix.py --matrix btc_risk_matrix
```

## 配置文件

单次回测配置在：

```text
config/backtests/
```

当前内置 profile：

```text
latest_month
btc_5m_range
btc_5m_range_cached
btc_5m_range_cached_legacy
sol_5m_range_cached
sol_5m_range_cached_legacy
```

查看可用配置：

```bash
./myvenv/bin/python run_backtest.py --list-profiles
```

参数矩阵配置在：

```text
config/backtest_matrices/
```

当前内置矩阵：

```text
btc_risk_matrix
sol_risk_matrix
```

## 单次回测

最近一段数据：

```bash
./myvenv/bin/python run_backtest.py --profile latest_month
```

BTC 长区间缓存回测：

```bash
./myvenv/bin/python run_backtest.py --profile btc_5m_range_cached
```

SOL 长区间缓存回测：

```bash
./myvenv/bin/python run_backtest.py --profile sol_5m_range_cached
```

临时覆盖时间：

```bash
./myvenv/bin/python run_backtest.py \
  --profile btc_5m_range_cached \
  --start-time "2026-06-01 00:00:00" \
  --end-time "2026-06-20 00:00:00"
```

未带时区的时间按北京时间解析。

## 本地 K 线缓存

启用缓存后，K 线会写入：

```text
data_cache/backtest_klines/
```

缓存文件按产品、合约、周期和 K 线类型分开：

```text
USDT-FUTURES_BTCUSDT_300s_MARKET.csv
USDT-FUTURES_SOLUSDT_300s_MARKET.csv
```

缓存行为：

- 第一次运行：从 Bitget 公共接口拉取并写入缓存。
- 后续运行：如果缓存完整覆盖请求区间，直接读取本地。
- 如果只缺左侧、右侧或中间缺口，只增量补齐缺失区间。
- `--no-cache` 可以临时绕过缓存强制在线请求。

命令：

```bash
./myvenv/bin/python run_backtest.py --profile btc_5m_range_cached --no-cache
```

## 撮合模型

默认撮合规则：

1. 复用当前实盘信号策略和高周期过滤。
2. 信号在目标 K 线收完后确认。
3. 下一根 K 线 `open` 开仓。
4. 持仓期间先检查回测风控出场。
5. 如果本根 K 线被风控平仓，本根不再重新开仓。
6. 未触发风控时，出现反向实盘信号则平旧仓并开新仓。
7. 回测结束时仍未平仓的最后一笔交易会被丢弃，不纳入统计。

默认风控出场：

```text
启动失败止损：
开仓后第 24 根 5m K 检查，最大浮盈 < 300 且当前点数 < -150，则平仓。

灾难硬止损：
任意 K 线内最大浮亏达到 -1800 点，则平仓。

保本保护：
最大浮盈达到 +800 点后，保护线抬到 +100 点。

移动保护：
最大浮盈达到 +2000 点后保护 40% 最大浮盈。
最大浮盈达到 +4000 点后保护 50% 最大浮盈。
最大浮盈达到 +8000 点后保护 60% 最大浮盈。
```

## Legacy 模型

如果要复现早期“只按反向信号平仓/反手”的模型，使用 legacy profile：

```bash
./myvenv/bin/python run_backtest.py --profile btc_5m_range_cached_legacy
./myvenv/bin/python run_backtest.py --profile sol_5m_range_cached_legacy
```

或者临时关闭风控出场：

```bash
./myvenv/bin/python run_backtest.py --profile btc_5m_range_cached --no-risk-exits
```

对应配置：

```yaml
risk_exits_enabled: false
```

## 仓位和手续费

当前回测仓位固定：

```text
保证金：1000U
杠杆：10x
名义价值：10000U
```

默认手续费：

```yaml
fee_rate: 0.00023
```

换算：

```text
1000U 保证金 * 10x * 0.00023 * 2 = 4.6U
4.6U / 1000U = 0.46%
```

也就是 10x 下，一次完整开平仓手续费约等于保证金的 `0.46%`。

## 参数矩阵

矩阵回测用于批量扫描风控参数，也可以扫描当前策略实际用到的指标参数。

先检查组合数：

```bash
./myvenv/bin/python run_backtest_matrix.py --matrix btc_risk_matrix --dry-run
./myvenv/bin/python run_backtest_matrix.py --matrix sol_risk_matrix --dry-run
./myvenv/bin/python run_backtest_matrix.py --matrix btc_indicator_param_matrix_v1 --dry-run
```

正式运行：

```bash
./myvenv/bin/python run_backtest_matrix.py --matrix btc_risk_matrix
./myvenv/bin/python run_backtest_matrix.py --matrix btc_indicator_param_matrix_v1
```

输出：

```text
backtest_outputs/matrix/btc_risk_matrix/
├── summary.csv
├── summary.json
└── runs/
```

`summary.csv` 会按评分排序，包含：

- 净收益
- 收益率
- 最大回撤
- 胜率
- 盈利因子
- 总点数
- 扣费后点数
- 手续费
- 参数组合

指标参数矩阵写法示例：

```yaml
indicator.stc.length: 60,80
indicator.merged_dkx_hull_ut.hull_length: 45,55
indicator.merged_dkx_hull_ut.ut_sensitivity: 1.5,2.0
```

当前实盘入场逻辑主要使用 `merged_dkx_hull_ut` 的 DKX/UT 信号、Hull 趋势带，以及 `stc` 的数值和颜色。`macd` 目前只用于展示/通知，不参与入场判断，因此首版指标矩阵没有扫描 MACD。

建议：

- 先粗扫 20-100 组。
- 找到稳定区域后再局部细扫。
- 不要一次性堆太多参数，否则组合数会指数级膨胀。

## 实盘参数更新建议

不建议每天按最新结果更新实盘参数，容易过拟合。

推荐流程：

```text
主训练窗口：最近 6-12 个月
验证窗口：最近 30-60 天
压力检查：大涨、大跌、震荡段分别单独看
更新频率：常规每 2 周评估一次；剧烈行情每周评估一次
上线条件：连续 2 次评估都稳定，再考虑更新实盘参数
```

## 输出文件

单次回测输出目录由 profile 的 `output_dir` 控制。

```text
report.json      # 完整结构化报告
report.md        # 英文摘要
report_zh.md     # 中文摘要
trades.csv       # 逐笔交易
candles.json     # K 线和开平仓 markers
```

优先看：

```text
report_zh.md
trades.csv
summary.csv      # 矩阵回测
```

## 常见问题

如果长区间第一次运行慢：

- 确认是否启用了缓存 profile。
- 第一次新合约需要拉历史 K 线，之后会快很多。
- 如果缓存只有部分区间，程序会自动补缺口。

如果你要回测其他合约：

```bash
./myvenv/bin/python run_backtest.py --profile btc_5m_range_cached --symbol ETHUSDT
```

如果要长期使用，建议复制一个新的 profile，修改 `symbol` 和 `output_dir`。
