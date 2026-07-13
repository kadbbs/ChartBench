# 回测模块

回测模块用于按 K 线级别复现实盘信号判断、开平仓撮合、手续费、风控出场和报告生成。它不需要 Binance / Bitget API Key，只使用公共行情接口和本地 K 线缓存。

## 入口

单次回测：

```bash
./myvenv/bin/python run_backtest.py --profile btc_5m_range_cached
```

参数矩阵回测：

```bash
./myvenv/bin/python run_backtest_matrix.py --matrix btc_risk_matrix
```

回测研究工作台：

```bash
./myvenv/bin/python chartbench.py chart run --backtest-ui
```

打开 `http://127.0.0.1:8050/backtests`。工作台是显式启用能力，普通行情服务
不会创建回测进程。计算任务在独立 spawn 进程中串行执行，不占用 Flask 请求
线程；服务重启后未完成任务会标记为中断，可复制参数重新测试。

## 回测研究工作台

工作台面向长周期参数研究：

- 选择命名 profile 后自动加载品种、周期、时间范围和风险基线。
- 提交前显示目标 K 线数量、本地缓存覆盖范围、组合数和计算类型。
- 风控参数与 Hull/STC 指标参数可以组成笛卡尔积，单次最多 256 组。
- 同一实验只准备一次低周期和高周期行情。
- 相同指标参数的组合共享预计算指标快照和策略信号，修改纯风控参数时只重跑撮合。
- 参数矩阵只保存指标和交易摘要，不为每一组复制完整 `candles.json`。
- 单一候选复测才生成完整报告、交易明细和最多 4000 根的图表预览。

矩阵结果按总收益、60/20/20 研究/验证/测试分段、最大回撤、盈利因子、
年度一致性和最小交易数惩罚计算稳健分。该分数用于寻找连续稳定的参数区域，
不能解释为未来收益预测。结果页同时保留原始指标，用户可以按自己的风险约束
判断候选组合。

UI 产物位于：

```text
backtest_outputs/ui/<run_id>/
├── request.json
├── status.json
├── summary.json
├── summary.csv
├── chart_preview.json       # 单次实验
└── artifacts/               # 单次实验的原有完整报告
```

工作台不会修改 `config/backtests/*.yaml` 或实盘 profile，也不会覆盖 CLI 原有
输出目录。候选组合需要用户显式复测和确认后，才能人工更新实盘配置。

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

这些命名 profile 都显式固定 `provider=bitget`、产品线、K 线口径、信号模式、4h Hull 过滤、指标周期和风控参数，不再继承 `.env` 或全局 provider 默认值。临时命令行参数仍可覆盖 profile。

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
BINANCE_UM-FUTURES_BTCUSDT_300s_MARKET.csv
USDT-FUTURES_BTCUSDT_300s_MARKET.csv
```

缓存行为：

- 第一次运行：从所选 provider 的公共接口拉取并写入缓存。
- 后续运行：如果缓存完整覆盖请求区间，直接读取本地。
- 如果只缺左侧、右侧或中间缺口，只增量补齐缺失区间。
- `--no-cache` 可以临时绕过缓存强制在线请求。

命令：

```bash
./myvenv/bin/python run_backtest.py --profile btc_5m_range_cached --no-cache
```

临时指定 provider：

```bash
./myvenv/bin/python run_backtest.py --provider binance --symbol BTCUSDT --duration 300 --length 1000
./myvenv/bin/python run_backtest.py --provider bitget --symbol BTCUSDT --duration 300 --length 1000
```

当前回测支持 `provider=binance` 和 `provider=bitget`，默认 provider 是 `bitget`。天勤当前不接入回测。

未选择 profile 的临时回测和仓库内置命名 profile 当前都默认使用 Bitget，以继续复用既有历史缓存并保持报告口径。

## 可复现配置

命名 profile 除行情区间和资金参数外，还应固定以下信号配置：

```yaml
provider: bitget
product_type: USDT-FUTURES
kline_type: MARKET
signal_strategy: stc_extreme_contrarian
signal_mode: any
use_closed_bar: true
htf_hull_filter_enabled: true
htf_hull_duration_seconds: 14400
atr_period: 14
```

`htf_hull_duration_seconds` 控制高周期 Hull/STC 方向过滤。高周期 STC
只检查颜色方向，不检查数值；STC 极值条件只作用于低周期基础信号。

每次回测的 `report.json` 会额外记录：

- profile 名称及原始 profile 值。
- 最终生效的行情、信号、高周期和风控配置。
- 各指标实际解析后的默认参数。
- 实际 K 线起止时间和根数。
- Git commit、工作区是否有修改、相关策略源码 SHA-256、Python 和 pandas 版本。

`latest_month` 仍是滚动窗口，但报告中的 `resolved_data_window` 会固定记录本次实际数据范围。

## 撮合模型

默认撮合规则：

1. 复用当前实盘信号策略和高周期过滤。
2. 信号在目标 K 线收完后确认。
3. 下一根 K 线 `open` 开仓。
4. 持仓期间先检查回测风控出场。
5. 如果本根 K 线被风控平仓，本根不再重新开仓。
6. 未触发风控时，出现反向实盘信号则平旧仓并开新仓。
7. 回测结束时仍未平仓的最后一笔交易会被丢弃，不纳入统计。

验证 1D 主趋势、1H Hull/STC 同向重复开仓策略：

```bash
./myvenv/bin/python run_backtest.py \
  --profile btc_5m_range_cached \
  --strategy stc_1d_1h_reentry
```

该策略会自动加载 1D 主过滤 K 线和 1H 重入确认 K 线，不使用 profile 中
原有的 `htf_hull_duration_seconds` 覆盖它的固定周期。首次开仓不要求 1H
确认；同一 1D Hull 同色段的第 2 次及之后开仓要求已收完的 1H Hull 和
1H STC 颜色都同向，1H STC 不检查数值极值。

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

当前默认回测资金模型：

```text
总资金：20000U
单笔保证金：固定 1000U
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

矩阵回测用于批量扫描风控参数。

CLI 参数矩阵会复用一次预计算的指标快照；输出文件和排序口径保持兼容。

先检查组合数：

```bash
./myvenv/bin/python chartbench.py backtest matrix --matrix btc_risk_matrix --plan
./myvenv/bin/python chartbench.py backtest matrix --matrix sol_risk_matrix --plan
```

旧入口的 `--dry-run` 继续作为 `--plan` 的兼容别名。

正式运行：

```bash
./myvenv/bin/python chartbench.py backtest matrix --matrix btc_risk_matrix
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
