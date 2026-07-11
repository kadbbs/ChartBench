# 统一命令行

推荐使用统一入口：

```bash
./myvenv/bin/python chartbench.py --help
```

旧入口 `web_tq_chart.py`、`run_live_trading.py`、`run_backtest.py` 和
`run_backtest_matrix.py` 继续兼容；统一入口调用同一套执行函数，不复制行情、
交易或回测逻辑。

## 图表

```bash
./myvenv/bin/python chartbench.py chart run
./myvenv/bin/python chartbench.py chart run \
  --provider tianqin \
  --symbol KQ.m@SHFE.cu \
  --duration-seconds 300 \
  --data-length 800
```

## 实盘与观察

```bash
./myvenv/bin/python chartbench.py live once --profile email
./myvenv/bin/python chartbench.py live run --profile dry_run_5u
./myvenv/bin/python chartbench.py live preflight --profile live_5u
./myvenv/bin/python chartbench.py live run --profile live_5u
```

`once` 对应旧入口的单次判断，`run` 对应 `--continuous`，`preflight`
对应 `--preflight`。

## 回测

```bash
./myvenv/bin/python chartbench.py backtest run --profile btc_5m_range_cached
./myvenv/bin/python chartbench.py backtest run \
  --profile btc_5m_range_cached \
  --strategy stc_1d_1h_reentry
./myvenv/bin/python chartbench.py backtest matrix --matrix btc_risk_matrix --plan
./myvenv/bin/python chartbench.py backtest matrix --matrix btc_risk_matrix
```

单次与矩阵回测共用 `tq_app.backtesting.runtime.prepare_backtest_market`，
主高周期和重入确认周期均由策略注册信息决定。`--plan` 只输出矩阵组合；
旧参数 `--dry-run` 仍可使用。

## 查询与配置检查

```bash
./myvenv/bin/python chartbench.py list strategies
./myvenv/bin/python chartbench.py list providers
./myvenv/bin/python chartbench.py list profiles --scope live
./myvenv/bin/python chartbench.py list profiles --scope backtest
./myvenv/bin/python chartbench.py list profiles --scope matrix

./myvenv/bin/python chartbench.py config show --scope chart --explain
./myvenv/bin/python chartbench.py config show --scope live --profile live_5u --explain
./myvenv/bin/python chartbench.py config validate --scope live --profile live_5u
./myvenv/bin/python chartbench.py config validate --scope backtest --profile btc_5m_range_cached
```

`config show --explain` 会显示每个字段的来源，并隐藏密钥、Secret、密码、
Passphrase 和 Token。`config validate` 只读取配置与注册表，不启动行情或连接
交易所。

## 参数规范

公共参数为：

```text
--provider
--symbol
--duration-seconds
--data-length
```

旧名称继续作为别名：

```text
--duration  -> --duration-seconds
--length    -> --data-length
--dry-run   -> matrix --plan
```

布尔参数使用成对形式，例如回测缓存：

```text
--cache
--no-cache
```

配置优先级统一为：代码默认值、`config/defaults.yaml`、`.env`、显式模块
profile、进程环境变量、命令行参数。`.env` 应只保存密钥和账号等敏感值，
运行模式、provider、策略和风控参数应写入 defaults 或对应 profile。
