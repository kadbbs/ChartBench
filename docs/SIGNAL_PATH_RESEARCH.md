# 信号路径研究与参数热力图

这个工作流解决一个特定问题：先保留策略原始的开仓到反向信号路径，不让
止损、止盈或重复开仓提前改变样本；分析完原始路径后，再快速重放候选规则，
寻找连续而稳定的盈利参数区域。

## 固定信号口径

内置 profile：

```text
config/backtests/btc_5m_signal_path.yaml
```

固定语义：

1. 低周期固定为 5m。
2. 只使用闭合 K 线。
3. 已闭合的 1D Hull 与 1D STC 必须同向。
4. 5m 信号在收线后确认，下一根 5m 开盘执行。
5. 日线方向不匹配的反向 5m 信号被忽略。
6. 基准持仓直到符合新日线方向的有效反向 5m 信号。
7. 基准不使用止损、止盈、保本、移动保护或时间退出。
8. 持仓内的同向有效信号只记录，不改变仓位。

STC 图表线段颜色和策略方向已经分离。策略使用当前 STC 与上一根 STC
比较得到的因果方向，不读取依赖下一根数据的显示颜色。
该 profile 同时固定 Hull、UT 和 STC 的全部信号参数；最终解析值也会写入
`manifest.json`。

## UI 工作流

启动：

```bash
./myvenv/bin/python chartbench.py chart run --backtest-ui --host 127.0.0.1
```

打开：

```text
http://127.0.0.1:8050/backtests
```

页面默认选择“信号路径与热力图”。

### 第一步：生成无风控样本

1. 本次操作选择“生成无风控样本”。
2. 选择 `btc_5m_signal_path`。
3. 设置时间范围和入场前上下文，默认 288 根，即 24 小时。
4. 点击“生成基准样本”。
5. 任务完成后从结果底部下载数据。

导出文件：

```text
manifest.json                    数据身份、执行语义、切分边界
episodes.csv                     开仓到有效反向信号的交易路径
signals.csv                      入场、同向、反向有效信号账本
bars.csv.gz                      唯一 5m OHLCV、ATR、STC、Hull 特征
llm_research_samples.jsonl.gz    仅包含完整落在研究段的模型样本
README.txt                       文件说明
```

`llm_research_samples.jsonl.gz` 不会包含验证段或最终测试段。研究段内开仓、
但到验证段才平仓的跨边界 Episode 也不会导出，避免结果路径泄漏。

### 第二步：分析样本

模型输入把数据分成：

```text
context_bars       入场前可见序列
holding_path_bars  入场后到反向信号的结果路径
episode            开平节点、MFE、MAE、日线趋势段和结果标签
same_side_signals  持仓内被记录但未执行的同向信号
```

模型适合提出：

- 失败路径分类；
- MAE 后恢复的时间特征；
- MFE 与持仓时长分布；
- 哪类同向信号可能适合再入场；
- 候选止损、止盈或延迟退出规则。

模型结论必须转换成确定的数值规则后再回测，不能让回测读取自由文本判断。

### 第三步：运行矩阵

1. 本次操作切换为“运行止损止盈矩阵”。
2. “复用基准样本”会自动选择最近完成的基准任务。
3. 选择 ATR、百分比或价格点数。
4. 输入止损 X 轴和止盈 Y 轴。
5. 保持“揭盲最终测试段”关闭。
6. 运行矩阵。

范围格式：

```text
0.5:3:0.25
1,1.5,2,3,4
```

百分比单位直接填写百分数，例如 `0.5` 表示距离入场价 `0.5%`。

矩阵不会重新计算每一组指标。使用已有基准任务时，只加载压缩路径并执行
参数重放；没有选择基准任务时，当前任务也只计算一次指标和信号。

同一根 5m 同时触发止损和止盈时，默认使用 `stop_first`，即止损优先。
页面会显示双触发 K 线数量，候选参数应进一步使用更低周期数据验证。

## 热力图含义

热力图的每一格严格对应唯一一组：

```text
X = stop_loss
Y = take_profit
```

不会从其他隐藏参数中挑选最高分填入格子。其他参数，例如同向再入场次数、
冷却时间和同 K 线规则，都是本次矩阵的固定切片。

可以切换显示：

- 验证段收益；
- 研究段收益；
- 总收益；
- 最大回撤；
- 盈利因子；
- 3×3 邻域盈利比例。

候选详情同时显示当前可见区间的逐年收益和正收益年度比例，避免只看累计值。

稳定格要求：

```text
当前格验证收益 > 0
验证段至少 5 笔交易
当前格拥有完整 3×3 邻域（边界格不判定为稳定中心）
3×3 邻域至少 80% 盈利
邻域最差验证收益 > 0
```

相邻稳定格会被合并成连续区域。推荐候选取最大稳定区域的中心，而不是单格
最高收益。

## 数据切分与揭盲

时间顺序固定为：

```text
研究段 60%
验证段 20%
最终测试段 20%
```

研究段和验证段参与候选选择，最终测试段不参与排序。只有参数和实现已经锁定
后才勾选“揭盲最终测试段”。如果根据测试结果继续调参，测试段就已经变成新的
验证段，需要向后保留新的未见数据。

## CLI

生成基准：

```bash
./myvenv/bin/python chartbench.py backtest baseline \
  --profile btc_5m_signal_path \
  --context-bars 288
```

直接重新计算并运行矩阵：

```bash
./myvenv/bin/python chartbench.py backtest heatmap \
  --profile btc_5m_signal_path \
  --stop-unit atr \
  --stop-values 0.5:3:0.25 \
  --take-values 0.75:6:0.25
```

复用已有 CLI 或 UI 数据集：

```bash
./myvenv/bin/python chartbench.py backtest heatmap \
  --profile btc_5m_signal_path \
  --baseline-dataset backtest_outputs/ui/RUN_ID/dataset/internal.json.gz \
  --stop-values 0.5:3:0.25 \
  --take-values 0.75:6:0.25
```

同向再入场切片：

```bash
./myvenv/bin/python chartbench.py backtest heatmap \
  --profile btc_5m_signal_path \
  --baseline-dataset PATH_TO_INTERNAL_JSON_GZ \
  --max-reentries 2 \
  --reentry-cooldown-bars 12
```

当前再入场定义为：一次止损或止盈导致空仓后，等待下一次记录到的同向有效
信号重新开仓；持仓中不会金字塔加仓。
